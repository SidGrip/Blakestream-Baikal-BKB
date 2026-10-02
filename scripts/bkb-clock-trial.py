#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Guarded per-board clock index trial runner.

Run this as root on the miner. It does not flash the bridge, does not open a
pool, and does not tune fans, temperatures, workers, or the public 400 MHz
clock. The only settings it writes are three raw clock indices.

By default the schedule is validated and printed. Nothing is read from the
miner and nothing is written. Mutation requires ``run --execute`` or
``restore --execute``.

Indices are raw register values from 8 through 32, not MHz. Index 30 is the
host encoding used for the public 400 MHz candidate. In normal mode the host
reports RequestedClockIndex 0 and SentClockIndex 30. Experimental mode reports
the raw index in both fields. CommandVerified does not prove a PLL locked.
Power is not measured.

Examples
--------
Validate only (safe on any machine)::

    python bkb-clock-trial.py /root/clock-schedule.json

Read-only baseline on the miner::

    python bkb-clock-trial.py observe --host-sha <64 hex>

Supervised trial as root on the miner::

    python bkb-clock-trial.py run --execute --host-sha <64 hex> \\
        --output /root/clock-trial-1 /root/clock-schedule.json

If the process is killed, ExecStopPost or a watchdog can restore the backup::

    python bkb-clock-trial.py restore --execute \\
        --backup /root/clock-trial-1/original-miner.options.json \\
        --host-sha <64 hex>

Schedule JSON
-------------
``measurement_seconds`` is 1800..3600 and ``warmup_seconds`` is at least 120.
``indices`` is three integers, logical boards 0..2::

    {"legs": [{
        "name": "board0-29",
        "indices": [29, 30, 30],
        "measurement_seconds": 1800,
        "warmup_seconds": 120
    }]}

