#!/usr/bin/env python3
"""Private application-configuration backup helper.

Fixed verbs only: snapshot, restore, list, export, import, finish, rename, delete.
Stdin is a small JSON object. Every path is chosen here. This is not an
OS-image backup and it never applies network settings.
"""
from __future__ import print_function

import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import zipfile

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

from measured_rate_service import Service
from measured_rate_store import (
    MAX_DB_BYTES,
    MAX_PENDING_BYTES,
    MAX_PENDING_INTERVALS,
    MAX_RUNTIME_BYTES,
    StoreError,
    _exclusive,
    _read_control,
    _stale_board_interval,
    _validate_interval,
)

try:
    _TEXT = (str, unicode)  # noqa: F821
except NameError:
    _TEXT = (str,)

VERBS = ('snapshot', 'restore', 'list', 'export', 'import', 'finish', 'rename', 'delete')
VERB_FIELDS = {
    'snapshot': ('name', 'items'),
    'restore': ('name',),
    'list': (),
    'export': ('name',),
    'import': ('name',),
    'finish': (),
    'rename': ('name', 'to'),
    'delete': ('name',),
}
NETWORK_STATUS_ARGV = ('/opt/scripta/bin/network-settings.py', 'backup')
KIND = 'blakestream-application-config'
NAME_RE_TEXT = r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
IFACE_RE_TEXT = r'^[A-Za-z][A-Za-z0-9_.-]{0,14}$'
BASELINE_RE_TEXT = (
    r'^clock-baselines/[A-Za-z0-9][A-Za-z0-9._-]{0,63}'
    r'/[A-Za-z0-9][A-Za-z0-9._-]{0,63}/baseline\.json$'
)
GENERATION_RE_TEXT = r'^[0-9a-f]{32}$'
HEX64_RE_TEXT = r'^[0-9a-f]{64}$'
IPV4_RE_TEXT = (
    r'^(?:0|1?\d?\d|2[0-4]\d|25[0-5])'
    r'(?:\.(?:0|1?\d?\d|2[0-4]\d|25[0-5])){3}$'
)

import re
NAME_RE = re.compile(NAME_RE_TEXT)
IFACE_RE = re.compile(IFACE_RE_TEXT)
BASELINE_RE = re.compile(BASELINE_RE_TEXT)
GENERATION_RE = re.compile(GENERATION_RE_TEXT)
HEX64_RE = re.compile(HEX64_RE_TEXT)
IPV4_RE = re.compile(IPV4_RE_TEXT)
MEASURED_DB_RE = re.compile(r'^measured-rates-([0-9a-f]{32})\.sqlite$')
MEASURED_SIDE_RE = re.compile(r'^measured-rates-([0-9a-f]{32})\.sqlite-(journal|wal|shm)$')
MEASURED_STATE_RE = re.compile(r'^measured-rates-state-([0-9a-f]{32})\.json$')

CONFIG_NAMES = (
    'scripta.conf',
    'uipasswd',
    'saved-pools.json',
    'board-assignments.json',
    'miner.options.json',
    'miner.pools.json',
    'clock-profiles.json',
    'failover-config.json',
    'temp-config.json',
    'clock-baseline.json',
)
HISTORY_NAMES = (
    'hashrate-history.json',
    'pool-history.json',
    'failover-events.json',
    'clock-history.json',
)
RAM_HISTORY_NAMES = HISTORY_NAMES + ('clock-history-sd-buffer.json',)
SELECTABLE = (
    tuple('etc/' + name for name in CONFIG_NAMES)
    + tuple('var/' + name for name in HISTORY_NAMES)
    + tuple('ram/' + name for name in RAM_HISTORY_NAMES)
)
LEGACY_PATHS = (
    tuple('etc/' + name for name in CONFIG_NAMES)
    + tuple('var/' + name for name in HISTORY_NAMES)
)
ENVELOPE = ('manifest.json', 'manifest.sha256')
MODES = ('dhcp', 'static', 'link-local', 'shared', 'disabled')
CACHE_NAMES = ('measured-rates-status.json', 'accepted-work-rates.json')
MAX_STDIN = 16384
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_FILES = 96
MAX_UPLOAD = 16 * 1024 * 1024
MAX_STATUS_BYTES = 262144

_fault_hook = None


class BackupError(Exception):
    def __init__(self, code, detail=None):
        Exception.__init__(self, code)
        self.code = code
        self.detail = detail


class _LeaveJournal(BaseException):
    """Test-only interrupt that keeps the on-disk rollback journal."""


class Layout(object):
    def __init__(self, root):
        self.root = root
        self.etc = os.path.join(root, 'opt', 'scripta', 'etc')
        self.var = os.path.join(root, 'opt', 'scripta', 'var')
        self.ram = os.path.join(root, 'run', 'blakestream')
        self.backup_root = os.path.join(self.etc, 'backup')
        self.baselines = os.path.join(root, 'var', 'lib', 'blakestream-clock-baselines')
        self.network_restore = os.path.join(self.etc, 'network-restore.json')
        self.upload = os.path.join(self.ram, 'backup-upload.bin')
        self.export_zip = os.path.join(self.ram, 'backup-export.zip')
        self.incoming = os.path.join(self.backup_root, '.incoming')
        self.rollback_dir = os.path.join(self.backup_root, '.rollback')
        self.lock_path = os.path.join(self.ram, 'backup-settings.lock')


def _service_root(layout):
    if os.path.abspath(layout.root) == os.path.abspath(os.sep):
        return ''
    return layout.root


def _hook(step):
    hook = _fault_hook
    if hook is not None:
        hook(step)


def _json_bytes(value):
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return (text + '\n').encode('ascii')


def _sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def _is_text(value):
    return isinstance(value, _TEXT)


def _within(parent, path):
    parent_real = os.path.realpath(parent)
    path_real = os.path.realpath(path)
    return path_real == parent_real or path_real.startswith(parent_real + os.sep)


def _reject_symlink(path):
    current = os.path.abspath(path)
    while current != os.path.dirname(current):
        if os.path.islink(current):
            raise BackupError('symlink')
        current = os.path.dirname(current)


def _mkdir(path, mode):
    _reject_symlink(path)
    if os.path.isdir(path) and not os.path.islink(path):
        return
    if os.path.exists(path):
        raise BackupError('io')
    parent = os.path.dirname(path)
    if parent and parent != path and not os.path.isdir(parent):
        _mkdir(parent, mode)
    _reject_symlink(parent)
    os.mkdir(path, mode)
    os.chmod(path, mode)


