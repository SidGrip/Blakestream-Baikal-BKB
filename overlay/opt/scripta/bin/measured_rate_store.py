#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bounded personal measured-rate store.

Python 2.7 and 3. The synchronized reducer owns sampling; this module only
stores compact hourly aggregates. It never reads miner APIs, scripta.conf, or
unrelated logs.

Files (nothing else in these directories is created or removed):

- ``{sd_dir}/measured-rates-control.json``
  Durable pointer: generation, target (``SD`` or ``RAM``), board installation
  ids, and the private HMAC salt. Rewritten only when the dataset is created,
  cleared, migrated, or a board is reset. Never on a refresh.
- ``{ram_dir}/measured-rates.lock``
  Exclusive writer/migration lock.
- ``{ram_dir}/measured-rates-state-<generation>.json``
  RAM-only runtime plus the not-yet-committed intervals, one atomic snapshot.
  Lost on reboot. An empty runtime after that loss is expected; old logs are
  not replayed.
- ``{sd_dir or ram_dir}/measured-rates-<generation>.sqlite``
  Active aggregates for the generation named by the control file. The other
  directory is not a second current copy. Selection is the control pointer,
  never mtime.

RAM is the default target. The selected target is stored in the control file
and survives reboot; RAM measurements themselves do not. A failed copy or a
failed control publish leaves the previous pointer and database in place.
Migration checks the copied rows, including pending intervals, before retention
or the byte cap removes anything. Superseded generation files are removed only
after the new pointer is durable.
Clear advances the generation before deleting the old files, so a crash cannot
make the old pending snapshot current again.

Commits carry a sequence watermark in the same SQLite transaction as the bucket
updates. The RAM snapshot is trimmed only after that commit. Replaying a
snapshot whose sequences are already watermarked does not add the work twice.

Storage budget, counted together:

- Main database target ``MAX_DB_BYTES`` (2 MiB). Hourly buckets, at most 64
  contexts, seven days. Expired hours and then oldest closed buckets or
  inactive contexts are removed with their time and work together. Control
  rows are never deleted to satisfy the cap.
- SQLite ``journal_mode=DELETE`` and ``synchronous=FULL``. The rollback
  journal exists only during a commit. A normal flush dirties a few 4 KiB
  pages (typically well under 64 KiB) and the journal is removed when the
  commit finishes. The worst case for one transaction is a journal about as
  large as the database, so plan for a transient peak near 4 MiB (2 MiB
  database + 2 MiB journal) only if a commit rewrites every page. Steady
  state is the database file alone.
- ``auto_vacuum=INCREMENTAL``. Free pages are reclaimed when the file is over
  the cap, not on every five-minute flush. That reclaim is the compact
  checkpoint; the history is not rewritten on each refresh.
- RAM snapshot: at most 128 pending intervals and 256 KiB of intervals, plus
  at most 256 KiB of runtime JSON. The whole snapshot stays under 576 KiB.
- New eligible intervals that do not fit are discarded, counted in ``dropped``,
  and omitted from the aggregates. They are not reported as saved.