Exit codes: 0 ok, 2 usage or schedule, 3 lock busy, 4 output exists,
5 not root, 6 host or bridge identity, 7 trial failed after restore,
8 restore did not verify a normal baseline.
"""

from __future__ import print_function, division

import argparse
import errno
import fcntl
import hashlib
import json
import math
import os
import random
import signal
import socket
import stat
import subprocess
import sys
import time
from collections import OrderedDict

try:
    string_types = (basestring,)
    integer_types = (int, long)  # noqa: F821
    text_type = unicode  # noqa: F821
    binary_type = str
except NameError:
    string_types = (str,)
    integer_types = (int,)
    text_type = str
    binary_type = bytes


BRIDGE_ARTIFACT_SHA256 = (
    'd2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a'
)
DEFAULT_OPTIONS = '/opt/scripta/etc/miner.options.json'
DEFAULT_RESTART = '/opt/scripta/startup/miner-restart.sh'
DEFAULT_BRIDGE = '/opt/scripta/firmware/custom.bin'
DEFAULT_LOCK = '/run/blakestream/bkb-clock-trial.lock'
DEFAULT_API_HOST = '127.0.0.1'
DEFAULT_API_PORT = 4028

CLOCK_INDEX_MIN = 8
CLOCK_INDEX_MAX = 32
BASELINE_CLOCK_INDEX = 30
# Host contract: ExperimentalClock false reports this, not index 30.
NORMAL_REQUESTED_CLOCK_INDEX = 0
PUBLIC_CLOCK_MHZ = 400
BOARD_IDS = (0, 1, 2)
STARTUP_REQUIRED = 64
CHIP_REQUIRED = 64
SAMPLE_SECONDS = 15
WARMUP_MIN = 120
WARMUP_MAX = 3600
MEASURE_MIN = 1800
MEASURE_MAX = 3600
MAX_LEGS = 8
MAX_SCHEDULE_SECONDS = 12 * 3600
ACCEPT_GAP_SECONDS = 180
REJECT_FRACTION_MAX = 0.01
ENOUGH_SHARES = 100
EXPLORATORY_SHARES = 2000
DEFAULT_TEMP_MAX = 50.0
TEMP_MARGIN = 3
TEMP_MIN = 1.0
TEMP_SENSOR_MAX = 120.0
API_MAX_BYTES = 256 * 1024
API_MAX_SECONDS = 4.0
RESTART_DEADLINE = 90
SETTLE_DEADLINE = 180
RESTORE_DEADLINE = 180
SETTLE_INTERVAL = 5
# Recovery has its own sample budget so a cancelled trial cannot exhaust it.
RESTORE_SAMPLE_BUDGET = int(RESTORE_DEADLINE / SETTLE_INTERVAL) + 8
HW_POLICIES = ('strict', 'baseline-budget-v1', 'logged-budget-10-v1')
LOGGED_HW_LIMIT = 10
BRIDGE_MAX_BYTES = 2 * 1024 * 1024
SCHEDULE_MAX_BYTES = 64 * 1024
READ_COMMANDS = ('devs', 'stats')
CLOCK_KEYS = (
    'baikal-clock-mode',
    'baikal-experimental-clock-index',
    'baikal-board-clock-index',
    'blakestream-clock-scope',
)
FAN_KEY = 'baikal-fan'
OPTIONS_KEY = 'baikal-options'
BOARD_CLOCKS_KEY = 'baikal-board-clocks'
TRANSIENT_REASONS = set((
    'board-missing',
    'not-alive',
    'not-enabled',
    'command-not-verified',
    'clock-pending',
    'unsynchronized',
    'temperature-missing',
    'algorithm-pending',
))
CONFIDENCE = (
    'Clock indices select clock-table entries (8..32), not measured MHz. '
    'Index 30 is the host encoding used for the public 400 MHz candidate, '
    'not a calibrated frequency. CommandVerified, SentClockIndex, and '
    'RequestedClockIndex do not prove the ASIC PLL locked. '
    'Accepted-work GH/s = difficulty_delta * 2^32 / device_elapsed_seconds / 1e9. '
    'A leg with fewer than 2000 accepted shares on any board is exploratory '
    'and is not a stable hashrate measurement. Power is not measured.'
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LOCK = 3
EXIT_OUTPUT = 4
EXIT_ROOT = 5
EXIT_IDENTITY = 6
EXIT_FAILED = 7
EXIT_RESTORE = 8

MISSING = object()


class TrialError(Exception):
    def __init__(self, reason, message=None):
        Exception.__init__(self, message or reason)
        self.reason = reason


class TrialAborted(TrialError):
    pass


class LockBusy(TrialAborted):
    def __init__(self):
        TrialAborted.__init__(self, 'lock-busy', 'another clock trial holds the lock')


def monotonic_seconds():
    """Clock that does not go backwards when the wall clock steps."""
    if hasattr(time, 'monotonic'):
        return float(time.monotonic())
    try:
        import ctypes

        class Timespec(ctypes.Structure):
            _fields_ = [('tv_sec', ctypes.c_long), ('tv_nsec', ctypes.c_long)]

        # Linux's loader resolves this soname without find_library spawning
        # ldconfig on every Python 2 clock read on the low-memory controller.
        librt = ctypes.CDLL('librt.so.1', use_errno=True)
        spec = Timespec()
        if librt.clock_gettime(1, ctypes.byref(spec)) == 0:
            return float(spec.tv_sec) + float(spec.tv_nsec) / 1e9
    except Exception:
        pass
    try:
        with open('/proc/uptime', 'rb') as handle:
            return float(handle.read().split()[0])
    except (IOError, OSError, ValueError, IndexError):
        return None


def is_finite(value):
    return value == value and value not in (float('inf'), float('-inf'))


def to_text(value):
    if value is None:
        return ''
    if isinstance(value, text_type):
        return value
    if isinstance(value, binary_type):
        return value.decode('utf-8', 'replace')
    return text_type(value)


def to_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode('utf-8')
    return text_type(value).encode('utf-8')


def strict_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, integer_types):
        return int(value)
    return None


def strict_bool(value):
    if isinstance(value, bool):
        return value
    return None


def strict_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, integer_types) or isinstance(value, float):
        number = float(value)
        if not is_finite(number):
            return None
        return number
    return None


def canon(value):
    if value is MISSING:
        return ('missing',)
    if isinstance(value, bool):
        return ('b', value)
    if isinstance(value, string_types):
        return ('s', to_text(value))
    if isinstance(value, float):
        if not is_finite(value):
            return ('bad', None)
        if value == math.trunc(value) and abs(value) < 9007199254740992.0:
            return ('n', int(value))
        return ('f', value)
    if isinstance(value, integer_types):
        return ('n', int(value))
    return ('j', json.dumps(value, sort_keys=True, separators=(',', ':')))


def unique(items):
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def sha256_file(path, max_bytes=BRIDGE_MAX_BYTES):
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise TrialAborted('bridge-sha', 'bridge file exceeds size cap')
            digest.update(chunk)
    return digest.hexdigest()


def normalize_host_sha(value):
    text = to_text(value).strip().lower()
    if len(text) != 64:
        return None
    for char in text:
        if char not in '0123456789abcdef':
            return None
    return text


def accepted_rate_ghs(difficulty_delta, seconds):
    """difficulty_delta * 2**32 / seconds / 1e9. None when the window is unusable."""
    try:
        difficulty = float(difficulty_delta)
        span = float(seconds)
    except (TypeError, ValueError):
        return None
    if not is_finite(difficulty) or not is_finite(span):
        return None
    if difficulty < 0 or span <= 0:
        return None
    return (difficulty * 4294967296.0) / span / 1e9


def expected_requested_clock_index(experimental, sent_index):
    """Normal mode reports RequestedClockIndex 0. Experimental mode echoes the index."""
    if experimental:
        return sent_index
    return NORMAL_REQUESTED_CLOCK_INDEX


def measurement_window_problems(requested_seconds, mono_span, device_elapsed):
    """Full requested duration is required. Short or drifted windows are not success."""
    reasons = []
    try:
        requested = float(requested_seconds)
    except (TypeError, ValueError):
        return ['measurement-short']
    if not is_finite(requested):
        return ['measurement-short']
    try:
        mono = None if mono_span is None else float(mono_span)
    except (TypeError, ValueError):
        mono = None
    try:
        device = None if device_elapsed is None else float(device_elapsed)
    except (TypeError, ValueError):
        device = None
    if mono is None or not is_finite(mono) or mono + 1e-6 < requested:
        reasons.append('measurement-short')
    if device is None or not is_finite(device) or device + 1e-6 < requested:
        reasons.append('device-elapsed-short')
    if (
        mono is not None and device is not None
        and is_finite(mono) and is_finite(device)
    ):
        slack = max(2.0, requested * 0.001)
        if abs(device - mono) > slack:
            reasons.append('device-elapsed-drift')
    return reasons


def parse_baikal_options(value):
    """Require the three-field 400:recover:cutoff form. Clock must be 400."""
    if not isinstance(value, string_types):
        return None
    text = to_text(value).strip()
    parts = text.split(':')
    if len(parts) != 3:
        return None
    numbers = []
    for part in parts:
        if not part or part != str(strict_int(part) if False else ''):
            pass
        try:
            if part.startswith('+') or (len(part) > 1 and part.startswith('0')):
                return None
            number = int(part)
        except (TypeError, ValueError):
            return None
        if str(number) != part:
            return None
        numbers.append(number)
    clock, recover, cutoff = numbers
    if clock != PUBLIC_CLOCK_MHZ:
        return None
    if recover < TEMP_MIN or cutoff < TEMP_MIN:
        return None
    if recover > TEMP_SENSOR_MAX or cutoff > TEMP_SENSOR_MAX:
        return None
    if recover >= cutoff:
        return None
    return (clock, recover, cutoff)


def parse_board_clocks(value):
    if value is None:
        return None
    if not isinstance(value, string_types):
        return False
    parts = to_text(value).strip().split(':')
    if len(parts) != 3:
        return False
    for part in parts:
        if part != '400':
            return False
    return True


def parse_board_clock_index(value):
    if isinstance(value, string_types):
        text = to_text(value).strip()
    else:
        return None
    parts = text.split(':')
    if len(parts) != 3:
        return None
    indices = []
    for part in parts:
        number = _decimal_int(part)
        if number is None or number < CLOCK_INDEX_MIN or number > CLOCK_INDEX_MAX:
            return None
        indices.append(number)
    return indices


def _decimal_int(part):
    if not isinstance(part, string_types):
        return None
    text = to_text(part)
    if not text or text.startswith('+') or (len(text) > 1 and text.startswith('0')):
        return None
    for char in text:
        if char < '0' or char > '9':
            return None
    try:
        number = int(text)
    except (TypeError, ValueError):
        return None
    if str(number) != text:
        return None
    return number


def is_experimental_value(value):
    if value is True:
        return True
    if isinstance(value, string_types) and to_text(value).strip().lower() == 'true':
        return True
    return False


def load_json_bytes(raw):
    text = raw.decode('utf-8')
    return json.loads(text, object_pairs_hook=OrderedDict)


def dump_json(obj, pretty=False):
    if pretty:
        text = json.dumps(obj, indent=4, ensure_ascii=False)
    else:
        text = json.dumps(
            obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False
        )
    data = to_bytes(text)
    if pretty and not data.endswith(b'\n'):
        data += b'\n'
    return data


def option_items_ok(items):
    if not isinstance(items, list) or not items:
        return False
    seen = set()
    for item in items:
        if not isinstance(item, dict) or 'key' not in item or 'value' not in item:
            return False
        key = item.get('key')
        if not isinstance(key, string_types):
            return False
        key = to_text(key)
        if not key or key in seen:
            return False
        seen.add(key)
    return True


def value_map(items):
    mapped = OrderedDict()
    for item in items:
        mapped[to_text(item.get('key'))] = item.get('value')
    return mapped


def clone_items(items):
    return [OrderedDict(item) for item in items]


def set_option(items, key, value):
    for item in items:
        if to_text(item.get('key')) == key:
            item['value'] = value
            return items
    items.append(OrderedDict((('key', key), ('value', value))))
    return items


def remove_option(items, key):
    return [item for item in items if to_text(item.get('key')) != key]


def baseline_clock_mode(items):
    mapped = value_map(items)
    mode = mapped.get('baikal-clock-mode', None)
    if isinstance(mode, string_types):
        text = to_text(mode).strip()
        if text in ('global', 'individual'):
            return text
        return None
    if mode is not None:
        return None
    if BOARD_CLOCKS_KEY in mapped:
        return 'individual'
    return 'global'


def backup_problems(items):
    """A startable backup is non-experimental public 400 with the new keys omitted."""
    problems = []
    if not option_items_ok(items):
        return ['backup-invalid']
    mapped = value_map(items)
    parsed = parse_baikal_options(mapped.get(OPTIONS_KEY))
    if parsed is None:
        problems.append('backup-invalid')
    if 'baikal-experimental-clock-index' in mapped or 'baikal-board-clock-index' in mapped:
        problems.append('backup-experimental')
    mode = baseline_clock_mode(items)
    if mode not in ('global', 'individual'):
        problems.append('backup-invalid')
    if BOARD_CLOCKS_KEY in mapped and parse_board_clocks(mapped.get(BOARD_CLOCKS_KEY)) is not True:
        problems.append('backup-invalid')
    return unique(problems)


def backup_is_baseline(items):
    return not backup_problems(items)


def temperature_limit(items, requested_max):
    parsed = parse_baikal_options(value_map(items).get(OPTIONS_KEY))
    if parsed is None:
        return None
    cutoff = parsed[2]
    user_max = DEFAULT_TEMP_MAX if requested_max is None else float(requested_max)
    if not is_finite(user_max) or user_max < TEMP_MIN or user_max > TEMP_SENSOR_MAX:
        return None
    limit = min(user_max, float(cutoff) - TEMP_MARGIN)
    if limit < TEMP_MIN:
        return None
    return limit


def apply_experimental(items, indices):
    updated = clone_items(items)
    set_option(updated, 'baikal-clock-mode', 'individual')
    set_option(updated, 'baikal-experimental-clock-index', True)
    set_option(
        updated,
        'baikal-board-clock-index',
        '%d:%d:%d' % (indices[0], indices[1], indices[2]),
    )
    # A saved global scope rejects unequal indices. This candidate is individual.
    # Restore puts the original scope, or its absence, back with the clock keys.
    set_option(updated, 'blakestream-clock-scope', 'individual')
    return updated


def clock_view(items):
    mapped = value_map(items)
    view = OrderedDict()
    for key in CLOCK_KEYS:
        if key in mapped:
            view[key] = mapped[key]
    return view


def plan_restore(current_items, backup_items):
    """Restore only our clock keys. Keep fan edits and any unrelated keys.

    Exact original bytes are used only when the current file differs solely
    by the clock keys this runner is allowed to change.
    """
    current_map = value_map(current_items)
    backup_map = value_map(backup_items)
    fan_diff = canon(current_map.get(FAN_KEY, MISSING)) != canon(backup_map.get(FAN_KEY, MISSING))
    unrelated = []
    seen = set()
    for key in list(backup_map.keys()) + list(current_map.keys()):
        if key in seen or key == FAN_KEY or key in CLOCK_KEYS:
            seen.add(key)
            continue
        seen.add(key)
        left = current_map.get(key, MISSING)
        right = backup_map.get(key, MISSING)
        if canon(left) != canon(right):
            unrelated.append(key)
    if unrelated or fan_diff:
        merged = clone_items(current_items)
        for key in CLOCK_KEYS:
            if key in backup_map:
                set_option(merged, key, backup_map[key])
            else:
                merged = remove_option(merged, key)
        return {'action': 'merge', 'items': merged, 'unrelated': unrelated, 'fan': fan_diff}
    return {'action': 'original-bytes', 'items': None, 'unrelated': [], 'fan': False}


def clocks_match(current_items, backup_items):
    return canon(clock_view(current_items)) == canon(clock_view(backup_items))


def config_problem(current_items, backup_items, expected_clock):
    if not option_items_ok(current_items):
        return 'config-changed'
    current_map = value_map(current_items)
    backup_map = value_map(backup_items)
    seen = set()
    for key in list(backup_map.keys()) + list(current_map.keys()):
        if key in seen:
            continue
        seen.add(key)
        if key == FAN_KEY:
            continue
        if key in CLOCK_KEYS:
            expected = expected_clock.get(key, MISSING)
            current = current_map.get(key, MISSING)
            if canon(current) != canon(expected):
                return 'config-changed'
            continue
        if canon(current_map.get(key, MISSING)) != canon(backup_map.get(key, MISSING)):
            return 'config-changed'
    return None


def fan_differs(current_items, backup_items):
    current_map = value_map(current_items)
    backup_map = value_map(backup_items)
    return canon(current_map.get(FAN_KEY, MISSING)) != canon(backup_map.get(FAN_KEY, MISSING))


class Leg(object):
    def __init__(self, name, indices, measurement_seconds, warmup_seconds):
        self.name = name
        self.indices = list(indices)
        self.measurement_seconds = measurement_seconds
        self.warmup_seconds = warmup_seconds


class Schedule(object):
    def __init__(self, legs, max_temp, hw_policy='strict', algorithm=None):
        self.legs = legs
        self.max_temp = max_temp
        self.hw_policy = hw_policy
        self.algorithm = algorithm


def algorithm_key(value):
    if not isinstance(value, string_types):
        return None
    key = to_text(value).strip().lower()
    if key == 'blakecoin':
        key = 'blake256r8'
    if not key or len(key) > 32 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in key):
        return None
    return key


def validate_schedule(data, cli_max_temp=None):
    if not isinstance(data, dict):
        raise TrialError('schedule', 'schedule must be a JSON object')
    allowed = set(('legs', 'max_temp', 'hw_policy', 'algorithm'))
    extra = [key for key in data.keys() if key not in allowed]
    if extra:
        raise TrialError('schedule', 'unknown schedule fields: %s' % ', '.join(sorted(map(to_text, extra))))
    hw_policy = data.get('hw_policy', 'strict')
    if hw_policy not in HW_POLICIES:
        raise TrialError('schedule', 'hw_policy must be one of: %s' % ', '.join(HW_POLICIES))
    algorithm = data.get('algorithm')
    if algorithm is not None and (not algorithm_key(algorithm) or algorithm_key(algorithm) != algorithm):
        raise TrialError('schedule', 'algorithm must be a canonical algorithm key')
    if 'max_temp' in data and cli_max_temp is None:
        cli_max_temp = data.get('max_temp')
    if cli_max_temp is not None:
        if isinstance(cli_max_temp, bool) or isinstance(cli_max_temp, string_types):
            raise TrialError('schedule', 'max_temp must be a number')
        number = strict_number(cli_max_temp)
        if number is None or number < TEMP_MIN or number > TEMP_SENSOR_MAX:
            raise TrialError('schedule', 'max_temp must be between 1 and 120')
        max_temp = number
    else:
        max_temp = None
    legs_in = data.get('legs')
    if not isinstance(legs_in, list) or not legs_in:
        raise TrialError('schedule', 'schedule needs at least one leg')
    if len(legs_in) > MAX_LEGS:
        raise TrialError('schedule', 'too many legs')
    legs = []
    names = set()
    total = 0
    leg_keys = set(('name', 'indices', 'measurement_seconds', 'warmup_seconds'))
    for entry in legs_in:
        if not isinstance(entry, dict):
            raise TrialError('schedule', 'each leg must be an object')
        unknown = [key for key in entry.keys() if key not in leg_keys]
        if unknown:
            raise TrialError('schedule', 'unknown leg fields: %s' % ', '.join(sorted(map(to_text, unknown))))
        for key in leg_keys:
            if key not in entry:
                raise TrialError('schedule', 'leg is missing %s' % key)
        name = entry.get('name')
        if not isinstance(name, string_types):
            raise TrialError('schedule', 'leg name must be a string')
        name = to_text(name)
        if len(name) < 1 or len(name) > 64:
            raise TrialError('schedule', 'leg name length is invalid')
        for char in name:
            ok = ('A' <= char <= 'Z') or ('a' <= char <= 'z') or ('0' <= char <= '9') or char in '_.-'
            if not ok:
                raise TrialError('schedule', 'leg name has unsafe characters')
        if name in names:
            raise TrialError('schedule', 'duplicate leg name %s' % name)
        names.add(name)
        raw_indices = entry.get('indices')
        if not isinstance(raw_indices, list) or len(raw_indices) != 3:
            raise TrialError('schedule', 'indices must be three integers')
        indices = []
        for raw in raw_indices:
            number = strict_int(raw)
            if number is None or number < CLOCK_INDEX_MIN or number > CLOCK_INDEX_MAX:
                raise TrialError('schedule', 'each index must be an integer from 8 through 32')
            indices.append(number)
        measure = strict_int(entry.get('measurement_seconds'))
        warmup = strict_int(entry.get('warmup_seconds'))
        if measure is None or measure < MEASURE_MIN or measure > MEASURE_MAX:
            raise TrialError('schedule', 'measurement_seconds must be an integer from 1800 through 3600')
        if warmup is None or warmup < WARMUP_MIN or warmup > WARMUP_MAX:
            raise TrialError('schedule', 'warmup_seconds must be an integer from 120 through 3600')
        total += measure + warmup
        legs.append(Leg(name, indices, measure, warmup))
    if total > MAX_SCHEDULE_SECONDS:
        raise TrialError('schedule', 'schedule duration exceeds the cap')
    return Schedule(legs, max_temp, hw_policy, algorithm)


def format_validation(schedule):
    payload = OrderedDict((
        ('command', 'validate'),
        ('execute', False),
        ('mutation', False),
        ('bridge_artifact_sha256', BRIDGE_ARTIFACT_SHA256),
        ('public_clock_mhz', PUBLIC_CLOCK_MHZ),
        ('baseline_clock_index', BASELINE_CLOCK_INDEX),
        ('indices_are', 'raw clock indices 8..32, not MHz'),
        ('confidence', CONFIDENCE),
        ('max_temp', schedule.max_temp),
        ('hw_policy', schedule.hw_policy),
        ('algorithm', schedule.algorithm),
        ('legs', [
            OrderedDict((
                ('name', leg.name),
                ('indices', leg.indices),
                ('measurement_seconds', leg.measurement_seconds),
                ('warmup_seconds', leg.warmup_seconds),
            ))
            for leg in schedule.legs
        ]),
    ))
    return json.dumps(payload, indent=2, sort_keys=False) + '\n'


def _blank_board(board_id):
    return {
        'id': board_id,
        'alive': None,
        'enabled': None,
        'startup': None,
        'chips': None,
        'temp': None,
        'requested_clock': None,
        'sent_clock_index': None,
        'requested_clock_index': None,
        'experimental': None,
        'command_verified': None,
        'clock_mode': None,
        'algorithm': None,
        'generation': None,
        'pending': None,
        'failover': None,
        'effective_pool': None,
        'host_sha': None,
        'pid': None,
        'start': None,
        'stat_epoch': None,
        'route_epoch': None,
        'enable_epoch': None,
        'accepted': None,
        'rejected': None,
        'hw': None,
        'difficulty': None,
        'elapsed': None,
        'sync': False,
        'usb_tx': None,
        'usb_rx': None,
        'fan': None,
    }


def _index_rows(payload, key):
    rows = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ids = []
        ident = row.get('ID')
        if isinstance(ident, string_types) and to_text(ident).startswith('BKLU'):
            suffix = to_text(ident)[4:]
            ids.append(int(suffix) if suffix in ('0', '1', '2') else None)
        for field in ('DiagBoard', 'ASC'):
            if field in row:
                ids.append(strict_int(row[field]))
        board_id = ids[0] if ids else None
        if any(value != board_id for value in ids):
            for value in ids:
                if value in BOARD_IDS:
                    found[value] = 'duplicate'
            continue
        if board_id not in BOARD_IDS:
            continue
        if board_id in found:
            found[board_id] = 'duplicate'
        else:
            found[board_id] = row
    return found


def _enabled(value):
    if value is True:
        return True
    if isinstance(value, string_types) and to_text(value).strip() == 'Y':
        return True
    if value is False or (isinstance(value, string_types) and to_text(value).strip() == 'N'):
        return False
    return None


def parse_boards(devs_payload, stats_payload):
    devs = _index_rows(devs_payload, 'DEVS')
    stats = _index_rows(stats_payload, 'STATS')
    boards = {}
    structural = []
    for board_id in BOARD_IDS:
        if devs.get(board_id) == 'duplicate' or stats.get(board_id) == 'duplicate':
            structural.append('identity-disagreement')
            continue
        stat = stats.get(board_id)
        dev = devs.get(board_id)
        if not isinstance(stat, dict) or not isinstance(dev, dict):
            structural.append('board-missing')
            continue
        record = _blank_board(board_id)
        status = dev.get('Status')
        record['alive'] = True if isinstance(status, string_types) and to_text(status) == 'Alive' else False
        record['enabled'] = _enabled(dev.get('Enabled'))
        record['startup'] = strict_int(stat.get('StartupSuccessCount'))
        record['chips'] = strict_int(stat.get('Chip Count'))
        if 'Temperature' in stat:
            record['temp'] = strict_number(stat.get('Temperature'))
        elif 'Temperature' in dev:
            record['temp'] = strict_number(dev.get('Temperature'))
        record['requested_clock'] = strict_int(stat.get('RequestedClock'))
        record['sent_clock_index'] = strict_int(stat.get('SentClockIndex'))
        record['requested_clock_index'] = strict_int(stat.get('RequestedClockIndex'))
        record['experimental'] = strict_bool(stat.get('ExperimentalClock')) if 'ExperimentalClock' in stat else None
        record['command_verified'] = strict_bool(stat.get('CommandVerified')) if 'CommandVerified' in stat else None
        mode = stat.get('ClockMode')
        record['clock_mode'] = to_text(mode).strip() if isinstance(mode, string_types) else None
        algo = stat.get('Algo')
        record['algorithm'] = to_text(algo).strip() if isinstance(algo, string_types) else None
        record['generation'] = strict_int(stat.get('ClockGeneration'))
        pending_values = []
        pending_bad = False
        for name in (
            'GlobalClockPending',
            'GlobalClockInflight',
            'ClockRetryPending',
            'ClockReapplyPending',
        ):
            if name not in stat:
                pending_bad = True
                break
            flag = strict_bool(stat.get(name))
            if flag is None:
                pending_bad = True
                break
            pending_values.append(flag)
        record['pending'] = True if pending_bad else any(pending_values)
        if 'Failover Active' in stat:
            record['failover'] = strict_bool(stat.get('Failover Active'))
        elif 'Failover Active' in dev:
            record['failover'] = strict_bool(dev.get('Failover Active'))
        pool = stat.get('Effective Pool', dev.get('Effective Pool'))
        record['effective_pool'] = strict_int(pool)
        record['host_sha'] = normalize_host_sha(stat.get('DiagHostSha256'))
        record['pid'] = strict_int(stat.get('DiagPid'))
        record['start'] = strict_int(stat.get('DiagStartUnix'))
        record['stat_epoch'] = strict_int(stat.get('DiagStatEpoch'))
        record['route_epoch'] = strict_int(stat.get('DiagRouteEpoch'))
        record['enable_epoch'] = strict_int(stat.get('DiagEnableEpoch'))
        share_ok = (
            strict_int(stat.get('DiagShareSnapValid')) == 1
            and strict_int(stat.get('DiagDeviceElapsedValid')) == 1
        )
        record['sync'] = bool(share_ok)
        if share_ok:
            record['accepted'] = strict_int(stat.get('DiagAccepted'))
            record['rejected'] = strict_int(stat.get('DiagRejected'))
            record['hw'] = strict_int(stat.get('DiagHwErrors'))
            record['difficulty'] = strict_number(stat.get('DiagDiffAccepted'))
            record['elapsed'] = strict_number(stat.get('DiagDeviceElapsed'))
        record['usb_tx'] = strict_int(stat.get('DiagUsbTxErrors'))
        record['usb_rx'] = strict_int(stat.get('DiagUsbRxErrors'))
        record['invalid_id'] = strict_int(stat.get('DiagInvalidId'))
        record['fan'] = strict_int(stat.get('FanPercent'))
        boards[board_id] = record
    return {'boards': boards, 'structural': unique(structural)}


def _went_back(new, old):
    if isinstance(new, float) or isinstance(old, float):
        return new < (old - 1e-6)
    return new < old


def assess_boards(parsed, expected, state, now):
    """Return stable reason codes. Empty means this sample may extend the window."""
    reasons = list(parsed.get('structural') or [])
    boards = parsed.get('boards') or {}
    if any(board_id not in boards for board_id in BOARD_IDS):
        reasons.append('board-missing')
        return unique(reasons)
    identity_keys = ('pid', 'start', 'host_sha')
    first = boards[0]
    for board_id in (1, 2):
        for key in identity_keys:
            if boards[board_id][key] != first[key]:
                reasons.append('identity-disagreement')
    phase = expected.get('phase')
    if phase == 'precheck' and len(set(board.get('algorithm') for board in boards.values())) != 1:
        reasons.append('mixed-algorithms')
    locked = state.get('locked') and phase in ('warmup', 'measure')
    budget_policy = expected.get('hw_policy') == 'baseline-budget-v1'
    logged_policy = expected.get('hw_policy') == 'logged-budget-10-v1'
    hw_first = state.get('measure_first') if budget_policy and phase == 'measure' else None
    hw_board_limit, hw_total_limit = (3, 5) if expected.get('measurement_seconds', 0) >= 3600 else (2, 3)
    hw_total = 0
    for board_id in BOARD_IDS:
        board = boards[board_id]
        if board['alive'] is not True:
            reasons.append('not-alive')
        if board['enabled'] is not True:
            reasons.append('not-enabled')
        startup_bad = board['startup'] != STARTUP_REQUIRED or board['chips'] != CHIP_REQUIRED
        previous = state.get('previous', {}).get(board_id)
        if startup_bad:
            dropped = False
            if previous:
                if previous.get('startup') == STARTUP_REQUIRED and (
                    board['startup'] is None or board['startup'] < STARTUP_REQUIRED
                ):
                    dropped = True
                if previous.get('chips') == CHIP_REQUIRED and (
                    board['chips'] is None or board['chips'] < CHIP_REQUIRED
                ):
                    dropped = True
            reasons.append('startup-drop' if dropped else 'startup-count')
        if board['failover'] is not False:
            reasons.append('failover')
        if board['pending'] is not False:
            reasons.append('clock-pending')
        if board['command_verified'] is not True:
            reasons.append('command-not-verified')
        if board['sent_clock_index'] != expected['indices'][board_id]:
            reasons.append('sent-clock-index')
        if board['requested_clock_index'] != expected_requested_clock_index(
                expected['experimental'], expected['indices'][board_id]):
            reasons.append('requested-clock-index')
        if board['experimental'] is not expected['experimental']:
            reasons.append('experimental-clock')
        if board['requested_clock'] != PUBLIC_CLOCK_MHZ:
            reasons.append('requested-clock')
        if board['clock_mode'] != expected['clock_mode']:
            reasons.append('clock-mode')
        algorithms = expected.get('algorithms')
        algorithm_reason = 'algorithm-missing' if not board.get('algorithm') else None
        if algorithms is not None and board.get('algorithm') != algorithms.get(board_id):
            algorithm_reason = algorithm_reason or 'algorithm-changed'
        if expected.get('algorithm') and algorithm_key(board.get('algorithm')) != expected['algorithm']:
            algorithm_reason = algorithm_reason or 'algorithm-mismatch'
        if algorithm_reason:
            # Before the first clock command finishes, the driver can still
            # report its default algorithm. Never accept that as measured work.
            initializing = (phase == 'settle' and board['pending'] is True and
                            board['command_verified'] is False and board['accepted'] == 0)
            reasons.append('algorithm-pending' if initializing else algorithm_reason)
        temp = board['temp']
        if temp is None or temp < TEMP_MIN or temp > TEMP_SENSOR_MAX:
            reasons.append('temperature-missing')
        elif temp >= expected['temp_limit']:
            reasons.append('temperature')
        if board['host_sha'] != expected['host_sha']:
            reasons.append('host-sha')
        for label, value in (('hw', board['hw']), ('usb_tx', board['usb_tx']), ('usb_rx', board['usb_rx']), ('invalid_id', board.get('invalid_id'))):
            if label == 'hw' and value is None and not board['sync']:
                # Before the first synchronized snapshot, absence is not an error count.
                reasons.append('unsynchronized')
                continue
            if value is None or value < 0:
                reasons.append('negative-counter' if value is not None and value < 0 else (
                    'hw-error' if label == 'hw' else 'usb-error'
                ))
            elif label == 'hw' and logged_policy:
                # Every leg restarts the process. Count from that restart so
                # settling/warm-up errors cannot disappear at measurement start.
                hw_total += value
            elif label == 'hw' and hw_first:
                delta = value - hw_first[board_id]['hw']
                if delta < 0:
                    reasons.append('counter-reset')
                elif delta >= hw_board_limit:
                    reasons.append('hw-budget')
                hw_total += max(0, delta)
            elif value != 0:
                reasons.append('hw-error' if label == 'hw' else 'usb-error')
        if not board['sync'] or None in (
            board['accepted'], board['rejected'], board['difficulty'], board['elapsed']
        ):
            reasons.append('unsynchronized')
            continue
        if board['accepted'] < 0 or board['rejected'] < 0 or board['difficulty'] < 0 or board['elapsed'] < 0:
            reasons.append('negative-counter')
            continue
        if (budget_policy or logged_policy) and board['rejected'] != 0:
            reasons.append('nonzero-rejects')
        if phase in ('precheck', 'settle', 'verify') and not locked:
            if board['rejected'] != 0:
                reasons.append('nonzero-rejects')
        if board['pid'] is None or board['start'] is None:
            reasons.append('process-restart')
        if None in (board['stat_epoch'], board['route_epoch'], board['enable_epoch'], board['generation']):
            reasons.append('stat-epoch')
        routes = expected.get('routes')
        if routes is not None and board['effective_pool'] != routes.get(board_id):
            reasons.append('route-changed')
        identity = (state.get('identity') or {}).get(board_id)
        if locked and identity:
            if board['pid'] != identity['pid'] or board['start'] != identity['start']:
                reasons.append('process-restart')
            if board['stat_epoch'] != identity['stat_epoch']:
                reasons.append('stat-epoch')
            if board['route_epoch'] != identity['route_epoch']:
                reasons.append('route-epoch')
            if board['enable_epoch'] != identity['enable_epoch']:
                reasons.append('enable-epoch')
            if board['generation'] != identity['generation']:
                reasons.append('generation-changed')
            if board['host_sha'] != identity['host_sha']:
                reasons.append('host-sha')
            base = state.get('baseline', {}).get(board_id)
            if previous:
                for key in ('accepted', 'rejected', 'hw', 'difficulty', 'elapsed', 'usb_tx', 'usb_rx'):
                    old = previous.get(key)
                    new = board.get(key)
                    if old is None or new is None:
                        continue
                    if _went_back(new, old):
                        reasons.append('counter-reset')
            if base and phase in ('warmup', 'measure'):
                accepted_delta = board['accepted'] - base['accepted']
                rejected_delta = board['rejected'] - base['rejected']
                if accepted_delta < 0 or rejected_delta < 0:
                    reasons.append('counter-reset')
                total = accepted_delta + rejected_delta
                if total >= ENOUGH_SHARES and rejected_delta / float(total) > REJECT_FRACTION_MAX:
                    reasons.append('reject-fraction')
                accept_mono = state.get('accept_mono', {}).get(board_id)
                if accept_mono is not None and (now - accept_mono) > ACCEPT_GAP_SECONDS:
                    reasons.append('loss-of-acceptance')
    if hw_first and hw_total >= hw_total_limit:
        reasons.append('hw-budget-total')
    if logged_policy and hw_total >= LOGGED_HW_LIMIT:
        reasons.append('hw-budget-total')
    return unique(reasons)


def filtered_board(board):
    kept = OrderedDict()
    for key in (
        'id', 'alive', 'enabled', 'startup', 'chips', 'temp', 'requested_clock',
        'sent_clock_index', 'requested_clock_index', 'experimental', 'command_verified',
        'clock_mode', 'algorithm', 'generation', 'pending', 'failover', 'effective_pool', 'host_sha',
        'pid', 'start', 'stat_epoch', 'route_epoch', 'enable_epoch', 'accepted',
        'rejected', 'hw', 'difficulty', 'elapsed', 'sync', 'usb_tx', 'usb_rx', 'invalid_id', 'fan',
    ):
        kept[key] = board.get(key)
    return kept


def call_api(command, host, port, max_bytes=None, max_seconds=None, clock=None):
    if command not in READ_COMMANDS:
        raise TrialAborted('api-command', 'refusing non-read sgminer command')
    if max_bytes is None:
        max_bytes = API_MAX_BYTES
    if max_seconds is None:
        max_seconds = API_MAX_SECONDS
    if clock is None:
        clock = monotonic_seconds
    sock = None
    start = clock()
    stage = 'connect'
    total = 0

    def fail(reason, detail):
        now = clock()
        elapsed = None if start is None or now is None else round(now - start, 3)
        exc = TrialAborted(reason, '%s %s: %s' % (command, stage, detail))
        exc.api_details = {'command': command, 'stage': stage, 'detail': detail,
                           'bytes': total, 'elapsed_seconds': elapsed,
                           'limit_seconds': max_seconds}
        raise exc

    def remaining():
        now = clock()
        if start is None or now is None or now < start:
            fail('api-timeout', 'monotonic clock unavailable')
        budget = max_seconds - (now - start)
        if budget <= 0:
            fail('api-timeout', 'total deadline exceeded')
        return budget

    try:
        sock = socket.socket()
        sock.settimeout(remaining())
        sock.connect((host, int(port)))
        stage = 'send'
        sock.settimeout(remaining())
        sock.sendall(('{"command":"%s"}' % command).encode('ascii'))
        try:
            sock.shutdown(socket.SHUT_WR)
        except socket.error:
            pass
        stage = 'read'
        chunks = []
        while True:
            # One deadline covers connect, send and all reads, including trickles.
            sock.settimeout(remaining())
            chunk = sock.recv(8192)
            remaining()
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                fail('api-timeout', 'response exceeded size cap')
            # sgminer terminates its reply with NUL; socket close is not the frame.
            if b'\x00' in chunk:
                body, trailer = chunk.split(b'\x00', 1)
                if trailer.strip(b'\x00 \t\r\n'):
                    fail('api-invalid', 'unexpected data after reply terminator')
                chunks.append(body)
                break
            chunks.append(chunk)
        raw = b''.join(chunks)
    except TrialAborted:
        raise
    except socket.timeout:
        fail('api-timeout', 'socket deadline exceeded')
    except socket.error:
        fail('api-timeout', 'socket operation failed')
    finally:
        if sock is not None:
            try:
                sock.close()
            except socket.error:
                pass
    stage = 'parse'
    text = raw.decode('utf-8', 'replace').strip()
    try:
        payload = json.loads(text)
    except ValueError:
        fail('api-invalid', 'response was not JSON')
    if not isinstance(payload, dict):
        fail('api-invalid', 'response was not an object')
    return payload


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, IOError):
        try:
            proc.terminate()
        except (OSError, IOError):
            return
    deadline = time.time() + 1.0
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, IOError):
            try:
                proc.kill()
            except (OSError, IOError):
                pass


def execute_restart(path, deps, deadline_seconds):
    """Run the absolute miner-restart.sh once. No shell and no retry."""
    if not os.path.isabs(path) or os.path.islink(path):
        raise TrialAborted('restart-path', 'restart path must be a direct absolute file')
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise TrialAborted('restart-path', 'restart path is not executable')
    with open(os.devnull, 'wb') as quiet:
        proc = subprocess.Popen(
            [path], stdout=quiet, stderr=subprocess.STDOUT,
            close_fds=True, preexec_fn=os.setsid,
        )
    timed_out = False
    deadline = deps.monotonic() + float(deadline_seconds)
    try:
        while proc.poll() is None:
            now = deps.monotonic()
            if now is None or now >= deadline:
                timed_out = True
                break
            deps.sleep(min(0.2, deadline - now))
    finally:
        if proc.poll() is None:
            _kill_group(proc)
        try:
            proc.communicate()
        except (OSError, IOError):
            pass
    if timed_out:
        raise TrialAborted('restart-timeout', 'miner restart exceeded its deadline')
    if proc.returncode != 0:
        raise TrialAborted('restart-failed', 'miner restart exited %s' % proc.returncode)


class FileLock(object):
    def __init__(self, path):
        self.path = path
        self.fd = None

    def acquire(self):
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            try:
                os.makedirs(directory)
            except OSError:
                if not os.path.isdir(directory):
                    raise
            try:
                os.chmod(directory, 0o755)
            except OSError:
                pass
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self.path, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            os.close(self.fd)
            self.fd = None
            raise LockBusy()
        try:
            os.ftruncate(self.fd, 0)
            os.write(self.fd, to_bytes(str(os.getpid())))
        except (IOError, OSError):
            pass
        return self

    def release(self):
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        except (IOError, OSError):
            pass
        try:
            os.close(self.fd)
        except (IOError, OSError):
            pass
        self.fd = None


def _fsync_dir(path):
    flags = os.O_RDONLY
    if hasattr(os, 'O_DIRECTORY'):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except (IOError, OSError):
        return
    try:
        os.fsync(fd)
    except (IOError, OSError):
        pass
    finally:
        os.close(fd)


def atomic_write(path, data, mode, uid, gid):
    """Same-directory replace that keeps the caller's owner and permission bits."""
    if os.path.islink(path):
        raise TrialAborted('symlink', 'refusing to replace a symlink')
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    tmp = os.path.join(
        directory,
        '.bkb-clock-trial.%d.%d.tmp' % (os.getpid(), random.randint(0, 1 << 30)),
    )
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        write_all(fd, data)
        os.fsync(fd)
    except (IOError, OSError):
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    try:
        os.chmod(tmp, mode)
        try:
            os.chown(tmp, uid, gid)
        except (IOError, OSError):
            if os.geteuid() == 0:
                raise
            current = os.stat(tmp)
            if current.st_uid != uid or current.st_gid != gid:
                raise
        os.rename(tmp, path)
        os.chmod(path, mode)
        _fsync_dir(directory)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_private(path, data):
    """Create a new 0600 file. Never overwrite."""
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    _fsync_dir(directory)