def _read_nofollow(path, limit):
    _reject_symlink(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        raise BackupError('io')
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BackupError('io')
        if info.st_size > limit:
            raise BackupError('limit')
        chunks = []
        got = 0
        while got < info.st_size:
            block = os.read(fd, min(65536, info.st_size - got))
            if not block:
                break
            got += len(block)
            if got > limit:
                raise BackupError('limit')
            chunks.append(block)
        if got != info.st_size:
            raise BackupError('io')
        return b''.join(chunks)
    finally:
        os.close(fd)


def _write_replace(path, blob, mode):
    parent = os.path.dirname(path)
    _mkdir(parent, 0o755)
    _reject_symlink(parent)
    _reject_symlink(path)
    owner = os.stat(path if os.path.exists(path) else parent)
    tmp = path + '.bktmp'
    _reject_symlink(tmp)
    if os.path.exists(tmp):
        _unlink_file(tmp)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        offset = 0
        while offset < len(blob):
            written = os.write(fd, blob[offset:])
            if written <= 0:
                raise OSError('short backup write')
            offset += written
        if os.geteuid() == 0:
            os.fchown(fd, owner.st_uid, owner.st_gid)
        os.fsync(fd)
    except OSError:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise BackupError('io')
    os.close(fd)
    os.chmod(tmp, mode & 0o666)
    os.rename(tmp, path)
    try:
        dirfd = os.open(parent, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except OSError:
        pass


def _unlink_file(path):
    _reject_symlink(path)
    if not os.path.exists(path):
        return
    if not os.path.isfile(path):
        raise BackupError('io')
    os.unlink(path)


def archive_path_ok(path):
    if not _is_text(path) or len(path) > 180 or '\\' in path or '\x00' in path:
        return False
    if path == 'network/portable.json':
        return True
    if path in ('measured/control.json', 'measured/history.sqlite', 'measured/pending.json'):
        return True
    if path.startswith('etc/'):
        return path[4:] in CONFIG_NAMES and '/' not in path[4:]
    if path.startswith('var/'):
        return path[4:] in HISTORY_NAMES and '/' not in path[4:]
    if path.startswith('ram/'):
        return path[4:] in RAM_HISTORY_NAMES and '/' not in path[4:]
    return BASELINE_RE.match(path) is not None


def _key_ok(key):
    if not _is_text(key) or len(key) > 220 or '\\' in key or '\x00' in key:
        return False
    if key.startswith('etc/'):
        name = key[4:]
        return '/' not in name and (name in CONFIG_NAMES or name == 'network-restore.json')
    if key.startswith('var/'):
        name = key[4:]
        if '/' in name:
            return False
        if name in HISTORY_NAMES or name == 'measured-rates-control.json':
            return True
        return MEASURED_DB_RE.match(name) is not None or MEASURED_SIDE_RE.match(name) is not None
    if key.startswith('ram/'):
        name = key[4:]
        if '/' in name:
            return False
        if name in RAM_HISTORY_NAMES or name in CACHE_NAMES:
            return True
        return (
            MEASURED_DB_RE.match(name) is not None
            or MEASURED_SIDE_RE.match(name) is not None
            or MEASURED_STATE_RE.match(name) is not None
        )
    return BASELINE_RE.match(key) is not None


def _key_for(layout, path):
    absolute = os.path.abspath(path)
    mapping = (
        (os.path.abspath(layout.etc), 'etc'),
        (os.path.abspath(layout.var), 'var'),
        (os.path.abspath(layout.ram), 'ram'),
        (os.path.abspath(layout.baselines), 'clock-baselines'),
    )
    for root, prefix in mapping:
        lead = root + os.sep
        if absolute.startswith(lead):
            key = prefix + '/' + absolute[len(lead):].replace(os.sep, '/')
            if _key_ok(key):
                return key
            break
    raise BackupError('io')


def _path_for_key(layout, key):
    if not _key_ok(key):
        raise BackupError('rollback-failed')
    if key.startswith('etc/'):
        return os.path.join(layout.etc, key[4:])
    if key.startswith('var/'):
        return os.path.join(layout.var, key[4:])
    if key.startswith('ram/'):
        return os.path.join(layout.ram, key[4:])
    if key.startswith('clock-baselines/'):
        return os.path.join(layout.baselines, *key.split('/')[1:])
    raise BackupError('rollback-failed')


def _dest_for_archive(layout, archive_path):
    if not archive_path_ok(archive_path) or archive_path.startswith('measured/') or archive_path == 'network/portable.json':
        raise BackupError('unsafe-archive')
    if archive_path.startswith('etc/'):
        return os.path.join(layout.etc, archive_path[4:])
    if archive_path.startswith('var/'):
        return os.path.join(layout.var, archive_path[4:])
    if archive_path.startswith('ram/'):
        return os.path.join(layout.ram, archive_path[4:])
    return os.path.join(layout.baselines, *archive_path.split('/')[1:])


def _mode_for(path):
    if path == 'etc/uipasswd':
        return 0o600
    return 0o664


class _Change(object):
    def __init__(self, path, blob, mode):
        self.path = path
        self.blob = blob
        self.mode = mode


class _Journal(object):
    def __init__(self, layout):
        self.layout = layout
        self.directory = layout.rollback_dir
        self.entries = []

    def capture(self, change):
        key = _key_for(self.layout, change.path)
        index = len(self.entries)
        entry = {'key': key, 'existed': False, 'mode': 0o664, 'file': None}
        path = change.path
        if os.path.islink(path):
            raise BackupError('symlink')
        if os.path.exists(path):
            if not os.path.isfile(path):
                raise BackupError('io')
            blob = _read_nofollow(path, MAX_FILE_BYTES)
            name = '%04d' % index
            _write_replace(os.path.join(self.directory, name), blob, 0o600)
            entry['existed'] = True
            entry['mode'] = os.stat(path).st_mode & 0o666
            entry['file'] = name
        self.entries.append(entry)

    def commit(self):
        _write_replace(os.path.join(self.directory, 'journal.json'), _json_bytes({
            'v': 1,
            'entries': self.entries,
        }), 0o600)


def _discard_tree(path, parent):
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not _within(parent, path):
        raise BackupError('symlink')
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def recover_rollback(layout):
    directory = layout.rollback_dir
    if not os.path.lexists(directory):
        return
    if os.path.islink(directory) or not _within(layout.backup_root, directory):
        raise BackupError('symlink')
    journal_path = os.path.join(directory, 'journal.json')
    if not os.path.exists(journal_path):
        _discard_tree(directory, layout.backup_root)
        return
    if os.path.islink(journal_path):
        raise BackupError('rollback-failed')
    try:
        parsed = json.loads(_read_nofollow(journal_path, 262144).decode('utf-8'))
    except (BackupError, ValueError, UnicodeError):
        raise BackupError('rollback-failed')
    if not isinstance(parsed, dict) or parsed.get('v') != 1 or not isinstance(parsed.get('entries'), list):
        raise BackupError('rollback-failed')
    if len(parsed['entries']) > MAX_ARCHIVE_FILES + 32:
        raise BackupError('rollback-failed')
    for entry in reversed(parsed['entries']):
        if not isinstance(entry, dict):
            raise BackupError('rollback-failed')
        path = _path_for_key(layout, entry.get('key'))
        if entry.get('existed') is True:
            name = entry.get('file')
            if not _is_text(name) or not re.match(r'^[0-9]{4}$', name):
                raise BackupError('rollback-failed')
            blob = _read_nofollow(os.path.join(directory, name), MAX_FILE_BYTES)
            mode = entry.get('mode')
            if isinstance(mode, bool) or not isinstance(mode, int):
                mode = 0o664
            _write_replace(path, blob, mode & 0o666)
        elif entry.get('existed') is False:
            if os.path.islink(path):
                raise BackupError('rollback-failed')
            if os.path.isfile(path):
                os.unlink(path)
        else:
            raise BackupError('rollback-failed')
    _discard_tree(directory, layout.backup_root)


def _recover_pending(layout):
    if not os.path.lexists(layout.rollback_dir):
        return
    service = Service(_service_root(layout))
    with service.locked(wait=True):
        with _exclusive(service.ram, service.sd):
            recover_rollback(layout)


def _apply_plan(layout, changes):
    if len(changes) > MAX_ARCHIVE_FILES + 32:
        raise BackupError('limit')
    _mkdir(layout.rollback_dir, 0o700)
    journal = _Journal(layout)
    try:
        for change in changes:
            journal.capture(change)
        journal.commit()
    except BackupError:
        _discard_tree(layout.rollback_dir, layout.backup_root)
        raise
    except (IOError, OSError):
        _discard_tree(layout.rollback_dir, layout.backup_root)
        raise BackupError('io')
    try:
        for change in changes:
            _hook('restore-write')
            if change.blob is None:
                _unlink_file(change.path)
                if change.path.startswith(os.path.abspath(layout.baselines) + os.sep):
                    _prune_empty(os.path.dirname(change.path), layout.baselines)
            else:
                _write_replace(change.path, change.blob, change.mode)
            _hook('restore-wrote')
    except _LeaveJournal:
        raise
    except Exception:
        try:
            recover_rollback(layout)
        except BackupError as exc:
            if exc.code == 'rollback-failed':
                raise
            raise BackupError('rollback-failed')
        except (IOError, OSError):
            raise BackupError('rollback-failed')
        raise BackupError('rollback')
    _discard_tree(layout.rollback_dir, layout.backup_root)


def _prune_empty(path, stop):
    stop = os.path.abspath(stop)
    while os.path.abspath(path).startswith(stop + os.sep):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


class _Lock(object):
    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        parent = os.path.dirname(self.path)
        _mkdir(parent, 0o775)
        self.handle = open(self.path, 'a')
        try:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except (IOError, OSError):
            self.handle.close()
            raise BackupError('io')
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            try:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except (IOError, OSError):
                pass
            self.handle.close()
        return False


def _prepare(layout):
    for path in (layout.etc, layout.var, layout.ram, layout.baselines, layout.backup_root):
        if os.path.islink(path):
            raise BackupError('symlink')
    if not os.path.isdir(layout.etc):
        raise BackupError('io')
    _mkdir(layout.var, 0o775)
    _mkdir(layout.ram, 0o775)
    _mkdir(layout.backup_root, 0o700)
    if os.geteuid() == 0:
        os.chown(layout.backup_root, 0, 0)
    os.chmod(layout.backup_root, 0o700)


def _name_ok(name):
    return _is_text(name) and NAME_RE.match(name) is not None


def _backup_dir(layout, name):
    if not _name_ok(name):
        raise BackupError('invalid-name')
    path = os.path.join(layout.backup_root, name)
    if os.path.islink(path):
        raise BackupError('symlink')
    if os.path.exists(path) and not _within(layout.backup_root, path):
        raise BackupError('symlink')
    return path


def _parse_items(items):
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > 64:
        raise BackupError('invalid-request')
    selected = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item.keys()) - set(('name', 'selected')):
            raise BackupError('invalid-request')
        name = item.get('name')
        if not _is_text(name) or name not in SELECTABLE or name in seen:
            raise BackupError('invalid-request')
        seen.add(name)
        selected_flag = item.get('selected', False)
        if not isinstance(selected_flag, bool):
            raise BackupError('invalid-request')
        if selected_flag:
            selected.append(name)
    return selected


def _ipv4(value, allow_none):
    if value is None and allow_none:
        return None
    if not _is_text(value) or IPV4_RE.match(value) is None:
        raise BackupError('network')
    return value


def portable_from_status(status):
    if not isinstance(status, dict) or status.get('ok') is not True:
        raise BackupError('network')
    interfaces = status.get('interfaces')
    if not isinstance(interfaces, list) or len(interfaces) > 8:
        raise BackupError('network')
    cleaned = []
    for item in interfaces:
        if not isinstance(item, dict):
            raise BackupError('network')
        interface = item.get('interface')
        if not _is_text(interface) or IFACE_RE.match(interface) is None:
            raise BackupError('network')
        mode = item.get('mode')
        if mode is not None and (not _is_text(mode) or mode not in MODES):
            raise BackupError('network')
        prefix = item.get('prefix')
        if prefix is not None and (isinstance(prefix, bool) or not isinstance(prefix, int) or prefix < 0 or prefix > 32):
            raise BackupError('network')
        dns = item.get('dns')
        if not isinstance(dns, list) or len(dns) > 4:
            raise BackupError('network')
        saved = item.get('saved')
        if not isinstance(saved, bool):
            raise BackupError('network')
        cleaned.append({
            'interface': interface,
            'mode': mode,
            'address': _ipv4(item.get('address'), True),
            'prefix': prefix,
            'gateway': _ipv4(item.get('gateway'), True),
            'dns': [_ipv4(entry, False) for entry in dns],
            'saved': saved,
        })
    return {'v': 1, 'kind': 'network-portable', 'interfaces': cleaned}


def _parse_portable_blob(blob, code):
    try:
        parsed = json.loads(blob.decode('utf-8'))
    except (ValueError, UnicodeError, AttributeError):
        raise BackupError(code)
    if (
        not isinstance(parsed, dict)
        or set(parsed.keys()) != set(('v', 'kind', 'interfaces'))
        or parsed.get('v') != 1
        or parsed.get('kind') != 'network-portable'
    ):
        raise BackupError(code)
    try:
        cleaned = portable_from_status({'ok': True, 'interfaces': parsed.get('interfaces')})
    except BackupError:
        raise BackupError(code)
    return cleaned


def default_network_status():
    try:
        proc = subprocess.Popen(
            list(NETWORK_STATUS_ARGV),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _err = proc.communicate(b'{}')
    except (OSError, IOError):
        raise BackupError('network')
    if out is None or len(out) > MAX_STATUS_BYTES:
        raise BackupError('network')
    try:
        parsed = json.loads(out.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise BackupError('network')
    return parsed


def _read_optional(path, limit, code):
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        raise BackupError('symlink')
    if not os.path.isfile(path):
        raise BackupError(code)
    return _read_nofollow(path, limit)


def _collect_exact(root, names, prefix, budget=MAX_ARCHIVE_BYTES):
    files = {}
    _reject_symlink(root)
    if not os.path.isdir(root):
        return files
    for name in names:
        path = os.path.join(root, name)
        blob = _read_optional(path, min(MAX_FILE_BYTES, budget), 'io')
        if blob is not None:
            files[prefix + name] = blob
            budget -= len(blob)
    return files


def _walk_baselines(layout, read, budget=MAX_ARCHIVE_BYTES):
    root = layout.baselines
    found = {}
    if not os.path.lexists(root):
        return found
    if os.path.islink(root) or not os.path.isdir(root):
        raise BackupError('symlink')
    for campaign in sorted(os.listdir(root)):
        if NAME_RE.match(campaign) is None:
            continue
        campaign_path = os.path.join(root, campaign)
        if os.path.islink(campaign_path):
            raise BackupError('symlink')
        if not os.path.isdir(campaign_path):
            continue
        for algorithm in sorted(os.listdir(campaign_path)):
            if NAME_RE.match(algorithm) is None:
                continue
            algorithm_path = os.path.join(campaign_path, algorithm)
            if os.path.islink(algorithm_path):
                raise BackupError('symlink')
            if not os.path.isdir(algorithm_path):
                continue
            baseline = os.path.join(algorithm_path, 'baseline.json')
            if not os.path.lexists(baseline):
                continue
            if os.path.islink(baseline) or not os.path.isfile(baseline):
                raise BackupError('symlink')
            key = 'clock-baselines/%s/%s/baseline.json' % (campaign, algorithm)
            if read:
                found[key] = _read_nofollow(baseline, min(MAX_FILE_BYTES, budget))
                budget -= len(found[key])
            else:
                found[key] = baseline
    return found


def _sqlite_consistent(blob, journal):
    if len(blob) > MAX_DB_BYTES:
        raise BackupError('limit')
    tmp = tempfile.mkdtemp(prefix='bkb-backup-db-')
    path = os.path.join(tmp, 'history.sqlite')
    try:
        with open(path, 'wb') as handle:
            handle.write(blob)
        os.chmod(path, 0o600)
        if journal:
            with open(path + '-journal', 'wb') as handle:
                handle.write(journal)
            os.chmod(path + '-journal', 0o600)
        conn = sqlite3.connect(path)
        try:
            checked = conn.execute('PRAGMA quick_check').fetchone()
            if checked is None or checked[0] != 'ok':
                raise BackupError('measured')
            rows = {}
            for key, value in conn.execute('select key, value from meta'):
                rows[key] = value
        except sqlite3.Error:
            raise BackupError('measured')
        finally:
            conn.close()
        if os.path.exists(path + '-journal'):
            try:
                os.unlink(path + '-journal')
            except OSError:
                raise BackupError('measured')
        normalized = _read_nofollow(path, MAX_DB_BYTES)
        return normalized, rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _nonneg_int(value, code):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1000000000:
        raise BackupError(code)
    return value


def _pending_document(control, sequence, state_blob, code):
    if state_blob and len(state_blob) > MAX_PENDING_BYTES + MAX_RUNTIME_BYTES + 8192:
        raise BackupError(code)
    intervals = []
    next_seq = sequence + 1
    dropped = 0
    pending_since = None
    if state_blob:
        try:
            parsed = json.loads(state_blob.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise BackupError(code)
        if not isinstance(parsed, dict) or parsed.get('generation') != control['generation']:
            raise BackupError(code)
        allowed = set((
            'v', 'generation', 'next_seq', 'dropped', 'pending_since',
            'intervals', 'runtime', 'io_error',
        ))
        if set(parsed.keys()) - allowed:
            raise BackupError(code)
        raw_items = parsed.get('intervals')
        if not isinstance(raw_items, list):
            raise BackupError(code)
        for item in raw_items:
            if not isinstance(item, dict):
                raise BackupError(code)
            seq = item.get('seq')
            if isinstance(seq, bool) or not isinstance(seq, int):
                raise BackupError(code)
            if seq <= sequence:
                continue
            try:
                if _stale_board_interval(item.get('interval'), control['board_ids']):
                    continue
                interval = _validate_interval(item.get('interval'), control['board_ids'])
            except StoreError:
                raise BackupError(code)
            intervals.append({'seq': seq, 'interval': interval})
        if len(intervals) > MAX_PENDING_INTERVALS:
            raise BackupError(code)
        stored_next = parsed.get('next_seq')
        if isinstance(stored_next, bool) or not isinstance(stored_next, int):
            raise BackupError(code)
        next_seq = stored_next
        if 'dropped' in parsed and parsed.get('dropped') is not None:
            dropped = _nonneg_int(parsed.get('dropped'), code)
        pending_since = parsed.get('pending_since')
        if pending_since is not None:
            if isinstance(pending_since, bool) or not isinstance(pending_since, (int, float)):
                raise BackupError(code)
            pending_since = float(pending_since)
            if pending_since < 0 or pending_since != pending_since or pending_since == float('inf'):
                raise BackupError(code)
    seqs = []
    for item in intervals:
        if item['seq'] in seqs or item['seq'] >= next_seq:
            raise BackupError(code)
        seqs.append(item['seq'])
    if next_seq <= sequence:
        raise BackupError(code)
    document = {
        'v': 1,
        'generation': control['generation'],
        'next_seq': next_seq,
        'dropped': dropped,
        'pending_since': pending_since,
        'intervals': intervals,
    }
    encoded = _json_bytes(document)
    if len(encoded) > MAX_PENDING_BYTES + 4096:
        raise BackupError('limit')
    return document


def _control_blob(control):
    return _json_bytes({
        'v': 1,
        'generation': control['generation'],
        'target': control['target'],
        'board_ids': {
            '0': control['board_ids']['0'],
            '1': control['board_ids']['1'],
            '2': control['board_ids']['2'],
        },
        'salt': control['salt'],
    })


def _load_control_blob(blob, code):
    if len(blob) > 8192:
        raise BackupError(code)
    tmp = tempfile.mkdtemp(prefix='bkb-backup-control-')
    try:
        with open(os.path.join(tmp, 'measured-rates-control.json'), 'wb') as handle:
            handle.write(blob)
        try:
            control = _read_control(tmp)
        except StoreError:
            raise BackupError(code)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not control:
        raise BackupError(code)
    return control


def _collect_measured(layout):
    service = Service(_service_root(layout))
    try:
        lock = service.locked(wait=True)
    except StoreError as exc:
        if 'busy' in str(exc).lower():
            raise BackupError('busy')
        raise BackupError('measured')
    copied = None
    try:
        with lock:
            with _exclusive(service.ram, service.sd):
                try:
                    control = _read_control(service.sd)
                except StoreError:
                    raise BackupError('measured')
                if control is None:
                    return {}
                directory = service.sd if control['target'] == 'SD' else service.ram
                generation = control['generation']
                db_path = os.path.join(directory, 'measured-rates-%s.sqlite' % generation)
                for suffix in ('-wal', '-shm'):
                    if os.path.lexists(db_path + suffix):
                        raise BackupError('measured')
                journal = None
                if os.path.lexists(db_path + '-journal'):
                    journal = _read_nofollow(db_path + '-journal', MAX_DB_BYTES)
                db_blob = _read_optional(db_path, MAX_DB_BYTES, 'measured')
                if db_blob is None and control['target'] != 'RAM':
                    raise BackupError('measured')
                state_path = os.path.join(service.ram, 'measured-rates-state-%s.json' % generation)
                state_limit = MAX_PENDING_BYTES + MAX_RUNTIME_BYTES + 8192
                state_blob = _read_optional(state_path, state_limit, 'measured')
                copied = (control, db_blob, journal, state_blob)
    except StoreError:
        raise BackupError('measured')
    if not copied:
        return {}
    control, db_blob, journal, state_blob = copied
    sequence = 0
    files = {'measured/control.json': _control_blob(control)}
    if db_blob is not None:
        normalized, meta = _sqlite_consistent(db_blob, journal)
        if meta.get('generation') != control['generation']:
            raise BackupError('measured')
        try:
            sequence = int(meta.get('sequence') or 0)
        except (TypeError, ValueError):
            raise BackupError('measured')
        files['measured/history.sqlite'] = normalized
    pending = _pending_document(control, sequence, state_blob, 'measured')
    files['measured/pending.json'] = _json_bytes(pending)
    return files


def _measured_files(layout):
    found = []
    for directory, prefix in ((layout.var, 'var'), (layout.ram, 'ram')):
        if not os.path.isdir(directory) or os.path.islink(directory):
            continue
        for name in os.listdir(directory):
            key = prefix + '/' + name
            if _key_ok(key) and (
                name == 'measured-rates-control.json'
                or MEASURED_DB_RE.match(name)
                or MEASURED_SIDE_RE.match(name)
                or MEASURED_STATE_RE.match(name)
                or name in CACHE_NAMES
            ):
                found.append(os.path.join(directory, name))
    return found


def _plan_measured(layout, files):
    changes = []
    remove = set(_measured_files(layout))
    if 'measured/control.json' not in files:
        for path in sorted(remove):
            changes.append(_Change(path, None, 0o664))
        return changes
    control = _load_control_blob(files['measured/control.json'], 'unsafe-archive')
    if 'measured/pending.json' not in files:
        raise BackupError('unsafe-archive')
    if control['target'] == 'SD' and 'measured/history.sqlite' not in files:
        raise BackupError('unsafe-archive')
    if control['target'] == 'RAM' and 'measured/history.sqlite' not in files:
        sequence = 0
        normalized = None
    else:
        normalized, meta = _sqlite_consistent(files['measured/history.sqlite'], None)
        if meta.get('generation') != control['generation']:
            raise BackupError('unsafe-archive')
        try:
            sequence = int(meta.get('sequence') or 0)
        except (TypeError, ValueError):
            raise BackupError('unsafe-archive')
    pending = _pending_document(control, sequence, files['measured/pending.json'], 'unsafe-archive')
    state = {
        'v': 1,
        'generation': control['generation'],
        'next_seq': pending['next_seq'],
        'dropped': pending['dropped'],
        'pending_since': pending['pending_since'],
        'io_error': '',
        'intervals': pending['intervals'],
        'runtime': {},
    }
    db_dir = layout.var if control['target'] == 'SD' else layout.ram
    writes = {
        os.path.join(layout.var, 'measured-rates-control.json'): _control_blob(control),
        os.path.join(layout.ram, 'measured-rates-state-%s.json' % control['generation']): _json_bytes(state),
    }
    if normalized is not None:
        writes[os.path.join(db_dir, 'measured-rates-%s.sqlite' % control['generation'])] = normalized
    for path, blob in sorted(writes.items()):
        changes.append(_Change(path, blob, 0o664))
        if path in remove:
            remove.remove(path)
    for path in sorted(remove):
        changes.append(_Change(path, None, 0o664))
    return changes


def _plan(layout, fmt, files, backup_name):
    changes = []
    if fmt == 'legacy':
        for archive_path in sorted(files):
            if archive_path not in LEGACY_PATHS:
                raise BackupError('unsafe-archive')
            changes.append(_Change(_dest_for_archive(layout, archive_path), files[archive_path], _mode_for(archive_path)))
        return changes
    if 'network/portable.json' not in files:
        raise BackupError('unsafe-archive')
    portable = _parse_portable_blob(files['network/portable.json'], 'unsafe-archive')
    for archive_path in SELECTABLE:
        dest = _dest_for_archive(layout, archive_path)
        if archive_path in files:
            changes.append(_Change(dest, files[archive_path], _mode_for(archive_path)))
        else:
            changes.append(_Change(dest, None, _mode_for(archive_path)))
    wanted = {}
    for archive_path, blob in files.items():
        if archive_path.startswith('clock-baselines/'):
            wanted[_dest_for_archive(layout, archive_path)] = blob
    existing = _walk_baselines(layout, False)
    for dest, blob in sorted(wanted.items()):
        changes.append(_Change(dest, blob, 0o644))
    for archive_path, dest in sorted(existing.items()):
        if dest not in wanted:
            changes.append(_Change(dest, None, 0o644))
    changes.extend(_plan_measured(layout, files))
    staged = {
        'v': 1,
        'staged': True,
        'applied': False,
        'backup': backup_name,
        'portable': portable,
    }
    changes.append(_Change(layout.network_restore, _json_bytes(staged), 0o664))
    return changes


def _manifest_for(files):
    entries = []
    for path in sorted(files):
        if not archive_path_ok(path):
            raise BackupError('unsafe-archive')
        blob = files[path]
        entries.append({'bytes': len(blob), 'path': path, 'sha256': _sha256(blob)})
    if len(entries) > MAX_ARCHIVE_FILES or sum(item['bytes'] for item in entries) > MAX_ARCHIVE_BYTES:
        raise BackupError('limit')
    return {'v': 2, 'kind': KIND, 'os_image': False, 'files': entries}


def _write_tree(directory, files):
    manifest = _json_bytes(_manifest_for(files))
    digest = (_sha256(manifest) + '\n').encode('ascii')
    payload = dict(files)
    payload['manifest.json'] = manifest
    payload['manifest.sha256'] = digest
    _mkdir(directory, 0o700)
    for path in sorted(payload):
        parts = path.split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise BackupError('unsafe-archive')
        dest = directory
        for part in parts[:-1]:
            dest = os.path.join(dest, part)
            _mkdir(dest, 0o700)
        _write_replace(os.path.join(directory, *parts), payload[path], 0o600)


def _load_v2_files(directory, supplied=None):
    def read(path, limit):
        if supplied is None:
            return _read_nofollow(os.path.join(directory, *path.split('/')), limit)
        blob = supplied.get(path)
        if blob is None or len(blob) > limit:
            raise BackupError('unsafe-archive')
        return blob
    names = list(supplied) if supplied is not None else []
    if supplied is None:
        for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
            for name in dirnames:
                _reject_symlink(os.path.join(dirpath, name))
            for name in filenames:
                path = os.path.join(dirpath, name)
                _reject_symlink(path)
                names.append(os.path.relpath(path, directory).replace(os.sep, '/'))
    if len(names) > MAX_ARCHIVE_FILES + len(ENVELOPE):
        raise BackupError('limit')
    if 'manifest.json' not in names or 'manifest.sha256' not in names:
        raise BackupError('unsafe-archive')
    manifest_blob = read('manifest.json', 262144)
    digest_blob = read('manifest.sha256', 128)
    if _sha256(manifest_blob) != digest_blob.decode('ascii').strip():
        raise BackupError('checksum')
    try:
        manifest = json.loads(manifest_blob.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise BackupError('unsafe-archive')
    if (
        not isinstance(manifest, dict)
        or manifest.get('v') != 2
        or manifest.get('kind') != KIND
        or manifest.get('os_image') is not False
        or not isinstance(manifest.get('files'), list)
    ):
        raise BackupError('unsafe-archive')
    files = {}
    total = 0
    for entry in manifest['files']:
        if not isinstance(entry, dict) or set(entry.keys()) != set(('bytes', 'path', 'sha256')):
            raise BackupError('unsafe-archive')
        path = entry.get('path')
        if not archive_path_ok(path) or path in files:
            raise BackupError('unsafe-archive')
        digest = entry.get('sha256')
        size = entry.get('bytes')
        if not _is_text(digest) or HEX64_RE.match(digest) is None:
            raise BackupError('checksum')
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_FILE_BYTES:
            raise BackupError('limit')
        total += size
        if total > MAX_ARCHIVE_BYTES:
            raise BackupError('limit')
        blob = read(path, MAX_FILE_BYTES)
        if len(blob) != size or _sha256(blob) != digest:
            raise BackupError('checksum')
        files[path] = blob
    expected = set(files) | set(ENVELOPE)
    if set(names) != expected:
        raise BackupError('unsafe-archive')
    if 'network/portable.json' not in files:
        raise BackupError('unsafe-archive')
    _parse_portable_blob(files['network/portable.json'], 'unsafe-archive')
    if 'measured/control.json' in files or 'measured/pending.json' in files or 'measured/history.sqlite' in files:
        if 'measured/control.json' not in files or 'measured/pending.json' not in files:
            raise BackupError('unsafe-archive')
        control = _load_control_blob(files['measured/control.json'], 'unsafe-archive')
        if control['target'] == 'SD' and 'measured/history.sqlite' not in files:
            raise BackupError('unsafe-archive')
    return files


def _load_legacy_files(directory):
    files = {}
    budget = MAX_ARCHIVE_BYTES
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        rel_dir = os.path.relpath(dirpath, directory).replace(os.sep, '/')
        if rel_dir == '.':
            if filenames:
                raise BackupError('unsafe-archive')
            for name in dirnames:
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or name not in ('etc', 'var'):
                    raise BackupError('unsafe-archive')
            continue
        if rel_dir not in ('etc', 'var') or dirnames:
            raise BackupError('unsafe-archive')
        for name in filenames:
            rel = rel_dir + '/' + name
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or rel not in LEGACY_PATHS:
                raise BackupError('unsafe-archive')
            if rel in files:
                raise BackupError('unsafe-archive')
            files[rel] = _read_nofollow(path, min(MAX_FILE_BYTES, budget))
            budget -= len(files[rel])
    if not files:
        raise BackupError('unsafe-archive')
    return files


def _load_backup(layout, name):
    directory = _backup_dir(layout, name)
    if not os.path.isdir(directory):
        raise BackupError('not-found')
    manifest = os.path.join(directory, 'manifest.json')
    if os.path.lexists(manifest):
        return _load_v2_files(directory), 'v2'
    return _load_legacy_files(directory), 'legacy'


def _list_one(layout, name):
    directory = os.path.join(layout.backup_root, name)
    entry = {'dir': name, 'items': None, 'format': 'unreadable'}
    try:
        if os.path.islink(directory) or not os.path.isdir(directory):
            return None
        manifest = os.path.join(directory, 'manifest.json')
        if os.path.lexists(manifest):
            files = _load_v2_files(directory)
            fmt = 'v2'
        else:
            files = _load_legacy_files(directory)
            fmt = 'legacy'
        items = sorted(files)
        entry = {'dir': name, 'items': items or None, 'format': fmt}
    except BackupError:
        return entry
    return entry


def _member_name(name):
    if not _is_text(name) or len(name) > 220 or '\\' in name or '\x00' in name or name.startswith('/'):
        raise BackupError('unsafe-archive')
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise BackupError('unsafe-archive')
    return parts


def _known_member(path, legacy_only):
    if legacy_only:
        return path in LEGACY_PATHS
    return path in ENVELOPE or archive_path_ok(path)


def _zip_entries(blob):
    if len(blob) > MAX_UPLOAD or len(blob) < 4 or not blob.startswith(b'PK'):
        raise BackupError('unsafe-archive')
    try:
        handle = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipfile:
        raise BackupError('unsafe-archive')
    except Exception:
        try:
            handle = zipfile.ZipFile(io.BytesIO(blob))
        except Exception:
            raise BackupError('unsafe-archive')
    infos = handle.infolist()
    if len(infos) > MAX_ARCHIVE_FILES + 8:
        handle.close()
        raise BackupError('limit')
    seen = set()
    files = {}
    total = 0
    try:
        for info in infos:
            parts = _member_name(info.filename[:-1] if info.filename.endswith('/') else info.filename)
            if info.flag_bits & 0x1:
                raise BackupError('unsafe-archive')
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise BackupError('unsafe-archive')
            unix = (info.external_attr >> 16) & 0o170000
            if unix not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise BackupError('unsafe-archive')
            if info.filename.endswith('/'):
                continue
            if unix == stat.S_IFDIR:
                raise BackupError('unsafe-archive')
            rel = '/'.join(parts)
            if rel in seen:
                raise BackupError('unsafe-archive')
            seen.add(rel)
            if info.file_size > MAX_FILE_BYTES:
                raise BackupError('limit')
            source = handle.open(info, 'r')
            try:
                chunks = []
                got = 0
                while True:
                    block = source.read(65536)
                    if not block:
                        break
                    got += len(block)
                    if got > MAX_FILE_BYTES or total + got > MAX_ARCHIVE_BYTES:
                        raise BackupError('limit')
                    chunks.append(block)
            finally:
                source.close()
            files[rel] = b''.join(chunks)
            total += len(files[rel])
    finally:
        handle.close()
    return _unwrap_members(files)


def _unwrap_members(files):
    if not files:
        raise BackupError('unsafe-archive')
    prefixes = set()
    unwrapped = {}
    for name in files:
        if _known_member(name, False):
            prefixes.add('')
            rel = name
        else:
            parts = name.split('/')
            rest = '/'.join(parts[1:])
            if len(parts) < 2 or NAME_RE.match(parts[0]) is None or not _known_member(rest, False):
                raise BackupError('unsafe-archive')
            prefixes.add(parts[0])
            rel = rest
        if rel in unwrapped:
            raise BackupError('unsafe-archive')
        unwrapped[rel] = files[name]
    if len(prefixes) != 1:
        raise BackupError('unsafe-archive')
    return unwrapped


def _accept_archive_files(files):
    if 'manifest.json' in files or 'manifest.sha256' in files:
        if 'manifest.json' not in files or 'manifest.sha256' not in files:
            raise BackupError('unsafe-archive')
        for path in files:
            if path not in ENVELOPE and not archive_path_ok(path):
                raise BackupError('unsafe-archive')
        return _load_v2_files(None, supplied=files), 'v2'
    for path in files:
        if path not in LEGACY_PATHS:
            raise BackupError('unsafe-archive')
    return files, 'legacy'


def _write_tree_raw(directory, files):
    _mkdir(directory, 0o700)
    for path, blob in files.items():
        parts = path.split('/')
        dest = directory
        for part in parts[:-1]:
            dest = os.path.join(dest, part)
            _mkdir(dest, 0o700)
        _write_replace(os.path.join(directory, *parts), blob, 0o600)


def _store_new(layout, name, files, fmt):
    final = _backup_dir(layout, name)
    if os.path.exists(final):
        raise BackupError('exists')
    _discard_tree(layout.incoming, layout.backup_root) if os.path.lexists(layout.incoming) else None
    try:
        if fmt == 'v2':
            _write_tree(layout.incoming, files)
        else:
            _write_tree_raw(layout.incoming, files)
        _hook('snapshot-before-rename')
        os.rename(layout.incoming, final)
        os.chmod(final, 0o700)
    except Exception:
        if os.path.lexists(layout.incoming):
            _discard_tree(layout.incoming, layout.backup_root)
        if isinstance(sys.exc_info()[1], BackupError):
            raise
        raise BackupError('io')
    return final


def snapshot(layout, name, items=None, network_status=None):
    if not _name_ok(name):
        raise BackupError('invalid-name')
    selected = _parse_items(items)
    _prepare(layout)
    with _Lock(layout.lock_path):
        _recover_pending(layout)
        if os.path.exists(_backup_dir(layout, name)):
            raise BackupError('exists')
        files = {}
        files.update(_collect_exact(layout.etc, CONFIG_NAMES, 'etc/'))
        remaining = lambda: MAX_ARCHIVE_BYTES - sum(len(blob) for blob in files.values())
        files.update(_collect_exact(layout.var, HISTORY_NAMES, 'var/', remaining()))
        files.update(_collect_exact(layout.ram, RAM_HISTORY_NAMES, 'ram/', remaining()))
        files.update(_walk_baselines(layout, True, remaining()))
        files.update(_collect_measured(layout))
        for item in selected:
            if item not in files:
                raise BackupError('missing', item)
        status = (network_status or default_network_status)()
        files['network/portable.json'] = _json_bytes(portable_from_status(status))
        _store_new(layout, name, files, 'v2')
    return {'ok': True, 'name': name, 'format': 'v2', 'stored': sorted(files)}


def restore(layout, name):
    if not _name_ok(name):
        raise BackupError('invalid-name')
    _prepare(layout)
    with _Lock(layout.lock_path):
        _recover_pending(layout)
        files, fmt = _load_backup(layout, name)
        service = Service(_service_root(layout))
        try:
            lock = service.locked(wait=True)
        except StoreError as exc:
            if 'busy' in str(exc).lower():
                raise BackupError('busy')
            raise BackupError('measured')
        with lock:
            with _exclusive(service.ram, service.sd):
                changes = _plan(layout, fmt, files, name)
                _apply_plan(layout, changes)
    return {
        'ok': True,
        'name': name,
        'format': fmt,
        'network_staged': fmt == 'v2',
    }


def list_backups(layout):
    _prepare(layout)
    with _Lock(layout.lock_path):
        _recover_pending(layout)
        backups = []
        if os.path.isdir(layout.backup_root):
            for name in sorted(os.listdir(layout.backup_root)):
                if NAME_RE.match(name) is None:
                    continue
                entry = _list_one(layout, name)
                if entry is not None:
                    backups.append(entry)
    return {'ok': True, 'backups': backups}


def _zip_bytes(directory):
    files, _fmt = _load_backup_directory(directory)
    # Re-read the verified directory, including the envelope, after validation.
    buffer = io.BytesIO()
    handle = zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED)
    try:
        for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
            dirnames[:] = sorted(dirnames)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, directory).replace(os.sep, '/')
                info = zipfile.ZipInfo(rel)
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                handle.writestr(info, _read_nofollow(path, MAX_FILE_BYTES))
    finally:
        handle.close()
    blob = buffer.getvalue()
    if len(blob) > MAX_UPLOAD:
        raise BackupError('limit')
    return blob, files


def _load_backup_directory(directory):
    manifest = os.path.join(directory, 'manifest.json')
    if os.path.lexists(manifest):
        return _load_v2_files(directory), 'v2'
    return _load_legacy_files(directory), 'legacy'


def export_bytes(layout, name):
    if not _name_ok(name):
        raise BackupError('invalid-name')
    _prepare(layout)
    with _Lock(layout.lock_path):
        _recover_pending(layout)
        directory = _backup_dir(layout, name)
        if not os.path.isdir(directory):
            raise BackupError('not-found')
        return _zip_bytes(directory)[0]


def export_file(layout, name):
    blob = export_bytes(layout, name)
    _reject_symlink(layout.export_zip)
    if os.path.lexists(layout.export_zip):
        _unlink_file(layout.export_zip)
    _write_replace(layout.export_zip, blob, 0o640)
    return {'ok': True, 'name': name, 'bytes': len(blob), 'sha256': _sha256(blob)}


def import_bytes(layout, name, blob):
    if not _name_ok(name):
        raise BackupError('invalid-name')
    files = _zip_entries(blob)
    loaded, fmt = _accept_archive_files(files)
    _prepare(layout)
    with _Lock(layout.lock_path):
        _recover_pending(layout)
        if os.path.exists(_backup_dir(layout, name)):
            raise BackupError('exists')
        if fmt == 'v2':
            _store_new(layout, name, loaded, 'v2')
        else:
            _store_new(layout, name, loaded, 'legacy')
    return {'ok': True, 'name': name, 'format': fmt}


def import_upload(layout, name):
    try:
        if os.path.islink(layout.upload):
            os.unlink(layout.upload)
            raise BackupError('symlink')
        blob = _read_nofollow(layout.upload, MAX_UPLOAD)
    finally:
        if os.path.lexists(layout.upload) and not os.path.islink(layout.upload) and os.path.isfile(layout.upload):
            try:
                os.unlink(layout.upload)
            except OSError:
                pass
    return import_bytes(layout, name, blob)


def finish(layout):
    _prepare(layout)
    for path in (layout.export_zip, layout.upload):
        if not os.path.lexists(path):
            continue
        if os.path.islink(path):
            os.unlink(path)
            continue
        if os.path.isfile(path) and os.path.abspath(path) in (
            os.path.abspath(layout.export_zip),
            os.path.abspath(layout.upload),
        ):
            os.unlink(path)
    return {'ok': True}


def manage_backup(layout, verb, name, new_name=None):
    _prepare(layout)
    with _Lock(layout.lock_path):
        _recover_pending(layout)
        source = _backup_dir(layout, name)
        if not os.path.isdir(source):
            raise BackupError('not-found')
        if verb == 'rename':
            target = _backup_dir(layout, new_name)
            if os.path.lexists(target):
                raise BackupError('exists')
            os.rename(source, target)
        elif verb == 'delete':
            _discard_tree(source, layout.backup_root)
        else:
            raise BackupError('invalid-request')
    return {'ok': True}


def _body_error(exc):
    body = {'ok': False, 'error': exc.code if isinstance(exc, BackupError) else 'io'}
    if isinstance(exc, BackupError) and exc.detail and _is_text(exc.detail) and (
        exc.detail in SELECTABLE or archive_path_ok(exc.detail)
    ):
        body['detail'] = exc.detail
    return body


def _read_stdin(stdin):
    raw = stdin.read(MAX_STDIN + 1)
    if raw is None:
        raw = ''
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8')
        except UnicodeError:
            raise BackupError('invalid-request')
    if len(raw) > MAX_STDIN:
        raise BackupError('invalid-request')
    if raw.strip() == '':
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        raise BackupError('invalid-request')
    if not isinstance(payload, dict):
        raise BackupError('invalid-request')
    return payload


def _emit(stdout, payload, code):
    stdout.write(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n')
    return code


def main(argv, stdin, stdout, layout=None, network_status=None):
    if layout is None:
        layout = Layout(os.sep)
    if not isinstance(argv, (list, tuple)) or len(argv) != 2 or argv[1] not in VERBS:
        return _emit(stdout, {'ok': False, 'error': 'invalid-request'}, 1)
    verb = argv[1]
    try:
        payload = _read_stdin(stdin)
        if set(payload.keys()) - set(VERB_FIELDS[verb]):
            raise BackupError('invalid-request')
        if verb == 'snapshot':
            result = snapshot(layout, payload.get('name'), payload.get('items'), network_status)
        elif verb == 'restore':
            result = restore(layout, payload.get('name'))
        elif verb == 'list':
            result = list_backups(layout)
        elif verb == 'export':
            result = export_file(layout, payload.get('name'))
        elif verb == 'import':
            result = import_upload(layout, payload.get('name'))
        elif verb in ('rename', 'delete'):
            result = manage_backup(layout, verb, payload.get('name'), payload.get('to'))
        else:
            result = finish(layout)
    except BackupError as exc:
        return _emit(stdout, _body_error(exc), 1)
    except (IOError, OSError, StoreError, ValueError):
        return _emit(stdout, {'ok': False, 'error': 'io'}, 1)
    return _emit(stdout, result, 0)


if __name__ == '__main__':
    sys.exit(main(sys.argv, sys.stdin, sys.stdout))