``tick`` keeps the RAM snapshot if the database commit fails and returns
``ok`` false. Corrupt or missing durable data raises ``StoreError`` instead of
starting an empty dataset. A missing RAM database after reboot is not
corruption: the same generation continues empty.
"""

from __future__ import division, print_function

import binascii
import errno
import fcntl
import json
import math
import os
import re
import shutil
import sqlite3
import time

try:
    _TEXT = (str, unicode)  # noqa: F821
except NameError:
    _TEXT = (str,)
try:
    _INTS = (int, long)  # noqa: F821
except NameError:
    _INTS = (int,)

DEFAULT_TARGET = 'RAM'
FLUSH_SECONDS = 300
RETENTION_SECONDS = 7 * 24 * 3600
MAX_CONTEXTS = 64
MAX_DB_BYTES = 2 * 1024 * 1024
MAX_PENDING_INTERVALS = 128
MAX_PENDING_BYTES = 256 * 1024
MAX_RUNTIME_BYTES = 256 * 1024
MAX_CONTEXT_BYTES = 8192
MAX_INTERVAL_SECONDS = 120
_MAX_JSON_DEPTH = 8
_MAX_COLLECTION = 64
_MAX_KEY = 64
_MAX_STRING = 1024
_FORBIDDEN_KEYS = set([
    'password', 'passwd', 'secret', 'token', 'private_key', 'worker', 'username',
])
_HEX32 = re.compile(r'^[0-9a-f]{32}$')
_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_DB_RE = re.compile(r'^measured-rates-([0-9a-f]{32})\.sqlite$')
_SIDE_RE = re.compile(r'^measured-rates-([0-9a-f]{32})\.sqlite-(journal|wal|shm)$')
_STATE_RE = re.compile(r'^measured-rates-state-([0-9a-f]{32})\.json$')
_TMP_RE = re.compile(
    r'^measured-rates-(?:control\.json|state-[0-9a-f]{32}\.json)\.tmp$')

# Test hook. Production leaves this None. A callable receives a step name and
# may raise IOError or StoreError. It is not part of the Store constructor.
_fault = None

_SCHEMA = (
    'CREATE TABLE meta ('
    ' key TEXT PRIMARY KEY NOT NULL,'
    ' value TEXT NOT NULL)',
    'CREATE TABLE contexts ('
    ' context_id TEXT PRIMARY KEY NOT NULL,'
    ' board INTEGER NOT NULL,'
    ' installation TEXT NOT NULL,'
    ' context_json TEXT NOT NULL,'
    ' first_epoch REAL NOT NULL,'
    ' last_epoch REAL NOT NULL)',
    'CREATE TABLE buckets ('
    ' context_id TEXT NOT NULL,'
    ' hour INTEGER NOT NULL,'
    ' seconds REAL NOT NULL,'
    ' difficulty REAL NOT NULL,'
    ' accepted INTEGER NOT NULL,'
    ' rejected INTEGER NOT NULL,'
    ' hw INTEGER NOT NULL,'
    ' temp_sum REAL NOT NULL,'
    ' temp_min REAL NOT NULL,'
    ' temp_max REAL NOT NULL,'
    ' fan_sum REAL NOT NULL,'
    ' fixed32 INTEGER NOT NULL,'
    ' first_epoch REAL NOT NULL,'
    ' last_epoch REAL NOT NULL,'
    ' PRIMARY KEY (context_id, hour),'
    ' FOREIGN KEY (context_id) REFERENCES contexts(context_id)'
    ' ON DELETE CASCADE)',
    'CREATE INDEX idx_buckets_hour ON buckets(hour)',
    'CREATE INDEX idx_contexts_board ON contexts(board, installation)',
)


class StoreError(Exception):
    """Safe, actionable measured-rate storage failure."""


class Store(object):
    """Personal measured-rate aggregates. See the module docstring."""

    def __init__(self, ram_dir='/run/blakestream', sd_dir='/opt/scripta/var'):
        self.ram_dir = ram_dir
        self.sd_dir = sd_dir

    def tick(self, callback, now, force=False):
        now = _require_now(now)
        data = None
        try:
            with _exclusive(self.ram_dir, self.sd_dir):
                data = self._load()
                try:
                    meta = _callback_meta(data)
                    runtime = data.runtime
                    try:
                        produced = callback(meta, runtime)
                    except StoreError:
                        raise
                    except Exception:
                        # The callback's own error must not be rewritten as a
                        # successful empty save. Runtime mutations stay in
                        # memory only; the snapshot on disk is unchanged.
                        raise
                    if not isinstance(produced, list):
                        raise StoreError(
                            'measured-rate callback must return a list of intervals')
                    intervals = [
                        _validate_interval(item, data.board_ids) for item in produced]
                    _runtime_blob(runtime)
                    _enqueue(data, intervals, now)
                    _write_state(self.ram_dir, data)
                    if _should_flush(data, now, force):
                        try:
                            _flush(data, now)
                        except StoreError as exc:
                            data.io_error = str(exc)
                            try:
                                _write_state(self.ram_dir, data)
                            except StoreError:
                                pass
                        else:
                            data.io_error = ''
                            try:
                                _write_state(self.ram_dir, data)
                            except StoreError:
                                # The commit is already durable. The next open
                                # drops watermarked sequences without adding them.
                                pass
                    return _status(data, now)
                finally:
                    data.close()
        except StoreError:
            raise
        except (IOError, OSError) as exc:
            raise StoreError(_io_text('save the measured-rate snapshot', exc))

    def status(self, now=None):
        now = _optional_now(now)
        with _exclusive(self.ram_dir, self.sd_dir):
            data = self._load()
            try:
                return _status(data, now)
            finally:
                data.close()

    def set_target(self, target, now=None):
        if target not in ('SD', 'RAM'):
            raise StoreError("measured-rate target must be 'SD' or 'RAM'")
        now = _optional_now(now)
        with _exclusive(self.ram_dir, self.sd_dir):
            data = self._load()
            destination = None
            try:
                if data.target == target:
                    return _status(data, now)
                destination = _db_path(_dir_for(self, target), data.generation)
                source_totals = _totals(data.conn)
                pending = _uncommitted(data)
                expected = _add_totals(source_totals, _interval_totals(pending))
                expected_seq = pending[-1]['seq'] if pending else data.sequence
                source_path = data.db_path
                data.close()
                try:
                    _copy_database(source_path, destination)
                    copied = _connect_existing(destination)
                    try:
                        _check_fault('db-verify')
                        # Exact logical copy first. Retention below may remove
                        # expired rows and must not be what verification sees.
                        _begin(copied)
                        try:
                            if _get_meta(copied, 'generation') != data.generation:
                                raise StoreError(
                                    'measured-rate copy does not match the active '
                                    'generation; the original dataset was left unchanged')
                            protected = set()
                            for item in pending:
                                _apply_interval(copied, item['interval'], protected)
                            if pending:
                                _put_meta(copied, 'sequence', str(expected_seq))
                                _put_meta(copied, 'dropped', str(data.dropped))
                            _verify_copy(
                                copied, data.generation, expected_seq, expected, True)
                            if not _enforce_caps(copied, now, protected):
                                raise StoreError(
                                    'measured-rate data does not fit in the destination; '
                                    'the original dataset and target were left unchanged')
                            _sync_bounds(copied)
                            _put_meta(copied, 'last_saved', _format_num(now))
                            _commit(copied)
                        except StoreError:
                            _rollback(copied)
                            raise
                        except sqlite3.Error as exc:
                            _rollback(copied)
                            raise _db_error(exc)
                    finally:
                        copied.close()
                    control = _control_dict(data)
                    control['target'] = target
                    _publish_control(self.sd_dir, control)
                    _remove_db(source_path)
                    data.target = target
                    data.db_path = destination
                    data.conn = _connect_existing(destination)
                    data.sequence = expected_seq
                    data.last_saved = now
                    _drop_committed(data)
                    try:
                        _write_state(self.ram_dir, data)
                    except StoreError:
                        # The destination is already published. The next open
                        # drops watermarked sequences instead of counting them twice.
                        pass
                    _cleanup_others(self.ram_dir, self.sd_dir, data.generation, data.target)
                    return _status(data, now)
                except StoreError:
                    # A directory fsync can fail after control was renamed.
                    # Keep both copies until a later open durably resolves the
                    # pointer; removing destination here could erase live data.
                    if data.conn is None and os.path.isfile(source_path):
                        data.db_path = source_path
                        data.conn = _connect_existing(source_path)
                    raise
            finally:
                data.close()

    def clear(self, now=None):
        now = _optional_now(now)
        with _exclusive(self.ram_dir, self.sd_dir):
            control, data = self._control_for_recovery()
            try:
                if control is None:
                    created = self._create_new(DEFAULT_TARGET, None)
                    try:
                        return _status(created, now)
                    finally:
                        created.close()
                if data is not None:
                    data.close()
                    data = None
                return self._replace_generation(control, now)
            finally:
                if data is not None:
                    data.close()

    def reset_board(self, board, now=None):
        board = _validate_board(board)
        now = _optional_now(now)
        with _exclusive(self.ram_dir, self.sd_dir):
            data = self._load()
            try:
                new_id = _opaque(16)
                updated = dict(data.board_ids)
                updated[str(board)] = new_id
                _publish_control(self.sd_dir, _control_dict(data, board_ids=updated))
                data.board_ids = updated
                # Crash window: the new installation id is durable, the RAM
                # snapshot still has this board's old pending intervals.
                _check_fault('reset-after-publish')
                _delete_board_rows(data.conn, board)
                _put_meta(data.conn, 'last_saved', _format_num(now))
                _commit(data.conn)
                data.last_saved = now
                _forget_board_runtime(data, board)
                data.intervals = [
                    item for item in data.intervals
                    if int(item['interval']['context']['board']) != board]
                if not _uncommitted(data):
                    data.pending_since = None
                try:
                    _write_state(self.ram_dir, data)
                except StoreError:
                    # Installation id and row removal are already durable.
                    # Reload discards this board's stale pending intervals.
                    pass
                return _status(data, now)
            finally:
                data.close()

    def _load(self):
        _ensure_dir(self.ram_dir)
        _ensure_dir(self.sd_dir)
        control = _read_control(self.sd_dir)
        if control is None:
            return self._create_new(DEFAULT_TARGET, None)
        return self._open_control(control)

    def _control_for_recovery(self):
        """Return ``(control, open data or None)`` without requiring a healthy DB."""
        _ensure_dir(self.ram_dir)
        _ensure_dir(self.sd_dir)
        control = _read_control(self.sd_dir)
        if control is None:
            return None, None
        try:
            return control, self._open_control(control)
        except StoreError:
            return control, None

    def _create_new(self, target, salt):
        generation = _opaque(16)
        board_ids = dict((str(index), _opaque(16)) for index in range(3))
        if salt is None:
            salt = _opaque(32)
        path = _db_path(_dir_for(self, target), generation)
        conn = _create_db(path, generation)
        data = _blank(generation, target, board_ids, salt, path, conn)
        try:
            _publish_control(self.sd_dir, _control_dict(data))
            _write_state(self.ram_dir, data)
            _cleanup_others(self.ram_dir, self.sd_dir, generation, target)
            return data
        except Exception:
            data.close()
            raise

    def _open_control(self, control):
        # Finish any interrupted pointer publication before retiring copies or
        # applying a board reset recovered from that pointer.
        try:
            _sync_dir(self.sd_dir)
        except (IOError, OSError) as exc:
            raise StoreError(_io_text('confirm the measured-rate control pointer', exc))
        generation = control['generation']
        target = control['target']
        path = _db_path(_dir_for(self, target), generation)
        if not os.path.isfile(path):
            if target != 'RAM':
                raise StoreError(
                    'measured-rate database is missing; refusing to start an '
                    'empty replacement for the SD dataset')
            conn = _create_db(path, generation)
        else:
            conn = _open_existing(path, generation)
        try:
            _cleanup_others(self.ram_dir, self.sd_dir, generation, target)
            _purge_foreign_contexts(conn, control['board_ids'])
            data = _blank(
                generation, target, control['board_ids'], control['salt'], path, conn)
            _load_db_meta(data)
            discarded = _load_state(self.ram_dir, data)
            scrubbed = _scrub_runtime(data)
            changed = _drop_committed(data)
            if discarded or scrubbed or _reconcile_dropped(data):
                changed = True
            if changed:
                try:
                    _write_state(self.ram_dir, data)
                except StoreError:
                    pass
            return data
        except Exception:
            conn.close()
            raise

    def _replace_generation(self, control, now):
        old_generation = control['generation']
        target = control['target']
        salt = control['salt']
        generation = _opaque(16)
        board_ids = dict((str(index), _opaque(16)) for index in range(3))
        path = _db_path(_dir_for(self, target), generation)
        conn = _create_db(path, generation)
        data = _blank(generation, target, board_ids, salt, path, conn)
        try:
            _publish_control(self.sd_dir, _control_dict(data))
            # The new pointer is durable. A crash from here on must not load the
            # previous generation's database or pending snapshot.
            _check_fault('clear-after-publish')
            _remove_generation_files(self.ram_dir, self.sd_dir, old_generation)
            try:
                _write_state(self.ram_dir, data)
            except StoreError:
                pass
            _cleanup_others(self.ram_dir, self.sd_dir, generation, target)
            return _status(data, now)
        except StoreError:
            # Publication may already be visible even if its fsync failed.
            # The next successful open can safely remove an unused generation.
            raise
        finally:
            data.close()


class _Data(object):
    def __init__(self):
        self.generation = ''
        self.target = DEFAULT_TARGET
        self.board_ids = {}
        self.salt = ''
        self.sequence = 0
        self.last_saved = None
        self.dropped_db = 0
        self.next_seq = 1
        self.dropped = 0
        self.pending_since = None
        self.io_error = ''
        self.intervals = []
        self.runtime = {}
        self.conn = None
        self.db_path = ''

    def close(self):
        conn = self.conn
        self.conn = None
        if conn is not None:
            conn.close()


class _Lock(object):
    def __init__(self, ram_dir, sd_dir):
        self.ram_dir = ram_dir
        self.sd_dir = sd_dir
        self.handle = None

    def __enter__(self):
        _ensure_dir(self.ram_dir)
        _ensure_dir(self.sd_dir)
        path = os.path.join(self.ram_dir, 'measured-rates.lock')
        self.handle = open(path, 'a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except (IOError, OSError) as exc:
            self.handle.close()
            self.handle = None
            raise StoreError(_io_text('lock the measured-rate writer', exc))
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except (IOError, OSError):
                pass
            self.handle.close()
            self.handle = None
        return False


def _exclusive(ram_dir, sd_dir):
    return _Lock(ram_dir, sd_dir)


def _blank(generation, target, board_ids, salt, path, conn):
    data = _Data()
    data.generation = generation
    data.target = target
    data.board_ids = dict(board_ids)
    data.salt = salt
    data.db_path = path
    data.conn = conn
    data.runtime = {}
    return data


def _dir_for(store, target):
    return store.ram_dir if target == 'RAM' else store.sd_dir


def _db_path(directory, generation):
    if not _HEX32.match(generation):
        raise StoreError('measured-rate generation is not valid')
    return os.path.join(directory, 'measured-rates-%s.sqlite' % generation)


def _state_path(ram_dir, generation):
    return os.path.join(ram_dir, 'measured-rates-state-%s.json' % generation)


def _control_path(sd_dir):
    return os.path.join(sd_dir, 'measured-rates-control.json')


def _opaque(nbytes):
    raw = binascii.hexlify(os.urandom(nbytes))
    if not isinstance(raw, _TEXT):
        raw = raw.decode('ascii')
    return raw


def _is_binary(value):
    """True only for Python 3 bytes. Python 2 aliases bytes to str."""
    return isinstance(value, bytes) and not isinstance(value, str)


def _text_keep(value):
    """Normalize text to a native str (Python 2 byte string, Python 3 text)."""
    if _is_binary(value):
        try:
            value = value.decode('utf-8')
        except Exception:
            raise StoreError('measured-rate text field is not valid')
    if not isinstance(value, _TEXT):
        raise StoreError('measured-rate text field is not valid')
    if isinstance(value, str):
        return value
    try:
        return value.encode('utf-8')
    except Exception:
        raise StoreError('measured-rate text field is not valid')


def _is_int(value):
    return isinstance(value, _INTS) and not isinstance(value, bool)


def _require_now(now):
    if now is None:
        raise StoreError('measured-rate tick requires an epoch timestamp')
    return _finite(now, 'timestamp', minimum=0)


def _optional_now(now):
    if now is None:
        return time.time()
    return _finite(now, 'timestamp', minimum=0)


def _finite(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, _INTS + (float,)):
        raise StoreError('measured-rate %s must be a finite number' % label)
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise StoreError('measured-rate %s must be a finite number' % label)
    if minimum is not None and number < minimum:
        raise StoreError('measured-rate %s is out of range' % label)
    if maximum is not None and number > maximum:
        raise StoreError('measured-rate %s is out of range' % label)
    return number


def _nonneg_int(value, label):
    if not _is_int(value) or value < 0 or value > 9007199254740991:
        raise StoreError('measured-rate %s must be a nonnegative integer' % label)
    return int(value)


def _validate_board(board):
    if not _is_int(board) or board < 0 or board > 2:
        raise StoreError('measured-rate board must be an integer from 0 to 2')
    return int(board)


def _num(value):
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise StoreError('measured-rate aggregate is not finite')
    rounded = round(number)
    if abs(number - rounded) <= 1e-9 and abs(rounded) < 9007199254740992:
        return int(rounded)
    return number


def _format_num(value):
    number = _num(value)
    return str(number)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _clean_json(value, path, depth):
    if depth > _MAX_JSON_DEPTH:
        raise StoreError('measured-rate context %s is nested too deeply' % path)
    if value is None or isinstance(value, bool):
        return value
    if _is_int(value):
        if abs(int(value)) > 9007199254740991:
            raise StoreError('measured-rate context %s is out of range' % path)
        return int(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise StoreError('measured-rate context %s must be finite' % path)
        return value
    if _is_binary(value) or isinstance(value, _TEXT):
        if _is_binary(value):
            try:
                text = value.decode('utf-8')
            except Exception:
                raise StoreError('measured-rate context %s is not valid text' % path)
        else:
            text = value
        if len(text) > _MAX_STRING:
            raise StoreError('measured-rate context %s is too long' % path)
        return text
    if isinstance(value, list):
        if len(value) > _MAX_COLLECTION:
            raise StoreError('measured-rate context %s has too many items' % path)
        return [
            _clean_json(item, '%s[%d]' % (path, index), depth + 1)
            for index, item in enumerate(value)]
    if isinstance(value, dict):
        if len(value) > _MAX_COLLECTION:
            raise StoreError('measured-rate context %s has too many fields' % path)
        cleaned = {}
        for key in value:
            if _is_binary(key):
                try:
                    name = key.decode('utf-8')
                except Exception:
                    raise StoreError(
                        'measured-rate context %s has a non-text field' % path)
            elif isinstance(key, _TEXT):
                name = key
            else:
                raise StoreError('measured-rate context %s has a non-text field' % path)
            if len(name) > _MAX_KEY:
                raise StoreError('measured-rate context field name is too long')
            if name.lower() in _FORBIDDEN_KEYS:
                raise StoreError(
                    'measured-rate context field %s is not allowed in stored history'
                    % (path + '.' + name if path else name))
            child = name if not path else path + '.' + name
            cleaned[name] = _clean_json(value[key], child, depth + 1)
        return cleaned
    raise StoreError('measured-rate context %s is not a supported value' % path)


def _validate_context(context, board_ids):
    if not isinstance(context, dict):
        raise StoreError('measured-rate context must be an object')
    cleaned = _clean_json(context, 'context', 0)
    board = cleaned.get('board')
    if not _is_int(board) or int(board) not in (0, 1, 2):
        raise StoreError('measured-rate context board must be an integer from 0 to 2')
    board = int(board)
    cleaned['board'] = board
    installation = cleaned.get('installation')
    if not (_is_binary(installation) or isinstance(installation, _TEXT)):
        raise StoreError('measured-rate context installation is invalid')
    try:
        installation = _text_keep(installation)
    except StoreError:
        raise StoreError('measured-rate context installation is invalid')
    if not installation or len(installation) > 128:
        raise StoreError('measured-rate context installation is invalid')
    cleaned['installation'] = installation
    if installation != _text_keep(board_ids[str(board)]):
        raise StoreError(
            'measured-rate interval does not match this board installation; '
            'it was not stored')
    encoded = _canonical(cleaned)
    if len(encoded) > MAX_CONTEXT_BYTES:
        raise StoreError(
            'measured-rate context exceeds %d bytes' % MAX_CONTEXT_BYTES)
    return cleaned


def _validate_interval(interval, board_ids):
    if not isinstance(interval, dict):
        raise StoreError('measured-rate interval must be an object')
    required = (
        'context_id', 'context', 'end', 'seconds', 'difficulty', 'accepted',
        'rejected', 'hw', 'temp_sum', 'temp_min', 'temp_max', 'fan_sum', 'fixed32')
    for key in required:
        if key not in interval:
            raise StoreError('measured-rate interval is missing %s' % key)
    context_id = interval['context_id']
    if not (_is_binary(context_id) or isinstance(context_id, _TEXT)):
        raise StoreError(
            'measured-rate context_id must be a 64-character sha256 hex string')
    try:
        context_id = _text_keep(context_id)
    except StoreError:
        raise StoreError(
            'measured-rate context_id must be a 64-character sha256 hex string')
    if not _HEX64.match(context_id):
        raise StoreError(
            'measured-rate context_id must be a 64-character sha256 hex string')
    context = _validate_context(interval['context'], board_ids)
    end = _finite(interval['end'], 'interval end', minimum=0)
    seconds = _finite(interval['seconds'], 'interval seconds')
    if not (0 < seconds <= MAX_INTERVAL_SECONDS):
        raise StoreError(
            'measured-rate interval seconds must be greater than 0 and at most 120')
    difficulty = _finite(interval['difficulty'], 'difficulty', minimum=0)
    accepted = _nonneg_int(interval['accepted'], 'accepted')
    rejected = _nonneg_int(interval['rejected'], 'rejected')
    hw = _nonneg_int(interval['hw'], 'hw')
    temp_sum = _finite(interval['temp_sum'], 'temp_sum')
    temp_min = _finite(interval['temp_min'], 'temp_min')
    temp_max = _finite(interval['temp_max'], 'temp_max')
    if temp_min > temp_max:
        raise StoreError('measured-rate temp_min exceeds temp_max')
    fan_sum = _finite(interval['fan_sum'], 'fan_sum', minimum=0)
    if not isinstance(interval['fixed32'], bool):
        raise StoreError('measured-rate fixed32 must be a boolean')
    return {
        'context_id': context_id,
        'context': context,
        'end': end,
        'seconds': seconds,
        'difficulty': difficulty,
        'accepted': accepted,
        'rejected': rejected,
        'hw': hw,
        'temp_sum': temp_sum,
        'temp_min': temp_min,
        'temp_max': temp_max,
        'fan_sum': fan_sum,
        'fixed32': interval['fixed32'],
    }


def _runtime_blob(runtime):
    if not isinstance(runtime, dict):
        raise StoreError(
            'measured-rate runtime state must be an object; it was not written')
    try:
        raw = _canonical(runtime)
    except (TypeError, ValueError):
        raise StoreError(
            'measured-rate runtime state must contain only finite JSON values; '
            'it was not written')
    if len(raw) > MAX_RUNTIME_BYTES:
        raise StoreError(
            'measured-rate runtime state exceeds %d bytes; it was not written'
            % MAX_RUNTIME_BYTES)
    return raw


def _check_fault(step):
    fault = _fault
    if fault is None:
        return
    try:
        fault(step)
    except StoreError:
        raise
    except (IOError, OSError) as exc:
        raise StoreError(_io_text(step, exc))


def _io_text(action, exc):
    err = getattr(exc, 'errno', None)
    if err == errno.ENOSPC:
        return (
            'measured-rate data could not be saved because the disk is full; '
            'unsaved samples remain in the RAM buffer and the last good data was kept')
    if err in (errno.EROFS, errno.EACCES, errno.EPERM):
        return (
            'measured-rate data could not be saved because storage is not writable; '
            'unsaved samples remain in the RAM buffer and the last good data was kept')
    return (
        'measured-rate data could not be saved while trying to %s; unsaved samples '
        'remain in the RAM buffer and the last good data was kept' % action)


def _db_error(exc):
    text = str(exc).lower()
    if 'full' in text or 'disk' in text or 'read-only' in text or 'readonly' in text or 'i/o' in text:
        return StoreError(
            'measured-rate data could not be saved because storage is full or not '
            'writable; unsaved samples remain in the RAM buffer and the last good '
            'data was kept')
    if 'malformed' in text or 'not a database' in text or 'corrupt' in text:
        return StoreError(
            'measured-rate database is corrupt or incomplete; refusing to replace '
            'it with an empty store')
    return StoreError(
        'measured-rate database could not be updated; the last good data was kept '
        'and unsaved samples remain in the RAM buffer')


def _ensure_dir(path):
    if os.path.isdir(path):
        return
    try:
        os.makedirs(path)
    except (IOError, OSError) as exc:
        if not os.path.isdir(path):
            raise StoreError(_io_text('create ' + os.path.basename(path), exc))
    try:
        os.chmod(path, 0o775)
    except (IOError, OSError):
        pass


def _chown_www(path):
    try:
        import grp
        gid = grp.getgrnam('www-data').gr_gid
        os.chown(path, -1, gid)
    except Exception:
        pass


def _sync_file(path):
    handle = open(path, 'rb')
    try:
        os.fsync(handle.fileno())
    finally:
        handle.close()


def _sync_dir(path):
    handle = os.open(path, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _atomic_bytes(path, payload, fault_step):
    parent = os.path.dirname(path)
    _ensure_dir(parent)
    tmp = path + '.tmp'
    handle = open(tmp, 'wb')
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    except (IOError, OSError) as exc:
        handle.close()
        _remove_file(tmp)
        raise StoreError(_io_text(fault_step, exc))
    handle.close()
    try:
        os.chmod(tmp, 0o664)
    except (IOError, OSError):
        pass
    _chown_www(tmp)
    try:
        _check_fault(fault_step)
        os.rename(tmp, path)
        _sync_dir(parent)
    except StoreError:
        _remove_file(tmp)
        raise
    except (IOError, OSError) as exc:
        _remove_file(tmp)
        raise StoreError(_io_text(fault_step, exc))
    try:
        os.chmod(path, 0o664)
    except (IOError, OSError):
        pass
    _chown_www(path)


def _remove_file(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _remove_db(path):
    _remove_file(path)
    for suffix in ('-journal', '-wal', '-shm'):
        _remove_file(path + suffix)


def _db_bytes(path):
    total = 0
    for suffix in ('', '-journal', '-wal', '-shm'):
        candidate = path + suffix
        if os.path.isfile(candidate):
            total += os.path.getsize(candidate)
    return total


def _connect_existing(path):
    try:
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    except sqlite3.Error as exc:
        raise _db_error(exc)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute('PRAGMA cache_size=-128')
        mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        if str(mode).lower() != 'delete':
            conn.execute('PRAGMA journal_mode=DELETE')
    except sqlite3.Error as exc:
        conn.close()
        raise _db_error(exc)
    return conn


def _create_db(path, generation):
    _ensure_dir(os.path.dirname(path))
    if os.path.exists(path):
        raise StoreError(
            'measured-rate database already exists for the new generation; '
            'it was not overwritten')
    try:
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    except sqlite3.Error as exc:
        raise _db_error(exc)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA page_size=4096')
        conn.execute('PRAGMA auto_vacuum=INCREMENTAL')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute('PRAGMA cache_size=-128')
        # isolation_level=None is autocommit: COMMIT without BEGIN is an error.
        _begin(conn)
        try:
            for statement in _SCHEMA:
                conn.execute(statement)
            auto = int(conn.execute('PRAGMA auto_vacuum').fetchone()[0])
            if auto != 2:
                raise StoreError(
                    'measured-rate database cannot enable incremental vacuum on this '
                    'SQLite build')
            _put_meta(conn, 'v', '1')
            _put_meta(conn, 'generation', generation)
            _put_meta(conn, 'sequence', '0')
            _put_meta(conn, 'last_saved', '')
            _put_meta(conn, 'dropped', '0')
            _commit(conn)
        except StoreError:
            _rollback(conn)
            raise
        except sqlite3.Error as exc:
            _rollback(conn)
            raise _db_error(exc)
    except StoreError:
        conn.close()
        _remove_db(path)
        raise
    except sqlite3.Error as exc:
        conn.close()
        _remove_db(path)
        raise _db_error(exc)
    except (IOError, OSError) as exc:
        conn.close()
        _remove_db(path)
        raise StoreError(_io_text('create the measured-rate database', exc))
    try:
        _sync_file(path)
    except (IOError, OSError):
        pass
    try:
        os.chmod(path, 0o664)
    except (IOError, OSError):
        pass
    _chown_www(path)
    return conn


def _put_meta(conn, key, value):
    conn.execute(
        'INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)', (key, value))


def _get_meta(conn, key):
    row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    if row is None:
        return None
    return row[0]


def _begin(conn):
    conn.execute('BEGIN IMMEDIATE')


def _commit(conn):
    _check_fault('db-commit')
    try:
        conn.execute('COMMIT')
    except sqlite3.Error as exc:
        raise _db_error(exc)


def _rollback(conn):
    try:
        conn.execute('ROLLBACK')
    except sqlite3.Error:
        pass


def _integrity(conn, full):
    pragma = 'integrity_check' if full else 'quick_check'
    try:
        rows = conn.execute('PRAGMA ' + pragma).fetchall()
    except sqlite3.Error as exc:
        raise _db_error(exc)
    if len(rows) != 1 or str(rows[0][0]).lower() != 'ok':
        raise StoreError(
            'measured-rate database is corrupt or incomplete; refusing to replace '
            'it with an empty store')


def _open_existing(path, generation):
    conn = _connect_existing(path)
    try:
        _integrity(conn, False)
        version = _get_meta(conn, 'v')
        got = _get_meta(conn, 'generation')
        if version != '1' or got != generation:
            raise StoreError(
                'measured-rate database does not match the active generation; '
                'refusing to start an empty replacement')
    except StoreError:
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise _db_error(exc)
    return conn


def _load_db_meta(data):
    sequence = _get_meta(data.conn, 'sequence')
    saved = _get_meta(data.conn, 'last_saved')
    dropped = _get_meta(data.conn, 'dropped')
    try:
        data.sequence = int(sequence or 0)
        data.dropped_db = int(dropped or 0)
    except (TypeError, ValueError):
        raise StoreError(
            'measured-rate database metadata is unreadable; refusing to start an '
            'empty replacement')
    if saved:
        try:
            data.last_saved = _num(saved)
        except StoreError:
            raise StoreError(
                'measured-rate database metadata is unreadable; refusing to start '
                'an empty replacement')
    else:
        data.last_saved = None


def _control_dict(data, board_ids=None):
    return {
        'v': 1,
        'generation': data.generation,
        'target': data.target,
        'board_ids': dict(data.board_ids if board_ids is None else board_ids),
        'salt': data.salt,
    }


def _publish_control(sd_dir, control):
    if control.get('target') not in ('SD', 'RAM'):
        raise StoreError("measured-rate target must be 'SD' or 'RAM'")
    try:
        generation = _text_keep(control.get('generation', ''))
        salt = _text_keep(control.get('salt', ''))
    except StoreError:
        raise StoreError('measured-rate control record is not valid')
    if not _HEX32.match(generation) or not _HEX64.match(salt):
        raise StoreError('measured-rate control record is not valid')
    boards = control.get('board_ids')
    if not isinstance(boards, dict):
        raise StoreError('measured-rate control record is not valid')
    keys = []
    for key in boards.keys():
        try:
            keys.append(_text_keep(key))
        except StoreError:
            raise StoreError('measured-rate control record is not valid')
    if sorted(keys) != ['0', '1', '2']:
        raise StoreError('measured-rate control record is not valid')
    normalized = {}
    for key in ('0', '1', '2'):
        try:
            ident = _text_keep(boards[key])
        except StoreError:
            raise StoreError('measured-rate control record is not valid')
        if not _HEX32.match(ident):
            raise StoreError('measured-rate control record is not valid')
        normalized[key] = ident
    control = {
        'v': 1,
        'generation': generation,
        'target': control['target'],
        'board_ids': normalized,
        'salt': salt,
    }
    payload = _canonical(control).encode('utf-8')
    _atomic_bytes(_control_path(sd_dir), payload, 'control-publish')


def _read_control(sd_dir):
    path = _control_path(sd_dir)
    if not os.path.exists(path):
        return None
    try:
        handle = open(path, 'rb')
        try:
            raw = handle.read(8193)
        finally:
            handle.close()
        if len(raw) > 8192:
            raise StoreError(
                'measured-rate control record is unreadable; refusing to start an '
                'empty dataset')
        parsed = json.loads(raw.decode('utf-8'))
    except StoreError:
        raise
    except Exception:
        raise StoreError(
            'measured-rate control record is unreadable; refusing to start an '
            'empty dataset')
    if not isinstance(parsed, dict) or parsed.get('v') != 1:
        raise StoreError(
            'measured-rate control record is unreadable; refusing to start an '
            'empty dataset')
    target = parsed.get('target')
    boards = parsed.get('board_ids')
    try:
        generation = _text_keep(parsed.get('generation', ''))
        salt = _text_keep(parsed.get('salt', ''))
    except StoreError:
        raise StoreError(
            'measured-rate control record is unreadable; refusing to start an '
            'empty dataset')
    if target not in ('SD', 'RAM') or not _HEX32.match(generation) or not _HEX64.match(salt):
        raise StoreError(
            'measured-rate control record is unreadable; refusing to start an '
            'empty dataset')
    if not isinstance(boards, dict):
        raise StoreError(
            'measured-rate control record is unreadable; refusing to start an '
            'empty dataset')
    normalized = {}
    for key in ('0', '1', '2'):
        value = boards.get(key)
        try:
            value = _text_keep(value)
        except StoreError:
            raise StoreError(
                'measured-rate control record is unreadable; refusing to start an '
                'empty dataset')
        if not _HEX32.match(value):
            raise StoreError(
                'measured-rate control record is unreadable; refusing to start an '
                'empty dataset')
        normalized[key] = value
    return {
        'generation': generation,
        'target': target,
        'salt': salt,
        'board_ids': normalized,
    }


def _callback_meta(data):
    return {
        'generation': data.generation,
        'target': data.target,
        'board_ids': {
            '0': data.board_ids['0'],
            '1': data.board_ids['1'],
            '2': data.board_ids['2'],
        },
        'salt': data.salt,
    }


def _state_dict(data):
    return {
        'v': 1,
        'generation': data.generation,
        'next_seq': data.next_seq,
        'dropped': data.dropped,
        'pending_since': data.pending_since,
        'io_error': data.io_error,
        'intervals': data.intervals,
        'runtime': data.runtime,
    }


def _write_state(ram_dir, data):
    _runtime_blob(data.runtime)
    payload = _canonical(_state_dict(data)).encode('utf-8')
    if len(payload) > MAX_PENDING_BYTES + MAX_RUNTIME_BYTES + 4096:
        raise StoreError(
            'measured-rate RAM buffer exceeds its byte cap; the new snapshot was '
            'not written')
    _atomic_bytes(_state_path(ram_dir, data.generation), payload, 'state-write')


def _load_state(ram_dir, data):
    path = _state_path(ram_dir, data.generation)
    if not os.path.exists(path):
        data.next_seq = data.sequence + 1
        data.dropped = data.dropped_db
        data.runtime = {}
        data.intervals = []
        return False
    try:
        handle = open(path, 'rb')
        try:
            raw = handle.read(MAX_PENDING_BYTES + MAX_RUNTIME_BYTES + 8193)
        finally:
            handle.close()
        if len(raw) > MAX_PENDING_BYTES + MAX_RUNTIME_BYTES + 8192:
            raise StoreError(
                'measured-rate RAM buffer is unreadable; clear the measured-rate '
                'dataset before continuing. Durable history was not modified')
        parsed = json.loads(raw.decode('utf-8'))
    except StoreError:
        raise
    except Exception:
        raise StoreError(
            'measured-rate RAM buffer is unreadable; clear the measured-rate '
            'dataset before continuing. Durable history was not modified')
    generation = parsed.get('generation') if isinstance(parsed, dict) else None
    generation = generation if isinstance(generation, str) else (
        _text_keep(generation) if isinstance(generation, _TEXT) else '')
    if generation != data.generation or not isinstance(parsed.get('runtime'), dict):
        raise StoreError(
            'measured-rate RAM buffer does not match the active generation; clear '
            'the measured-rate dataset before continuing. Durable history was not '
            'modified')
    intervals = parsed.get('intervals')
    if not isinstance(intervals, list):
        raise StoreError(
            'measured-rate RAM buffer is unreadable; clear the measured-rate '
            'dataset before continuing. Durable history was not modified')
    normalized = []
    discarded = False
    for item in intervals:
        if not isinstance(item, dict) or not _is_int(item.get('seq')):
            raise StoreError(
                'measured-rate RAM buffer is unreadable; clear the measured-rate '
                'dataset before continuing. Durable history was not modified')
        # A reset can publish the new installation id before the snapshot is
        # rewritten. Drop only that board's stale intervals; keep the others.
        if _stale_board_interval(item.get('interval'), data.board_ids):
            discarded = True
            continue
        interval = _validate_interval(item.get('interval'), data.board_ids)
        normalized.append({'seq': int(item['seq']), 'interval': interval})
    try:
        data.next_seq = int(parsed.get('next_seq') or (data.sequence + 1))
        data.dropped = int(parsed.get('dropped') or 0)
    except (TypeError, ValueError):
        raise StoreError(
            'measured-rate RAM buffer is unreadable; clear the measured-rate '
            'dataset before continuing. Durable history was not modified')
    pending_since = parsed.get('pending_since')
    if pending_since is None:
        data.pending_since = None
    else:
        data.pending_since = _finite(pending_since, 'pending timestamp', minimum=0)
    error = parsed.get('io_error') or ''
    if not isinstance(error, _TEXT):
        error = ''
    data.io_error = error if isinstance(error, str) else _text_keep(error)
    data.intervals = normalized
    data.runtime = parsed['runtime']
    if data.next_seq <= data.sequence:
        data.next_seq = data.sequence + 1
    return discarded


def _stale_board_interval(interval, board_ids):
    """True when a readable interval belongs to a replaced board installation."""
    if not isinstance(interval, dict):
        return False
    context = interval.get('context')
    if not isinstance(context, dict) or not _is_int(context.get('board')):
        return False
    board = int(context['board'])
    if board not in (0, 1, 2):
        return False
    installation = context.get('installation')
    if not (_is_binary(installation) or isinstance(installation, _TEXT)):
        return False
    try:
        installation = _text_keep(installation)
        expected = _text_keep(board_ids[str(board)])
    except StoreError:
        return False
    return installation != expected


def _enqueue(data, intervals, now):
    for interval in intervals:
        if len(_uncommitted(data)) >= MAX_PENDING_INTERVALS:
            data.dropped += 1
            continue
        prospective = data.intervals + [{
            'seq': data.next_seq,
            'interval': interval,
        }]
        try:
            encoded = _canonical(prospective)
        except (TypeError, ValueError):
            raise StoreError('measured-rate interval could not be stored')
        if len(encoded) > MAX_PENDING_BYTES:
            data.dropped += 1
            continue
        if data.pending_since is None:
            data.pending_since = now
        data.intervals.append({'seq': data.next_seq, 'interval': interval})
        data.next_seq += 1


def _uncommitted(data):
    pending = [item for item in data.intervals if item['seq'] > data.sequence]
    pending.sort(key=lambda item: item['seq'])
    return pending


def _drop_committed(data):
    before = len(data.intervals)
    data.intervals = [item for item in data.intervals if item['seq'] > data.sequence]
    if not _uncommitted(data):
        data.pending_since = None
    return len(data.intervals) != before


def _reconcile_dropped(data):
    if data.dropped_db > data.dropped:
        data.dropped = data.dropped_db
        return True
    return False


def _should_flush(data, now, force):
    pending = _uncommitted(data)
    if not pending:
        return False
    if data.target == 'RAM' or force:
        return True
    if data.pending_since is None:
        return False
    return now < data.pending_since or (now - data.pending_since) >= FLUSH_SECONDS


def _flush(data, now):
    pending = _uncommitted(data)
    if not pending:
        return
    try:
        if _flush_batch(data, pending, now):
            data.sequence = pending[-1]['seq']
            data.last_saved = now
            _drop_committed(data)
            return
    except StoreError:
        raise
    for item in pending:
        result = _flush_one(data, item, now)
        if result == 'saved':
            data.sequence = item['seq']
            data.last_saved = now
        elif result == 'dropped':
            data.sequence = item['seq']
            data.dropped += 1
        else:
            raise StoreError(
                'measured-rate data could not be saved; unsaved samples remain in '
                'the RAM buffer and the last good data was kept')
    _drop_committed(data)


def _flush_batch(data, pending, now):
    conn = data.conn
    _begin(conn)
    try:
        protected = set()
        for item in pending:
            _apply_interval(conn, item['interval'], protected)
        if not _enforce_caps(conn, now, protected):
            _rollback(conn)
            return False
        _sync_bounds(conn)
        _put_meta(conn, 'sequence', str(pending[-1]['seq']))
        _put_meta(conn, 'last_saved', _format_num(now))
        _put_meta(conn, 'dropped', str(max(data.dropped, data.dropped_db)))
        _commit(conn)
        _shrink_file(conn, data.db_path, now)
        return True
    except StoreError:
        _rollback(conn)
        raise
    except sqlite3.Error as exc:
        _rollback(conn)
        raise _db_error(exc)


def _flush_one(data, item, now):
    conn = data.conn
    _begin(conn)
    try:
        protected = set()
        _apply_interval(conn, item['interval'], protected)
        if not _enforce_caps(conn, now, protected):
            _rollback(conn)
            return _mark_dropped(data, item)
        _sync_bounds(conn)
        _put_meta(conn, 'sequence', str(item['seq']))
        _put_meta(conn, 'last_saved', _format_num(now))
        _put_meta(conn, 'dropped', str(max(data.dropped, data.dropped_db)))
        _commit(conn)
        _shrink_file(conn, data.db_path, now)
        return 'saved'
    except StoreError:
        _rollback(conn)
        raise
    except sqlite3.Error as exc:
        _rollback(conn)
        raise _db_error(exc)


def _mark_dropped(data, item):
    """Advance the watermark over an interval that cannot fit. It is not saved."""
    conn = data.conn
    _begin(conn)
    try:
        dropped = max(data.dropped, data.dropped_db) + 1
        _put_meta(conn, 'sequence', str(item['seq']))
        _put_meta(conn, 'dropped', str(dropped))
        _commit(conn)
        data.dropped_db = dropped
        return 'dropped'
    except StoreError:
        _rollback(conn)
        raise
    except sqlite3.Error as exc:
        _rollback(conn)
        raise _db_error(exc)


def _split_interval(interval):
    seconds = float(interval['seconds'])
    end = float(interval['end'])
    start = end - seconds
    pieces = []
    cursor = start
    left = seconds
    guard = 0
    while left > 1e-9 and guard < 4:
        hour = int(cursor // 3600) * 3600
        room = (hour + 3600) - cursor
        if room <= 1e-12:
            cursor = hour + 3600
            guard += 1
            continue
        chunk = left if left <= room else room
        if left - chunk <= 1e-9:
            chunk = left
        pieces.append({
            'hour': hour,
            'seconds': chunk,
            'start': cursor,
            'end': cursor + chunk,
        })
        cursor += chunk
        left -= chunk
        guard += 1
    if not pieces:
        raise StoreError('measured-rate interval could not be placed in an hour')
    # The last piece absorbs binary remainder so the stored seconds match.
    consumed = 0.0
    for piece in pieces[:-1]:
        consumed += piece['seconds']
    pieces[-1]['seconds'] = seconds - consumed
    pieces[-1]['end'] = end
    pieces[0]['start'] = start
    return pieces


def _parts_float(total, weights):
    if len(weights) == 1:
        return [total]
    weight_sum = 0.0
    for weight in weights:
        weight_sum += weight
    if weight_sum <= 0:
        raise StoreError('measured-rate interval could not be aggregated')
    out = []
    consumed = 0.0
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            out.append(total - consumed)
        else:
            part = total * (weight / weight_sum)
            out.append(part)
            consumed += part
    return out


def _parts_int(total, weights):
    total = int(total)
    if len(weights) == 1:
        return [total]
    weight_sum = 0.0
    for weight in weights:
        weight_sum += weight
    raw = []
    floors = []
    for weight in weights:
        value = (total * weight) / weight_sum
        raw.append(value)
        floors.append(int(math.floor(value + 1e-9)))
    short = total - sum(floors)
    order = list(range(len(weights)))
    order.sort(key=lambda index: (raw[index] - floors[index], weights[index]), reverse=True)
    for index in range(short):
        floors[order[index]] += 1
    if sum(floors) != total:
        raise StoreError('measured-rate interval counters could not be aggregated')
    return floors


def _apply_interval(conn, interval, protected):
    context = interval['context']
    context_id = interval['context_id']
    protected.add(context_id)
    encoded = _canonical(context)
    row = conn.execute(
        'SELECT context_json, first_epoch, last_epoch FROM contexts WHERE context_id = ?',
        (context_id,)).fetchone()
    start = float(interval['end']) - float(interval['seconds'])
    end = float(interval['end'])
    if row is None:
        conn.execute(
            'INSERT INTO contexts(context_id, board, installation, context_json, '
            'first_epoch, last_epoch) VALUES (?, ?, ?, ?, ?, ?)',
            (context_id, int(context['board']), _text_keep(context['installation']),
             encoded, start, end))
    else:
        if row[0] != encoded:
            raise StoreError(
                'measured-rate context_id does not match the stored context; '
                'the interval was not stored')
        conn.execute(
            'UPDATE contexts SET first_epoch = ?, last_epoch = ? WHERE context_id = ?',
            (min(float(row[1]), start), max(float(row[2]), end), context_id))
    pieces = _split_interval(interval)
    weights = [piece['seconds'] for piece in pieces]
    difficulties = _parts_float(interval['difficulty'], weights)
    temp_sums = _parts_float(interval['temp_sum'], weights)
    fan_sums = _parts_float(interval['fan_sum'], weights)
    accepted = _parts_int(interval['accepted'], weights)
    rejected = _parts_int(interval['rejected'], weights)
    hw = _parts_int(interval['hw'], weights)
    for index, piece in enumerate(pieces):
        _merge_bucket(conn, interval, piece, difficulties[index], temp_sums[index],
                      fan_sums[index], accepted[index], rejected[index], hw[index])


def _merge_bucket(conn, interval, piece, difficulty, temp_sum, fan_sum,
                  accepted, rejected, hw):
    context_id = interval['context_id']
    hour = int(piece['hour'])
    row = conn.execute(
        'SELECT seconds, difficulty, accepted, rejected, hw, temp_sum, temp_min, '
        'temp_max, fan_sum, fixed32, first_epoch, last_epoch FROM buckets '
        'WHERE context_id = ? AND hour = ?',
        (context_id, hour)).fetchone()
    if row is None:
        conn.execute(
            'INSERT INTO buckets(context_id, hour, seconds, difficulty, accepted, '
            'rejected, hw, temp_sum, temp_min, temp_max, fan_sum, fixed32, '
            'first_epoch, last_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (context_id, hour, piece['seconds'], difficulty, accepted, rejected, hw,
             temp_sum, interval['temp_min'], interval['temp_max'], fan_sum,
             1 if interval['fixed32'] else 0, piece['start'], piece['end']))
        return
    conn.execute(
        'UPDATE buckets SET seconds = ?, difficulty = ?, accepted = ?, rejected = ?, '
        'hw = ?, temp_sum = ?, temp_min = ?, temp_max = ?, fan_sum = ?, fixed32 = ?, '
        'first_epoch = ?, last_epoch = ? WHERE context_id = ? AND hour = ?',
        (float(row[0]) + piece['seconds'], float(row[1]) + difficulty,
         int(row[2]) + accepted, int(row[3]) + rejected, int(row[4]) + hw,
         float(row[5]) + temp_sum, min(float(row[6]), interval['temp_min']),
         max(float(row[7]), interval['temp_max']), float(row[8]) + fan_sum,
         1 if (int(row[9]) and interval['fixed32']) else 0,
         min(float(row[10]), piece['start']), max(float(row[11]), piece['end']),
         context_id, hour))


def _used_bytes(conn):
    page_size = int(conn.execute('PRAGMA page_size').fetchone()[0])
    page_count = int(conn.execute('PRAGMA page_count').fetchone()[0])
    freelist = int(conn.execute('PRAGMA freelist_count').fetchone()[0])
    return (page_count - freelist) * page_size


def _evict_expired(conn, now):
    newest = conn.execute('SELECT MAX(last_epoch) FROM contexts').fetchone()[0]
    if newest is not None and now + 1 < float(newest):
        # Clock moved backwards relative to stored observations.
        return
    if data_last_saved_blocks_jump(conn, now, newest):
        return
    cutoff = now - RETENTION_SECONDS
    conn.execute('DELETE FROM buckets WHERE hour + 3600 <= ?', (cutoff,))
    _delete_empty_contexts(conn)


def data_last_saved_blocks_jump(conn, now, newest):
    """Skip a single enormous forward step that is not real aging.

    A gap longer than the retention window still ages normally when the last
    commit itself is old. A step of more than 30 days since a commit that was
    current with the newest sample is treated as a clock jump so one bad
    timestamp cannot wipe the file.
    """
    if newest is None:
        return False
    saved = _get_meta(conn, 'last_saved')
    if not saved:
        return False
    try:
        saved = float(saved)
    except (TypeError, ValueError):
        return False
    if now <= saved + (30 * 24 * 3600):
        return False
    return float(newest) + 3600 >= saved


def _delete_empty_contexts(conn):
    conn.execute(
        'DELETE FROM contexts WHERE context_id NOT IN (SELECT context_id FROM buckets)')


def _delete_context(conn, context_id):
    conn.execute('DELETE FROM buckets WHERE context_id = ?', (context_id,))
    conn.execute('DELETE FROM contexts WHERE context_id = ?', (context_id,))


def _context_count(conn):
    return int(conn.execute('SELECT COUNT(*) FROM contexts').fetchone()[0])


def _oldest_context(conn, protected, allow_protected):
    rows = conn.execute(
        'SELECT context_id FROM contexts ORDER BY last_epoch ASC, context_id ASC'
    ).fetchall()
    fallback = None
    for row in rows:
        context_id = row[0]
        if context_id not in protected:
            return context_id
        if allow_protected and fallback is None:
            fallback = context_id
    return fallback


def _oldest_closed_bucket(conn, now, protected, allow_protected):
    current = int(now // 3600) * 3600
    rows = conn.execute(
        'SELECT context_id, hour FROM buckets WHERE hour < ? '
        'ORDER BY hour ASC, context_id ASC',
        (current,)).fetchall()
    fallback = None
    for row in rows:
        if row[0] not in protected:
            return row[0], int(row[1])
        if allow_protected and fallback is None:
            fallback = (row[0], int(row[1]))
    return fallback


def _enforce_caps(conn, now, protected):
    _evict_expired(conn, now)
    while _context_count(conn) > MAX_CONTEXTS:
        victim = _oldest_context(conn, protected, False)
        if victim is None:
            return False
        _delete_context(conn, victim)
    while _used_bytes(conn) > MAX_DB_BYTES:
        closed = _oldest_closed_bucket(conn, now, protected, True)
        if closed is not None:
            conn.execute(
                'DELETE FROM buckets WHERE context_id = ? AND hour = ?', closed)
            _delete_empty_contexts(conn)
            continue
        victim = _oldest_context(conn, protected, False)
        if victim is None:
            return _used_bytes(conn) <= MAX_DB_BYTES
        _delete_context(conn, victim)
    return True


def _sync_bounds(conn):
    rows = conn.execute('SELECT context_id FROM contexts').fetchall()
    for row in rows:
        context_id = row[0]
        bounds = conn.execute(
            'SELECT MIN(first_epoch), MAX(last_epoch) FROM buckets WHERE context_id = ?',
            (context_id,)).fetchone()
        if bounds[0] is None:
            _delete_context(conn, context_id)
        else:
            conn.execute(
                'UPDATE contexts SET first_epoch = ?, last_epoch = ? WHERE context_id = ?',
                (bounds[0], bounds[1], context_id))


def _shrink_file(conn, path, now):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= MAX_DB_BYTES:
            return
        _begin(conn)
        try:
            if not _enforce_caps(conn, now, set()):
                _rollback(conn)
                return
            _sync_bounds(conn)
            _commit(conn)
        except StoreError:
            _rollback(conn)
            return
        conn.execute('PRAGMA incremental_vacuum')
        if os.path.getsize(path) > MAX_DB_BYTES:
            conn.execute('VACUUM')
    except (sqlite3.Error, IOError, OSError, StoreError):
        _rollback(conn)


def _delete_board_rows(conn, board):
    _begin(conn)
    try:
        conn.execute('DELETE FROM buckets WHERE context_id IN '
                     '(SELECT context_id FROM contexts WHERE board = ?)', (board,))
        conn.execute('DELETE FROM contexts WHERE board = ?', (board,))
    except sqlite3.Error as exc:
        _rollback(conn)
        raise _db_error(exc)


def _purge_foreign_contexts(conn, board_ids):
    try:
        rows = conn.execute(
            'SELECT context_id, board, installation FROM contexts').fetchall()
    except sqlite3.Error as exc:
        raise _db_error(exc)
    stale = []
    for row in rows:
        board = str(int(row[1]))
        installation = row[2]
        if board not in board_ids or installation != board_ids[board]:
            stale.append(row[0])
    if not stale:
        return
    _begin(conn)
    try:
        for context_id in stale:
            _delete_context(conn, context_id)
        _commit(conn)
    except StoreError:
        _rollback(conn)
        raise
    except sqlite3.Error as exc:
        _rollback(conn)
        raise _db_error(exc)


def _forget_board_runtime(data, board):
    runtime = data.runtime
    if not isinstance(runtime, dict):
        data.runtime = {'boards': {str(board): {}}}
        return
    boards = runtime.get('boards')
    if not isinstance(boards, dict):
        boards = {}
        runtime['boards'] = boards
    boards[str(board)] = {}


def _scrub_runtime(data):
    boards = data.runtime.get('boards') if isinstance(data.runtime, dict) else None
    if not isinstance(boards, dict):
        return False
    changed = False
    for board, expected in data.board_ids.items():
        state = boards.get(board)
        if not isinstance(state, dict):
            continue
        anchor = state.get('anchor')
        if not isinstance(anchor, dict):
            continue
        context = anchor.get('context')
        if not isinstance(context, dict):
            continue
        installation = context.get('installation')
        if not (_is_binary(installation) or isinstance(installation, _TEXT)):
            continue
        try:
            got = _text_keep(installation)
            want = _text_keep(expected)
        except StoreError:
            continue
        if got != want:
            boards[board] = {}
            changed = True
    return changed


def _copy_database(source, destination):
    _check_fault('db-copy')
    _remove_db(destination)
    try:
        shutil.copyfile(source, destination)
        _sync_file(destination)
    except (IOError, OSError) as exc:
        _remove_db(destination)
        raise StoreError(
            'measured-rate data could not be copied; the original dataset and '
            'target were left unchanged')
    try:
        os.chmod(destination, 0o664)
    except (IOError, OSError):
        pass
    _chown_www(destination)


def _verify_copy(conn, generation, expected_seq, expected, applied_pending):
    _integrity(conn, True)
    if _get_meta(conn, 'generation') != generation:
        raise StoreError(
            'measured-rate copy failed verification; the original dataset and '
            'target were left unchanged')
    try:
        sequence = int(_get_meta(conn, 'sequence') or 0)
    except (TypeError, ValueError):
        raise StoreError(
            'measured-rate copy failed verification; the original dataset and '
            'target were left unchanged')
    if sequence != int(expected_seq):
        raise StoreError(
            'measured-rate copy failed verification; the original dataset and '
            'target were left unchanged')
    if not applied_pending:
        return
    totals = _totals(conn)
    for key in ('accepted', 'rejected', 'hw'):
        if int(totals[key]) != int(expected[key]):
            raise StoreError(
                'measured-rate copy did not match the source data; the original '
                'dataset and target were left unchanged')
    for key in ('seconds', 'difficulty'):
        if abs(float(totals[key]) - float(expected[key])) > 1e-6:
            raise StoreError(
                'measured-rate copy did not match the source data; the original '
                'dataset and target were left unchanged')


def _totals(conn):
    row = conn.execute(
        'SELECT COALESCE(SUM(seconds), 0), COALESCE(SUM(difficulty), 0), '
        'COALESCE(SUM(accepted), 0), COALESCE(SUM(rejected), 0), COALESCE(SUM(hw), 0) '
        'FROM buckets').fetchone()
    return {
        'seconds': float(row[0]),
        'difficulty': float(row[1]),
        'accepted': int(row[2]),
        'rejected': int(row[3]),
        'hw': int(row[4]),
    }


def _interval_totals(pending):
    totals = {'seconds': 0.0, 'difficulty': 0.0, 'accepted': 0, 'rejected': 0, 'hw': 0}
    for item in pending:
        interval = item['interval']
        totals['seconds'] += float(interval['seconds'])
        totals['difficulty'] += float(interval['difficulty'])
        totals['accepted'] += int(interval['accepted'])
        totals['rejected'] += int(interval['rejected'])
        totals['hw'] += int(interval['hw'])
    return totals


def _add_totals(left, right):
    return dict((key, left[key] + right[key]) for key in left)


def _empty_aggregate(context_id, context):
    return {
        'context_id': context_id,
        'context': context,
        'seconds': 0.0,
        'difficulty': 0.0,
        'accepted': 0,
        'rejected': 0,
        'hw': 0,
        'temp_sum': 0.0,
        'temp_min': None,
        'temp_max': None,
        'fan_sum': 0.0,
        'fixed32': None,
        'first': None,
        'last': None,
    }


def _read_aggregates(conn, board_ids):
    try:
        contexts = conn.execute(
            'SELECT context_id, context_json, board, installation, first_epoch, last_epoch '
            'FROM contexts').fetchall()
        buckets = conn.execute(
            'SELECT context_id, seconds, difficulty, accepted, rejected, hw, temp_sum, '
            'temp_min, temp_max, fan_sum, fixed32, first_epoch, last_epoch FROM buckets'
        ).fetchall()
    except sqlite3.Error as exc:
        raise _db_error(exc)
    by_id = {}
    for row in contexts:
        board = str(int(row[2]))
        if board not in board_ids or row[3] != board_ids[board]:
            continue
        try:
            context = json.loads(row[1])
        except (TypeError, ValueError):
            raise StoreError(
                'measured-rate database context is unreadable; refusing to replace '
                'it with an empty store')
        item = _empty_aggregate(row[0], context)
        item['first'] = float(row[4])
        item['last'] = float(row[5])
        by_id[row[0]] = item
    for row in buckets:
        item = by_id.get(row[0])
        if item is None:
            continue
        item['seconds'] += float(row[1])
        item['difficulty'] += float(row[2])
        item['accepted'] += int(row[3])
        item['rejected'] += int(row[4])
        item['hw'] += int(row[5])
        item['temp_sum'] += float(row[6])
        item['temp_min'] = float(row[7]) if item['temp_min'] is None else min(item['temp_min'], float(row[7]))
        item['temp_max'] = float(row[8]) if item['temp_max'] is None else max(item['temp_max'], float(row[8]))
        item['fan_sum'] += float(row[9])
        flag = bool(int(row[10]))
        item['fixed32'] = flag if item['fixed32'] is None else (item['fixed32'] and flag)
        item['first'] = float(row[11]) if item['first'] is None else min(item['first'], float(row[11]))
        item['last'] = float(row[12]) if item['last'] is None else max(item['last'], float(row[12]))
    return by_id


def _overlay_intervals(by_id, intervals):
    for interval in intervals:
        context_id = interval['context_id']
        item = by_id.get(context_id)
        if item is None:
            item = _empty_aggregate(context_id, interval['context'])
            by_id[context_id] = item
        elif _canonical(item['context']) != _canonical(interval['context']):
            raise StoreError(
                'measured-rate context_id does not match the stored context')
        start = float(interval['end']) - float(interval['seconds'])
        item['seconds'] += float(interval['seconds'])
        item['difficulty'] += float(interval['difficulty'])
        item['accepted'] += int(interval['accepted'])
        item['rejected'] += int(interval['rejected'])
        item['hw'] += int(interval['hw'])
        item['temp_sum'] += float(interval['temp_sum'])
        item['fan_sum'] += float(interval['fan_sum'])
        item['temp_min'] = interval['temp_min'] if item['temp_min'] is None else min(
            item['temp_min'], interval['temp_min'])
        item['temp_max'] = interval['temp_max'] if item['temp_max'] is None else max(
            item['temp_max'], interval['temp_max'])
        item['fixed32'] = interval['fixed32'] if item['fixed32'] is None else (
            item['fixed32'] and interval['fixed32'])
        item['first'] = start if item['first'] is None else min(item['first'], start)
        item['last'] = interval['end'] if item['last'] is None else max(item['last'], interval['end'])


def _finish_aggregate(item):
    if item['seconds'] <= 0:
        return None
    return {
        'context_id': item['context_id'],
        'context': item['context'],
        'seconds': _num(item['seconds']),
        'difficulty': _num(item['difficulty']),
        'accepted': int(item['accepted']),
        'rejected': int(item['rejected']),
        'hw': int(item['hw']),
        'temp_sum': _num(item['temp_sum']),
        'temp_min': _num(item['temp_min']),
        'temp_max': _num(item['temp_max']),
        'fan_sum': _num(item['fan_sum']),
        'fixed32': bool(item['fixed32']),
        'first': _num(item['first']),
        'last': _num(item['last']),
    }


def _contexts(data):
    by_id = _read_aggregates(data.conn, data.board_ids)
    _overlay_intervals(by_id, [item['interval'] for item in _uncommitted(data)])
    rows = []
    for item in by_id.values():
        finished = _finish_aggregate(item)
        if finished is not None:
            rows.append(finished)
    rows.sort(key=lambda row: (row['context']['board'], row['context_id']))
    return rows


def _error_text(data):
    parts = []
    if data.io_error:
        parts.append(data.io_error)
    if data.dropped:
        parts.append(
            'Dropped %d measured-rate intervals because the storage cap was reached.'
            % int(data.dropped))
    return ' '.join(parts)


def _status(data, now):
    pending = _uncommitted(data)
    try:
        runtime = json.loads(_runtime_blob(data.runtime))
    except StoreError:
        runtime = {}
    return {
        'ok': not bool(data.io_error),
        'target': data.target,
        'generation': data.generation,
        'board_ids': {
            '0': data.board_ids['0'],
            '1': data.board_ids['1'],
            '2': data.board_ids['2'],
        },
        'updated': _num(now),
        'last_saved': None if data.last_saved is None else _num(data.last_saved),
        'pending_intervals': len(pending),
        'unsaved': bool(pending),
        'error': _error_text(data),
        'bytes': _db_bytes(data.db_path),
        'dropped': int(data.dropped),
        'contexts': _contexts(data),
        'runtime': runtime,
    }


def _cleanup_others(ram_dir, sd_dir, generation, target):
    active = ram_dir if target == 'RAM' else sd_dir
    for directory in (ram_dir, sd_dir):
        if not os.path.isdir(directory):
            continue
        for name in list(os.listdir(directory)):
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            if _TMP_RE.match(name):
                _remove_file(path)
                continue
            matched = _DB_RE.match(name) or _SIDE_RE.match(name) or _STATE_RE.match(name)
            if not matched:
                continue
            other = matched.group(1)
            if other != generation:
                _remove_file(path)
                continue
            if _STATE_RE.match(name):
                if directory != ram_dir:
                    _remove_file(path)
                continue
            if directory != active:
                _remove_file(path)


def _remove_generation_files(ram_dir, sd_dir, generation):
    if not _HEX32.match(generation):
        return
    for directory in (ram_dir, sd_dir):
        _remove_db(_db_path(directory, generation))
        _remove_file(_state_path(directory, generation))
        _remove_file(_state_path(directory, generation) + '.tmp')