def write_all(fd, data):
    offset = 0
    while offset < len(data):
        count = os.write(fd, data[offset:])
        if count <= 0:
            raise IOError('short file write')
        offset += count


def file_meta(path):
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise TrialAborted('symlink', 'refusing to follow a symlink')
    return st


def read_file_bytes(path, max_bytes):
    st = file_meta(path)
    if st.st_size > max_bytes:
        raise TrialAborted('config-unreadable', 'file exceeds size cap')
    with open(path, 'rb') as handle:
        return handle.read()


class Dependencies(object):
    def __init__(self):
        self.monotonic = monotonic_seconds
        self.time = time.time
        self.sleep = time.sleep
        self.euid = os.geteuid
        self.hash_file = sha256_file
        self.api = None
        self.restart = None
        self.stdout = None
        self.stderr = None
        self.max_samples = None
        self.install_signals = True


class Settings(object):
    def __init__(self):
        self.command = 'validate'
        self.schedule_path = None
        self.options_path = DEFAULT_OPTIONS
        self.restart_path = DEFAULT_RESTART
        self.api_host = DEFAULT_API_HOST
        self.api_port = DEFAULT_API_PORT
        self.output_dir = None
        self.host_sha = None
        self.stop_file = None
        self.bridge_file = DEFAULT_BRIDGE
        self.lock_path = DEFAULT_LOCK
        self.max_temp = None
        self.execute = False
        self.backup_path = None
        self.hw_policy = 'strict'


def emit(deps, text, error=False):
    if error:
        stream = deps.stderr if deps is not None and deps.stderr is not None else sys.stderr
    else:
        stream = deps.stdout if deps is not None and deps.stdout is not None else sys.stdout
    stream.write(text)
    if text and not text.endswith('\n'):
        stream.write('\n')
    try:
        stream.flush()
    except (IOError, OSError, AttributeError):
        pass


class Runner(object):
    def __init__(self, deps, settings):
        self.deps = deps
        self.settings = settings
        self.lock = None
        self.mutated = False
        self.restored = False
        self.restore_verified = False
        self.abort_reason = None
        self.failure_reasons = []
        self.output_ready = False
        self.backup_items = None
        self.backup_bytes = None
        self.expected_clock = OrderedDict()
        self.file_mode = 0o640
        self.file_uid = 0
        self.file_gid = 0
        self.routes = None
        self.algorithms = None
        self.temp_limit = None
        self.baseline_mode = None
        self.state = {
            'locked': False,
            'identity': None,
            'previous': {},
            'baseline': {},
            'accept_mono': {},
            'measure_first': None,
            'measure_last': None,
        }
        self.samples_taken = 0
        self.events_written = 0
        self.max_samples = 0
        self.started_mono = None
        self.phase = 'prepare'
        self.phase_end = None
        self.current_leg = None
        self.leg_index = None
        self.legs_report = []
        self.active_leg = None
        self.schedule = None
        self.last_health = {'ok': None, 'reasons': []}
        self._previous_signals = {}
        self.fan_noted = False
        self.recovery_deadline = None
        self.recovery_options = None

    def mono(self):
        now = self.deps.monotonic()
        if now is None:
            raise TrialAborted('clock', 'monotonic clock unavailable')
        return float(now)

    def request_stop(self, signum, _frame=None):
        # Signal handlers may only set state. Restoration happens in the main thread.
        if signum == signal.SIGTERM:
            self.abort_reason = 'SIGTERM'
        else:
            self.abort_reason = 'SIGINT'

    def install_signals(self):
        if not self.deps.install_signals:
            return
        self._previous_signals[signal.SIGTERM] = signal.signal(signal.SIGTERM, self.request_stop)
        self._previous_signals[signal.SIGINT] = signal.signal(signal.SIGINT, self.request_stop)

    def restore_signals(self):
        for signum, previous in self._previous_signals.items():
            signal.signal(signum, previous)
        self._previous_signals = {}

    def _checkpoint(self):
        if self.recovery_deadline is not None:
            if self.mono() > self.recovery_deadline:
                raise TrialAborted('recovery-deadline', 'baseline recovery exceeded its deadline')
            return
        if self.abort_reason:
            raise TrialAborted(self.abort_reason, 'stopped by %s' % self.abort_reason)
        stop = self.settings.stop_file
        if stop and os.path.exists(stop):
            raise TrialAborted('stop-file', 'stop file exists')
        if self.started_mono is not None and self.schedule is not None:
            limit = self._run_deadline()
            if self.mono() > limit:
                raise TrialAborted('deadline', 'trial deadline exceeded')
        if self.max_samples and self.samples_taken > self.max_samples:
            raise TrialAborted('sample-cap', 'sample cap exceeded')

    def _run_deadline(self):
        total = 0
        for leg in self.schedule.legs:
            total += leg.warmup_seconds + leg.measurement_seconds
        slack = (SETTLE_DEADLINE + RESTART_DEADLINE) * (len(self.schedule.legs) + 2)
        slack += RESTORE_DEADLINE
        return self.started_mono + total + slack

    def _sleep(self, seconds):
        if seconds < 0:
            seconds = 0
        try:
            self.deps.sleep(seconds)
        except OSError as exc:
            if getattr(exc, 'errno', None) != errno.EINTR and type(exc).__name__ != 'InterruptedError':
                raise
        self._checkpoint()

    def _api(self, command):
        try:
            if self.deps.api is not None:
                payload = self.deps.api(command)
                if payload is None:
                    raise TrialAborted('api-timeout', 'api returned nothing')
                return payload
            return call_api(
                command,
                self.settings.api_host,
                self.settings.api_port,
                clock=self.deps.monotonic,
            )
        except TrialAborted as exc:
            details = getattr(exc, 'api_details', None)
            if details:
                self._best_effort(self._event, 'api-error', exc.reason, details)
                emit(self.deps, 'bkb-clock-trial API: %s' % dump_json(details, False).decode('ascii'),
                     error=True)
            self._note_health([exc.reason])
            raise

    def _restart(self):
        self._recovery_io(self._event, 'restart', 'miner-restart')
        if self.deps.restart is not None:
            self.deps.restart()
            return
        execute_restart(self.settings.restart_path, self.deps, RESTART_DEADLINE)

    def _read_options(self):
        try:
            raw = read_file_bytes(self.settings.options_path, 1024 * 1024)
            items = load_json_bytes(raw)
        except TrialAborted:
            raise
        except (IOError, OSError, ValueError, UnicodeError):
            raise TrialAborted('config-unreadable', 'miner options are unreadable')
        if not option_items_ok(items):
            raise TrialAborted('config-changed', 'miner options are not a unique key list')
        return items, raw

    def _check_config(self):
        try:
            items, _raw = self._read_options()
        except TrialAborted as exc:
            if exc.reason == 'config-unreadable':
                raise
            raise
        reference = self.recovery_options if self.recovery_options is not None else self.backup_items
        problem = config_problem(items, reference, self.expected_clock)
        if problem:
            raise TrialAborted(problem, 'miner options changed')
        if fan_differs(items, reference) and not self.fan_noted:
            self.fan_noted = True
            self._event('fan-ignored', 'baikal-fan change is outside this runner')
        return items

    def _write_options(self, items):
        data = dump_json(items, pretty=True)
        atomic_write(
            self.settings.options_path,
            data,
            self.file_mode,
            self.file_uid,
            self.file_gid,
        )

    def _event(self, kind, message, details=None):
        if not self.output_ready:
            return
        if self.events_written > 10000 and self.recovery_deadline is None:
            raise TrialAborted('sample-cap', 'event cap exceeded')
        row = OrderedDict((
            ('t_mono', round(self.mono() - (self.started_mono or self.mono()), 3)),
            ('kind', kind),
            ('reason', message),
            ('leg', self.current_leg),
        ))
        if details is not None:
            row['details'] = details
        self._recovery_io(self._append_jsonl, os.path.join(self.settings.output_dir, 'events.jsonl'), row)
        self.events_written += 1

    def _append_jsonl(self, path, row):
        data = dump_json(row, pretty=False)
        if len(data) > 8192:
            raise TrialAborted('sample-cap', 'output record exceeded size cap')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            data += b'\n'
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise IOError('short log write')
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _write_progress(self):
        if not self.output_ready:
            return
        remaining = 0
        elapsed = 0
        if self.started_mono is not None:
            now = self.mono()
            elapsed = max(0.0, now - self.started_mono)
            remaining = self._remaining(now)
        payload = OrderedDict((
            ('state', self.phase),
            ('current_leg', self.current_leg),
            ('leg_index', self.leg_index),
            ('last_health', self.last_health),
            ('elapsed_seconds', round(elapsed, 3)),
            ('remaining_seconds', round(remaining, 3)),
            ('experimental_active', bool(self.mutated and not self.restored)),
            ('samples', self.samples_taken),
        ))
        path = os.path.join(self.settings.output_dir, 'progress.json')
        self._recovery_io(atomic_write, path, dump_json(payload, pretty=True), 0o600, os.geteuid(), os.getegid())

    def _recovery_io(self, operation, *args):
        # Log storage must not prevent returning the miner to baseline.
        try:
            return operation(*args)
        except (IOError, OSError, TrialError):
            if self.recovery_deadline is None:
                raise
            return None

    def _remaining(self, now):
        future = 0
        if self.schedule is None:
            return 0
        if self.leg_index is None:
            for leg in self.schedule.legs:
                future += leg.warmup_seconds + leg.measurement_seconds
            return float(future)
        for index, leg in enumerate(self.schedule.legs):
            if index < self.leg_index:
                continue
            if index > self.leg_index:
                future += leg.warmup_seconds + leg.measurement_seconds
        if self.phase_end is not None and self.phase in ('warmup', 'measure'):
            future += max(0.0, self.phase_end - now)
            if self.phase == 'warmup' and self.active_leg is not None:
                future += self.active_leg.measurement_seconds
        elif self.active_leg is not None and self.phase == 'settling':
            future += self.active_leg.warmup_seconds + self.active_leg.measurement_seconds
        return float(future)

    def _note_health(self, reasons):
        self.last_health = {'ok': not reasons, 'reasons': list(reasons)}
        self._write_progress()

    def _query(self):
        devs = self._api('devs')
        stats = self._api('stats')
        if not isinstance(devs, dict) or not isinstance(stats, dict):
            raise TrialAborted('api-invalid', 'api payload was not an object')
        return parse_boards(devs, stats)

    def _expected(self, indices, experimental, clock_mode, phase):
        return {
            'indices': list(indices),
            'experimental': experimental,
            'clock_mode': clock_mode,
            'host_sha': self.settings.host_sha,
            'temp_limit': self.temp_limit,
            'routes': self.routes,
            'algorithms': self.algorithms,
            'algorithm': self.schedule.algorithm if self.schedule and phase != 'verify' else None,
            'phase': phase,
            'hw_policy': self.schedule.hw_policy if self.schedule else self.settings.hw_policy,
            'measurement_seconds': self.active_leg.measurement_seconds if self.active_leg else 0,
        }

    def _reset_window(self):
        self.state = {
            'locked': False,
            'identity': None,
            'previous': {},
            'baseline': {},
            'accept_mono': {},
            'measure_first': None,
            'measure_last': None,
        }

    def _lock_window(self, parsed, now):
        boards = parsed['boards']
        identity = {}
        for board_id in BOARD_IDS:
            identity[board_id] = dict((key, boards[board_id][key]) for key in (
                'pid', 'start', 'stat_epoch', 'route_epoch', 'enable_epoch', 'generation', 'host_sha'))
        previous = {}
        baseline = {}
        accept_mono = {}
        for board_id in BOARD_IDS:
            snapshot = dict(boards[board_id])
            previous[board_id] = snapshot
            baseline[board_id] = snapshot
            accept_mono[board_id] = now
        self.state = {
            'locked': True,
            'identity': identity,
            'previous': previous,
            'baseline': baseline,
            'accept_mono': accept_mono,
            'measure_first': None,
            'measure_last': None,
        }

    def _remember(self, parsed, phase, now):
        boards = parsed['boards']
        previous = {}
        for board_id in BOARD_IDS:
            previous[board_id] = dict(boards[board_id])
            old = self.state['accept_mono'].get(board_id, now)
            prior = self.state['previous'].get(board_id)
            if prior and boards[board_id]['accepted'] > prior['accepted']:
                self.state['accept_mono'][board_id] = now
            elif board_id not in self.state['accept_mono']:
                self.state['accept_mono'][board_id] = old
        self.state['previous'] = previous
        if phase == 'measure':
            counters = {}
            for board_id in BOARD_IDS:
                board = boards[board_id]
                counters[board_id] = {
                    'algorithm': board['algorithm'],
                    'accepted': board['accepted'],
                    'rejected': board['rejected'],
                    'hw': board['hw'],
                    'difficulty': board['difficulty'],
                    'elapsed': board['elapsed'],
                }
            if self.state['measure_first'] is None:
                self.state['measure_first'] = counters
                self.state['measure_first_mono'] = now
            self.state['measure_last'] = counters
            self.state['measure_last_mono'] = now

    def _persist_sample(self, phase, parsed, reasons):
        self.samples_taken += 1
        if self.max_samples and self.samples_taken > self.max_samples and self.recovery_deadline is None:
            raise TrialAborted('sample-cap', 'sample cap exceeded')
        if not self.output_ready:
            return
        boards = []
        for board_id in BOARD_IDS:
            board = parsed['boards'].get(board_id)
            if board is not None:
                boards.append(filtered_board(board))
        row = OrderedDict((
            ('phase', phase),
            ('leg', self.current_leg),
            ('t_mono', round(self.mono() - self.started_mono, 3)),
            ('hw_policy', self.schedule.hw_policy if self.schedule else self.settings.hw_policy),
            ('reasons', list(reasons)),
            ('boards', boards),
        ))
        self._recovery_io(self._append_jsonl, os.path.join(self.settings.output_dir, 'samples.jsonl'), row)

    def _sample(self, indices, experimental, clock_mode, phase, allow_transient=False):
        self._checkpoint()
        if self.backup_items is not None:
            self._check_config()
        parsed = self._query()
        self._checkpoint()
        if self.backup_items is not None:
            self._check_config()
        now = self.mono()
        expected = self._expected(indices, experimental, clock_mode, phase)
        reasons = assess_boards(parsed, expected, self.state, now)
        self._note_health(reasons)
        self._persist_sample(phase, parsed, reasons)
        if reasons:
            if allow_transient and set(reasons).issubset(TRANSIENT_REASONS):
                return reasons
            self._event('health', reasons[0])
            raise TrialAborted(reasons[0], ', '.join(reasons))
        if phase == 'settle':
            self._lock_window(parsed, now)
        elif phase in ('warmup', 'measure'):
            self._remember(parsed, phase, now)
        return []

    def _poll_until_healthy(self, indices, experimental, clock_mode, phase, deadline_seconds):
        deadline = self.mono() + deadline_seconds
        last = ['settle-timeout']
        while True:
            self._checkpoint()
            try:
                self.phase = phase
                reasons = self._sample(
                    indices, experimental, clock_mode, phase, allow_transient=True
                )
            except TrialAborted as exc:
                if exc.reason != 'api-timeout':
                    raise
                reasons = [exc.reason]
                self._note_health(reasons)
            if not reasons:
                return
            last = reasons
            now = self.mono()
            if now >= deadline:
                raise TrialAborted(last[0], ', '.join(last))
            self._sleep(min(SETTLE_INTERVAL, deadline - now))

    def _phase_loop(self, leg, phase, seconds):
        self.phase = phase
        self._sample(leg.indices, True, 'individual', phase, allow_transient=False)
        # Start after the first counter snapshot; allow integer elapsed quantization.
        end = self.mono() + float(seconds) + 1.0
        self.phase_end = end
        self._write_progress()
        while True:
            now = self.mono()
            if now >= end - 1e-9:
                break
            self._sleep(min(SAMPLE_SECONDS, end - now))
            self._sample(leg.indices, True, 'individual', phase, allow_transient=False)

    def _finish_leg(self, leg):
        first = self.state.get('measure_first')
        last = self.state.get('measure_last')
        report = self._leg_shell(leg, 'failed', ['measurement-short'])
        if not first or not last:
            self._store_leg(report)
            raise TrialAborted('measurement-short', 'measurement window was not sampled')
        reasons = []
        rates = []
        shares = 0
        boards = []
        for board_id in BOARD_IDS:
            start = first[board_id]
            end = last[board_id]
            difficulty_delta = end['difficulty'] - start['difficulty']
            elapsed = end['elapsed'] - start['elapsed']
            accepted_delta = end['accepted'] - start['accepted']
            rejected_delta = end['rejected'] - start['rejected']
            hw_delta = end['hw'] - start['hw']
            mono_span = self.state['measure_last_mono'] - self.state['measure_first_mono']
            reasons.extend(measurement_window_problems(leg.measurement_seconds, mono_span, elapsed))
            if difficulty_delta <= 0:
                reasons.append('difficulty-unchanged')
            rate = accepted_rate_ghs(difficulty_delta, elapsed)
            shares += accepted_delta
            boards.append(OrderedDict((
                ('id', board_id),
                ('index', leg.indices[board_id]),
                ('algorithm', algorithm_key(start['algorithm'])),
                ('reported_algorithm', start['algorithm']),
                ('nominal_mhz', PUBLIC_CLOCK_MHZ if leg.indices[board_id] == BASELINE_CLOCK_INDEX else None),
                ('physical_mhz', None),
                ('frequency_status', 'unverified'),
                ('accepted_delta', accepted_delta),
                ('rejected_delta', rejected_delta),
                ('startup_hw_errors', start['hw']),
                ('hw_errors_delta', hw_delta),
                ('leg_hw_errors', end['hw']),
                ('hw_status', 'errors-observed' if end['hw'] else 'no-errors-observed'),
                ('hw_per_1000_accepted', 1000.0 * hw_delta / accepted_delta if accepted_delta > 0 else None),
                ('difficulty_delta', difficulty_delta),
                ('device_elapsed_seconds', elapsed),
                ('rate_ghs', rate),
                ('exploratory', accepted_delta < EXPLORATORY_SHARES),
                ('approx_share_count_relative_95_pct', 196.0 / math.sqrt(accepted_delta) if accepted_delta > 0 else None),
            )))
            rates.append(rate)
        reasons = unique(reasons)
        status = 'failed' if reasons else 'success'
        report = self._leg_shell(leg, status, reasons)
        report['shares'] = shares
        report['exploratory'] = any(board['exploratory'] for board in boards)
        report['monotonic_seconds'] = mono_span
        report['boards'] = boards
        report['leg_hw_errors_total'] = sum(board['leg_hw_errors'] for board in boards)
        report['quality_flags'] = ['hardware-errors-observed'] if report['leg_hw_errors_total'] else []
        report['rate_ghs'] = rates if status == 'success' else None
        if status != 'success':
            for board in report['boards']:
                board['rate_ghs'] = None
        self._store_leg(report)
        if status != 'success':
            raise TrialAborted(reasons[0], ', '.join(reasons))

    def _leg_shell(self, leg, status, reasons):
        return OrderedDict((
            ('name', leg.name),
            ('status', status),
            ('reasons', list(reasons)),
            ('indices', list(leg.indices)),
            ('measurement_seconds', leg.measurement_seconds),
            ('warmup_seconds', leg.warmup_seconds),
            ('hw_policy', self.schedule.hw_policy),
            ('algorithm', self.schedule.algorithm),
            ('host_sha256', self.settings.host_sha),
            ('bridge_artifact_sha256', BRIDGE_ARTIFACT_SHA256),
            ('shares', None),
            ('exploratory', None),
            ('rate_ghs', None),
            ('boards', []),
            ('confidence', CONFIDENCE),
            ('physical_mhz_claimed', False),
            ('power_measured', False),
        ))

    def _store_leg(self, report):
        replaced = False
        for index, existing in enumerate(self.legs_report):
            if existing.get('name') == report.get('name'):
                self.legs_report[index] = report
                replaced = True
                break
        if not replaced:
            self.legs_report.append(report)
        if self.output_ready:
            path = os.path.join(self.settings.output_dir, 'legs', report['name'] + '.json')
            directory = os.path.dirname(path)
            if not os.path.isdir(directory):
                os.makedirs(directory)
                os.chmod(directory, 0o700)
            atomic_write(path, dump_json(report, pretty=True), 0o600, os.geteuid(), os.getegid())

    def _fail_open_leg(self, reason):
        leg = self.active_leg
        if leg is None:
            return
        reasons = unique(list(self.last_health.get('reasons') or []) + [reason])
        report = self._leg_shell(leg, 'failed', reasons)
        report['rate_ghs'] = None
        self._store_leg(report)

    def _check_bridge(self):
        path = self.settings.bridge_file
        if not path or not os.path.isfile(path) or os.path.islink(path):
            raise TrialAborted('bridge-missing', 'bridge artifact is not a regular file')
        try:
            digest = self.deps.hash_file(path)
        except TrialAborted:
            raise
        except (IOError, OSError):
            raise TrialAborted('bridge-missing', 'bridge artifact is unreadable')
        if to_text(digest).strip().lower() != BRIDGE_ARTIFACT_SHA256:
            raise TrialAborted('bridge-sha', 'bridge artifact hash does not match the fixed trial image')

    def _capture_options(self):
        items, raw = self._read_options()
        problems = backup_problems(items)
        if problems:
            raise TrialAborted(problems[0], 'original options are not a non-experimental 400 baseline')
        limit = temperature_limit(items, self._requested_max())
        if limit is None:
            raise TrialAborted('backup-invalid', 'temperature limit is not usable')
        meta = file_meta(self.settings.options_path)
        self.backup_items = items
        self.backup_bytes = raw
        self.expected_clock = clock_view(items)
        self.file_mode = stat.S_IMODE(meta.st_mode)
        self.file_uid = meta.st_uid
        self.file_gid = meta.st_gid
        self.baseline_mode = baseline_clock_mode(items)
        self.temp_limit = limit
        backup_path = self.settings.backup_path or os.path.join(
            self.settings.output_dir, 'original-miner.options.json'
        )
        self.settings.backup_path = backup_path
        write_private(backup_path, raw)

    def _requested_max(self):
        if self.settings.max_temp is not None:
            return self.settings.max_temp
        if self.schedule is not None:
            return self.schedule.max_temp
        return None

    def _precheck(self):
        self.phase = 'baseline'
        parsed = self._query()
        now = self.mono()
        # Routes are whatever the untouched miner is using. Later samples must match.
        boards = parsed['boards']
        if all(board_id in boards for board_id in BOARD_IDS):
            self.routes = dict(
                (board_id, boards[board_id]['effective_pool']) for board_id in BOARD_IDS
            )
        expected = self._expected(
            [BASELINE_CLOCK_INDEX] * 3, False, self.baseline_mode, 'precheck'
        )
        reasons = assess_boards(parsed, expected, self.state, now)
        self._note_health(reasons)
        self._persist_sample('baseline', parsed, reasons)
        if reasons:
            raise TrialAborted(reasons[0], 'baseline is not the known non-experimental state')
        self.routes = dict(
            (board_id, parsed['boards'][board_id]['effective_pool']) for board_id in BOARD_IDS
        )
        self.algorithms = dict(
            (board_id, parsed['boards'][board_id]['algorithm']) for board_id in BOARD_IDS
        )

    def _prepare_output(self):
        path = self.settings.output_dir
        if not path:
            raise TrialError('usage', 'run --execute requires --output')
        if os.path.lexists(path):
            raise TrialAborted('output-exists', 'refusing to overwrite an output directory')
        if os.path.isabs(path) is False:
            path = os.path.abspath(path)
            self.settings.output_dir = path
        os.makedirs(path)
        os.chmod(path, 0o700)
        self.output_ready = True

    def _sample_budget(self):
        if self.deps.max_samples is not None:
            return int(self.deps.max_samples)
        total = 10
        if self.schedule is not None:
            for leg in self.schedule.legs:
                total += int(leg.warmup_seconds / SAMPLE_SECONDS) + 3
                total += int(leg.measurement_seconds / SAMPLE_SECONDS) + 3
                total += int(SETTLE_DEADLINE / SETTLE_INTERVAL) + 3
        total += int(RESTORE_DEADLINE / SETTLE_INTERVAL) + 5
        return total

    def run(self):
        code = EXIT_OK
        self.install_signals()
        try:
            self._run_body()
        except KeyboardInterrupt:
            self.failure_reasons.append('SIGINT')
            self._best_effort(self._fail_open_leg, 'SIGINT')
            code = EXIT_FAILED
            self._best_effort(self._event, 'abort', 'SIGINT')
        except TrialAborted as exc:
            self.failure_reasons.append(exc.reason)
            self._best_effort(self._fail_open_leg, exc.reason)
            code = self._code_for(exc.reason)
            self._best_effort(self._event, 'abort', exc.reason)
            emit(self.deps, 'bkb-clock-trial: %s' % exc.reason, error=True)
        except Exception as exc:
            self.failure_reasons.append('exception')
            self._best_effort(self._fail_open_leg, 'exception')
            code = EXIT_FAILED
            self._best_effort(self._event, 'abort', 'exception')
            emit(
                self.deps,
                'bkb-clock-trial: exception: %s' % type(exc).__name__,
                error=True,
            )
        finally:
            if self.mutated and not self.restore_verified:
                try:
                    verified = self._restore_to_baseline()
                except Exception:
                    verified = False
                    emit(self.deps, 'bkb-clock-trial: restore-failed', error=True)
                if not verified:
                    code = EXIT_RESTORE
            elif code == EXIT_OK and self.mutated and not self.restore_verified:
                code = EXIT_RESTORE
            self._write_summary(code)
            self._release()
            self.restore_signals()
        return code

    def _best_effort(self, operation, *args):
        try:
            return operation(*args)
        except Exception:
            return None

    def _code_for(self, reason):
        if not self.mutated and reason in ('host-sha', 'bridge-sha', 'bridge-missing'):
            return EXIT_IDENTITY
        if not self.mutated and reason == 'lock-busy':
            return EXIT_LOCK
        if not self.mutated and reason == 'output-exists':
            return EXIT_OUTPUT
        if not self.mutated and reason in ('schedule', 'usage'):
            return EXIT_USAGE
        return EXIT_FAILED

    def _run_body(self):
        if self.deps.euid() != 0:
            raise TrialAborted('not-root', 'run --execute must be root on the miner')
        host = normalize_host_sha(self.settings.host_sha)
        if host is None:
            raise TrialError('usage', '--host-sha must be 64 hex characters')
        self.settings.host_sha = host
        if not self.settings.output_dir:
            raise TrialError('usage', 'run --execute requires --output')
        if os.path.lexists(self.settings.output_dir):
            raise TrialAborted('output-exists', 'refusing to overwrite an output directory')
        self.lock = FileLock(self.settings.lock_path)
        self.lock.acquire()
        self.started_mono = self.mono()
        self._prepare_output()
        self.max_samples = self._sample_budget()
        self._check_bridge()
        self._capture_options()
        self._write_progress()
        self._precheck()
        for index, leg in enumerate(self.schedule.legs):
            self._run_leg(index, leg)
        self.current_leg = None
        self.active_leg = None
        self.phase = 'restoring'

    def _run_leg(self, index, leg):
        self.current_leg = leg.name
        self.leg_index = index
        self.active_leg = leg
        self._reset_window()
        self.phase = 'applying'
        self._check_config()
        items, _raw = self._read_options()
        updated = apply_experimental(items, leg.indices)
        self.expected_clock = clock_view(updated)
        self.mutated = True
        self._write_options(updated)
        written, _raw = self._read_options()
        if config_problem(written, self.backup_items, self.expected_clock):
            raise TrialAborted('config-write-mismatch', 'options did not keep the intended clock keys')
        self._event('leg-start', leg.name)
        self._restart()
        self.phase = 'settling'
        self._poll_until_healthy(leg.indices, True, 'individual', 'settle', SETTLE_DEADLINE)
        self._phase_loop(leg, 'warmup', leg.warmup_seconds)
        self._phase_loop(leg, 'measure', leg.measurement_seconds)
        self._finish_leg(leg)
        self._event('leg-complete', leg.name)

    def _restore_to_baseline(self):
        self.recovery_deadline = self.mono() + RESTART_DEADLINE + RESTORE_DEADLINE + 10
        self.phase = 'restoring'
        self._recovery_io(self._write_progress)
        self._recovery_io(self._event, 'restore-start', self.settings.backup_path or '')
        try:
            current_items, _raw = self._read_options()
        except TrialAborted as exc:
            self.restore_verified = False
            self._recovery_io(self._event, 'restore-failed', exc.reason)
            if exc.reason == 'config-unreadable' and not os.path.exists(self.settings.options_path):
                atomic_write(
                    self.settings.options_path,
                    self.backup_bytes,
                    self.file_mode,
                    self.file_uid,
                    self.file_gid,
                )
            else:
                # An unreadable file is not merged. The private backup remains the recovery copy.
                emit(
                    self.deps,
                    'bkb-clock-trial: restore refused to clobber unreadable options; backup %s'
                    % (self.settings.backup_path or ''),
                    error=True,
                )
                return False
            current_items = self.backup_items
        plan = plan_restore(current_items, self.backup_items)
        self.expected_clock = clock_view(self.backup_items)
        self.routes = None
        self.algorithms = None
        # ExecStopPost and boot recovery are idempotent after a verified finish.
        if clocks_match(current_items, self.backup_items):
            try:
                parsed = self._query()
                expected = self._expected([BASELINE_CLOCK_INDEX] * 3, False, self.baseline_mode, 'verify')
                if not assess_boards(parsed, expected, {}, self.mono()):
                    self.restored = self.restore_verified = True
                    self.phase = 'done'
                    return True
            except (TrialError, IOError, OSError, ValueError):
                pass
        if plan['action'] == 'original-bytes':
            atomic_write(
                self.settings.options_path,
                self.backup_bytes,
                self.file_mode,
                self.file_uid,
                self.file_gid,
            )
        else:
            self._write_options(plan['items'])
            self._event('restore-merge', 'kept unrelated options')
        self.expected_clock = clock_view(self.backup_items)
        self.restored = True
        written, _raw = self._read_options()
        self.recovery_options = written
        if not clocks_match(written, self.backup_items):
            self._event('restore-failed', 'clock-keys')
            return False
        self._restart()
        self._reset_window()
        self.phase = 'verify'
        try:
            self._poll_until_healthy(
                [BASELINE_CLOCK_INDEX] * 3,
                False,
                self.baseline_mode,
                'verify',
                RESTORE_DEADLINE,
            )
        except TrialAborted as exc:
            self._event('restore-failed', exc.reason)
            self.restore_verified = False
            return False
        try:
            confirmed, _raw = self._read_options()
        except TrialAborted:
            self.restore_verified = False
            return False
        if not clocks_match(confirmed, self.backup_items):
            self.restore_verified = False
            self._event('restore-failed', 'clock-keys')
            return False
        self.restore_verified = True
        self.phase = 'done'
        self._event('restore-ok', 'baseline')
        self._write_progress()
        return True

    def _write_summary(self, code):
        if not self.output_ready:
            return
        if code == EXIT_OK and self.mutated and not self.restore_verified:
            code = EXIT_RESTORE
        status = 'success' if code == EXIT_OK else 'failed'
        payload = OrderedDict((
            ('status', status),
            ('exit_code', code),
            ('reasons', list(self.failure_reasons)),
            ('restore_verified', bool(self.restore_verified)),
            ('experimental_left_active', bool(self.mutated and not self.restore_verified)),
            ('bridge_artifact_sha256', BRIDGE_ARTIFACT_SHA256),
            ('host_sha256', self.settings.host_sha),
            ('hw_policy', self.schedule.hw_policy if self.schedule else self.settings.hw_policy),
            ('backup', self.settings.backup_path),
            ('confidence', CONFIDENCE),
            ('physical_mhz_claimed', False),
            ('power_measured', False),
            ('legs', self.legs_report),
            ('last_health', self.last_health),
        ))
        path = os.path.join(self.settings.output_dir, 'summary.json')
        try:
            atomic_write(path, dump_json(payload, pretty=True), 0o600, os.geteuid(), os.getegid())
        except (IOError, OSError, TrialError):
            pass
        self.phase = 'done' if code == EXIT_OK else 'failed'
        try:
            self._write_progress()
        except (IOError, OSError, TrialError):
            pass

    def _release(self):
        if self.lock is not None:
            self.lock.release()
            self.lock = None

    def observe(self):
        host = normalize_host_sha(self.settings.host_sha)
        if host is None:
            raise TrialError('usage', '--host-sha must be 64 hex characters')
        self.settings.host_sha = host
        if self.settings.output_dir and os.path.lexists(self.settings.output_dir):
            raise TrialAborted('output-exists', 'refusing to overwrite an output directory')
        self._check_bridge()
        items, _raw = self._read_options()
        problems = backup_problems(items)
        if problems:
            raise TrialAborted(problems[0], 'options are not a non-experimental 400 baseline')
        self.baseline_mode = baseline_clock_mode(items)
        self.temp_limit = temperature_limit(items, self._requested_max())
        if self.temp_limit is None:
            raise TrialAborted('backup-invalid', 'temperature limit is not usable')
        if self.settings.output_dir:
            self._prepare_output()
            self.started_mono = self.mono()
        parsed = self._query()
        boards = parsed['boards']
        if all(board_id in boards for board_id in BOARD_IDS):
            self.routes = dict(
                (board_id, boards[board_id]['effective_pool']) for board_id in BOARD_IDS
            )
        reasons = assess_boards(
            parsed,
            self._expected([BASELINE_CLOCK_INDEX] * 3, False, self.baseline_mode, 'precheck'),
            self.state,
            self.mono(),
        )
        self._note_health(reasons)
        if self.output_ready:
            self.started_mono = self.started_mono or self.mono()
            self._persist_sample('observe', parsed, reasons)
            self.phase = 'done' if not reasons else 'failed'
            self._write_summary(EXIT_OK if not reasons else EXIT_FAILED)
        report = OrderedDict((
            ('command', 'observe'),
            ('mutation', False),
            ('ok', not reasons),
            ('reasons', reasons),
            ('hw_policy', self.schedule.hw_policy if self.schedule else self.settings.hw_policy),
            ('board_hw_errors', [boards.get(i, {}).get('hw') for i in BOARD_IDS]),
            ('quality_flags', ['hardware-errors-observed'] if any(
                (boards.get(i, {}).get('hw') or 0) > 0 for i in BOARD_IDS) else []),
            ('host_sha256', self.settings.host_sha),
            ('bridge_artifact_sha256', BRIDGE_ARTIFACT_SHA256),
            ('clock_mode', self.baseline_mode),
            ('indices', [BASELINE_CLOCK_INDEX] * 3),
            ('confidence', CONFIDENCE),
        ))
        emit(self.deps, json.dumps(report, indent=2) + '\n')
        return EXIT_OK if not reasons else EXIT_FAILED

    def print_restore_plan(self):
        if not self.settings.backup_path:
            raise TrialError('usage', 'restore requires --backup')
        backup_items, _raw = self._load_backup_file()
        try:
            current_items, _raw = self._read_options()
            plan = plan_restore(current_items, backup_items)
            unrelated = plan['unrelated']
        except TrialAborted as exc:
            plan = {'action': 'refuse', 'unrelated': []}
            unrelated = [exc.reason]
        payload = OrderedDict((
            ('command', 'restore'),
            ('execute', False),
            ('mutation', False),
            ('backup_baseline', backup_is_baseline(backup_items)),
            ('action', plan['action']),
            ('unrelated', unrelated),
            ('clock_keys', list(CLOCK_KEYS)),
        ))
        emit(self.deps, json.dumps(payload, indent=2) + '\n')
        return EXIT_OK if backup_is_baseline(backup_items) else EXIT_FAILED

    def _load_backup_file(self):
        raw = read_file_bytes(self.settings.backup_path, 1024 * 1024)
        items = load_json_bytes(raw)
        problems = backup_problems(items)
        if problems:
            raise TrialAborted(problems[0], 'backup is not a non-experimental 400 baseline')
        return items, raw

    def restore_command(self):
        """Standalone recovery used when the trial process itself is gone."""
        code = EXIT_OK
        self.install_signals()
        try:
            if self.deps.euid() != 0:
                raise TrialAborted('not-root', 'restore --execute must be root on the miner')
            host = normalize_host_sha(self.settings.host_sha)
            if host is None:
                raise TrialError('usage', '--host-sha must be 64 hex characters')
            self.settings.host_sha = host
            if not self.settings.backup_path:
                raise TrialError('usage', 'restore requires --backup')
            self.lock = FileLock(self.settings.lock_path)
            self.lock.acquire()
            self.started_mono = self.mono()
            if self.settings.output_dir and not os.path.lexists(self.settings.output_dir):
                self._prepare_output()
            self._check_bridge()
            items, raw = self._load_backup_file()
            meta = file_meta(self.settings.options_path) if os.path.exists(self.settings.options_path) else None
            self.backup_items = items
            self.backup_bytes = raw
            self.baseline_mode = baseline_clock_mode(items)
            self.temp_limit = temperature_limit(items, self._requested_max())
            if self.temp_limit is None:
                raise TrialAborted('backup-invalid', 'temperature limit is not usable')
            if meta is not None:
                self.file_mode = stat.S_IMODE(meta.st_mode)
                self.file_uid = meta.st_uid
                self.file_gid = meta.st_gid
            else:
                self.file_mode = 0o640
                self.file_uid = os.geteuid()
                self.file_gid = os.getegid()
            self.mutated = True
            verified = self._restore_to_baseline()
            if not verified:
                code = EXIT_RESTORE
        except TrialAborted as exc:
            emit(self.deps, 'bkb-clock-trial: %s' % exc.reason, error=True)
            if exc.reason == 'lock-busy':
                code = EXIT_LOCK
            elif exc.reason == 'not-root':
                code = EXIT_ROOT
            elif exc.reason in ('bridge-sha', 'bridge-missing', 'host-sha'):
                code = EXIT_IDENTITY
            else:
                code = EXIT_RESTORE
        except TrialError as exc:
            emit(self.deps, 'bkb-clock-trial: %s' % exc, error=True)
            code = EXIT_USAGE
        finally:
            self._write_summary(code)
            self._release()
            self.restore_signals()
        return code


def build_parser():
    parser = argparse.ArgumentParser(
        prog='bkb-clock-trial.py',
        description='Validate or run a guarded raw-clock index trial. Default is validate-only.',
    )
    parser.add_argument('command', nargs='?', default='validate')
    parser.add_argument('schedule', nargs='?')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--options', default=DEFAULT_OPTIONS)
    parser.add_argument('--restart', default=DEFAULT_RESTART)
    parser.add_argument('--api-host', default=DEFAULT_API_HOST)
    parser.add_argument('--api-port', type=int, default=DEFAULT_API_PORT)
    parser.add_argument('--output')
    parser.add_argument('--host-sha')
    parser.add_argument('--stop-file')
    parser.add_argument('--bridge-file', default=DEFAULT_BRIDGE)
    parser.add_argument('--lock', default=DEFAULT_LOCK)
    parser.add_argument('--backup')
    parser.add_argument('--max-temp', type=float)
    parser.add_argument('--hw-policy', choices=HW_POLICIES,
                        help='observe/restore only; run takes its policy from the schedule')
    return parser


def settings_from_namespace(ns):
    command = ns.command
    schedule = ns.schedule
    if command not in ('validate', 'observe', 'restore', 'run'):
        if schedule is None:
            schedule = command
            command = 'validate'
        else:
            raise TrialError('usage', 'unknown command')
    settings = Settings()
    settings.command = command
    settings.schedule_path = schedule
    settings.options_path = ns.options
    settings.restart_path = ns.restart
    settings.api_host = ns.api_host
    settings.api_port = ns.api_port
    settings.output_dir = ns.output
    settings.host_sha = ns.host_sha
    settings.stop_file = ns.stop_file
    settings.bridge_file = ns.bridge_file
    settings.lock_path = ns.lock
    settings.max_temp = ns.max_temp
    settings.execute = bool(ns.execute)
    settings.backup_path = ns.backup
    if ns.hw_policy is not None and command not in ('observe', 'restore'):
        raise TrialError('usage', '--hw-policy is only valid for observe or restore')
    settings.hw_policy = ns.hw_policy or 'strict'
    if settings.api_port < 1 or settings.api_port > 65535:
        raise TrialError('usage', 'api port is invalid')
    if not settings.api_host:
        raise TrialError('usage', 'api host is empty')
    return settings


def _read_schedule(path):
    if not path:
        raise TrialError('usage', 'a schedule path is required')
    raw = read_file_bytes(path, SCHEDULE_MAX_BYTES)
    try:
        data = load_json_bytes(raw)
    except (ValueError, UnicodeError):
        raise TrialError('schedule', 'schedule is not JSON')
    return data


def main(argv=None, deps=None):
    if argv is None:
        argv = sys.argv[1:]
    if deps is None:
        deps = Dependencies()
    try:
        ns = build_parser().parse_args(argv)
        settings = settings_from_namespace(ns)
        if settings.command in ('validate', 'run') and not (
            settings.command == 'run' and settings.execute
        ):
            schedule = validate_schedule(_read_schedule(settings.schedule_path), settings.max_temp)
            emit(deps, format_validation(schedule))
            if settings.command == 'run':
                emit(deps, 'bkb-clock-trial: not executed; pass run --execute as root on the miner')
            return EXIT_OK
        if settings.command == 'observe':
            try:
                return Runner(deps, settings).observe()
            except TrialAborted as exc:
                emit(deps, 'bkb-clock-trial: %s' % exc.reason, error=True)
                if exc.reason == 'output-exists':
                    return EXIT_OUTPUT
                if exc.reason in ('bridge-sha', 'bridge-missing', 'host-sha'):
                    return EXIT_IDENTITY
                return EXIT_FAILED
        if settings.command == 'restore':
            runner = Runner(deps, settings)
            if not settings.execute:
                return runner.print_restore_plan()
            return runner.restore_command()
        schedule = validate_schedule(_read_schedule(settings.schedule_path), settings.max_temp)
        if deps.euid() != 0:
            emit(deps, 'bkb-clock-trial: not-root', error=True)
            return EXIT_ROOT
        runner = Runner(deps, settings)
        runner.schedule = schedule
        return runner.run()
    except TrialError as exc:
        emit(deps, 'bkb-clock-trial: %s' % exc, error=True)
        if exc.reason == 'not-root':
            return EXIT_ROOT
        if exc.reason == 'output-exists':
            return EXIT_OUTPUT
        if exc.reason == 'lock-busy':
            return EXIT_LOCK
        return EXIT_USAGE
    except TrialAborted as exc:
        emit(deps, 'bkb-clock-trial: %s' % exc.reason, error=True)
        return EXIT_FAILED


if __name__ == '__main__':
    sys.exit(main())
