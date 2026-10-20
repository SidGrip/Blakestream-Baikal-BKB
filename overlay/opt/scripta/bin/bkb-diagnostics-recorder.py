#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bkb-diagnostics-recorder.py - Phase 1 experiment collector.

Samples cached sgminer stats/devs/pools once a minute. Does not read or
honor the Adjustable Clocks toggle, and does not send USB, clock, pool,
or scheduling commands.

Null means the API did not provide a value. It is not a measured zero.
On-disk firmware hashes are artifact hashes, not chip read-back.

Python 2.7 and Python 3, standard library only.
"""

from __future__ import print_function, division

import hashlib
import json
import math
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from history_json import HistorySample, compact_sample_hook, write_history_json

try:
    string_types = (basestring,)
    text_type = unicode
    binary_type = str
except NameError:
    string_types = (str,)
    text_type = str
    binary_type = bytes

HISTORY_VERSION = 1
STEP_SECONDS = 60
RETENTION_SECONDS = 18 * 3600
MAX_SAMPLES_PER_BOARD = (RETENTION_SECONDS // STEP_SECONDS) + 120
CONTRIB_MAX_BYTES = 16 * 1024 * 1024
MAX_HASH_BYTES = 32 * 1024 * 1024
MAX_BOARDS = 4
READ_COMMANDS = ('stats', 'devs', 'pools')
MAX_API_BYTES = 256 * 1024
MAX_API_SECONDS = 4.0
TOTAL_API_SECONDS = 8.0

PROVENANCE_HOST_RUNNING = 'running-binary-file-sha256'
PROVENANCE_HOST_CONFIGURED = 'configured-path-sha256-not-verified-running'
PROVENANCE_ARTIFACT = 'on-disk-artifact-not-chip-readback'
# sgminer publishes this once from /proc/self/exe. www-data cannot open that inode.
DRIVER_HOST_SHA = 'DiagHostSha256'

# Logical name -> stats/devs key aliases. First present key wins.
# Names that are absent stay null and are listed on the latest snapshot.
API_ALIASES = {
    'startup_success': ('StartupSuccessCount', 'AsicCountR', 'AsicCountSuccess'),
    'chip_count': ('Chip Count', 'AsicCount', 'ChipCount'),
    'asic_version': ('AsicVersion', 'ASICVersion', 'AsicVer'),
    'firmware_version': ('FWV', 'FwVersion', 'FirmwareVersion'),
    'firmware_label': ('FirmwareLabel', 'FwLabel', 'BridgeLabel'),
    'hardware_version': ('HWV', 'HwVersion', 'HardwareVersion'),
    'nonce_grid': ('LocalNoncesRaw', 'NonceGrid', 'LocalNonceGrid', 'DiagNonceGrid'),
    'error_grid': ('LocalErrorsRaw', 'ErrorGrid', 'LocalErrorGrid', 'DiagErrorGrid'),
    'process_pid': ('DiagPid', 'ProcessPid'),
    'process_start': ('DiagStartUnix', 'ProcessStartUnix'),
    'host_sha256': (DRIVER_HOST_SHA,),
    'snap_mono_us': ('DiagSnapMonoUs', 'SnapMonoUs'),
    'stat_epoch': ('DiagStatEpoch', 'StatEpoch'),
    'route_epoch': ('DiagRouteEpoch', 'RouteEpoch'),
    'enable_epoch': ('DiagEnableEpoch', 'EnableEpoch', 'EnablementEpoch'),
    'share_snap_valid': ('DiagShareSnapValid',),
    'device_elapsed_valid': ('DiagDeviceElapsedValid',),
    'clock_mode': ('ClockMode',),
    'requested_clock': ('RequestedClock',),
    'clock_generation': ('ClockGeneration',),
    'verified_generation': ('VerifiedClockGeneration',),
    'command_verified': ('CommandVerified', 'AppliedClockValid'),
    'global_pending': ('GlobalClockPending',),
    'global_inflight': ('GlobalClockInflight',),
    'retry_pending': ('ClockRetryPending',),
    'reapply_pending': ('ClockReapplyPending',),
    'sent_clock_index': ('SentClockIndex',),
    'fan_percent': ('FanPercent',),
    'difficulty_accepted': ('DiagDiffAccepted', 'Difficulty Accepted', 'DifficultyAccepted'),
    'difficulty_rejected': ('DiagDiffRejected', 'Difficulty Rejected', 'DifficultyRejected'),
    'accepted': ('DiagAccepted', 'Accepted'),
    'rejected': ('DiagRejected', 'Rejected'),
    'hardware_errors': ('DiagHwErrors', 'Hardware Errors', 'HardwareErrors'),
    'temperature': ('Temperature',),
    'device_elapsed': ('DiagDeviceElapsed', 'Device Elapsed', 'DeviceElapsed'),
    'enabled': ('Enabled',),
    'status': ('Status',),
    'effective_pool': ('Effective Pool', 'EffectivePool'),
    'failover': ('Failover Active', 'FailoverActive'),
}

DIAG_ALIASES = {
    'invalid_id': ('DiagInvalidId',),
    'invalid_workidx': ('DiagInvalidWorkIdx',),
    'missing_work': ('DiagMissingWork',),
    'algo_mismatch': ('DiagAlgoMismatch',),
    'short_reply': ('DiagShortReply',),
    'reply_mismatch': ('DiagReplyMismatch',),
    'work_requests': ('DiagWorkRequests',),
    'work_sends': ('DiagWorkSends',),
    'skip_overheat': ('DiagSkipOverheat',),
    'skip_no_target': ('DiagSkipNoTarget',),
    'skip_disabled': ('DiagSkipDisabled',),
    'skip_empty': ('DiagSkipEmpty',),
    'usb_tx_error': ('DiagUsbTxErrors', 'DiagUsbTxError'),
    'usb_rx_error': ('DiagUsbRxErrors', 'DiagUsbRxError'),
    'work_wait_us_sum': ('DiagWorkWaitUsSum',),
    'work_wait_us_max': ('DiagWorkWaitUsMax',),
    'work_wait_count': ('DiagWorkWaitCount',),
    'ack_latency_us_sum': ('DiagAckLatencyUsSum',),
    'ack_latency_us_max': ('DiagAckLatencyUsMax',),
    'ack_latency_count': ('DiagAckLatencyCount',),
    'request_to_ack_us_sum': ('DiagRequestToAckUsSum',),
    'request_to_ack_us_max': ('DiagRequestToAckUsMax',),
    'request_to_ack_count': ('DiagRequestToAckCount',),
    'poll_gap_us_max': ('DiagPollGapUsMax',),
    'poll_gap_us_last': ('DiagPollGapUsLast',),
    'attempted_clock_code': ('AttemptedClockCode', 'DiagAttemptedClockCode'),
    'uptime_us': ('DiagUptimeUs',),
    'info_valid': ('DiagInfoValid',),
    'last_info_ok': ('DiagLastInfoOk',),
    'last_result_ok': ('DiagLastResultOk',),
    'trace_enabled': ('DiagTraceEnabled',),
    'trace_live': ('DiagTraceLive',),
    'trace_used': ('DiagTraceUsed',),
    'trace_capacity': ('DiagTraceCapacity',),
    'trace_overwritten': ('DiagTraceOverwritten',),
    'trace_dropped': ('DiagTraceDropped',),
    'trace_recorded': ('DiagTraceRecorded',),
}

# Cumulative counters. poll_gap last and attempted clock code are levels.
DIAG_MONOTONIC = (
    'invalid_id',
    'invalid_workidx',
    'missing_work',
    'algo_mismatch',
    'short_reply',
    'reply_mismatch',
    'work_requests',
    'work_sends',
    'skip_overheat',
    'skip_no_target',
    'skip_disabled',
    'skip_empty',
    'usb_tx_error',
    'usb_rx_error',
    'work_wait_us_sum',
    'work_wait_us_max',
    'work_wait_count',
    'ack_latency_us_sum',
    'ack_latency_us_max',
    'ack_latency_count',
    'request_to_ack_us_sum',
    'request_to_ack_us_max',
    'request_to_ack_count',
    'poll_gap_us_max',
    'uptime_us',
    'trace_overwritten',
    'trace_dropped',
    'trace_recorded',
)

# Recognized stats names whose values are intentionally not copied.
KNOWN_EXTRA_KEYS = (
    'Clock',
    'AppliedClock',
    'ReportedClock',
    'ReportedClockCode',
    'BridgeStatusEcho',
    'BridgeStatusEchoCode',
    'ClockRetryDone',
    'ClockRetryFailures',
    'PendingClockReapply',
    'ClockReapplyFailures',
    'FanPending',
    'PendingFanPercent',
    'Algo',
    'Primary Pool',
    'DiagBoard',
    'DiagTraceExport',
    'DiagCoSnapshot',
    'CountersCoSnapshot',
)

# Diag* share names are used only when both validity flags are 1 and every
# field in the set is present. Otherwise the legacy devs/stats names are used
# and the sample stays unsynchronized. DiagCoSnapshot is not that gate.
SHARE_FIELDS = (
    'accepted',
    'rejected',
    'hardware_errors',
    'difficulty_accepted',
    'difficulty_rejected',
    'device_elapsed',
)
SHARE_DIAG_NAMES = {
    'accepted': ('DiagAccepted',),
    'rejected': ('DiagRejected',),
    'hardware_errors': ('DiagHwErrors',),
    'difficulty_accepted': ('DiagDiffAccepted',),
    'difficulty_rejected': ('DiagDiffRejected',),
    'device_elapsed': ('DiagDeviceElapsed',),
}
SHARE_LEGACY_NAMES = {
    'accepted': ('Accepted',),
    'rejected': ('Rejected',),
    'hardware_errors': ('Hardware Errors', 'HardwareErrors'),
    'difficulty_accepted': ('Difficulty Accepted', 'DifficultyAccepted'),
    'device_elapsed': ('Device Elapsed', 'DeviceElapsed'),
}

CORE_MONOTONIC = ('a', 'da', 'de', 'rj', 'hw', 'dr')
IGNORED_VALUE_KEYS = ('DiagTrace', 'Trace', 'TraceDump', 'CommandTrace')
SENSITIVE_PARTS = ('pass', 'pwd', 'secret', 'token', 'credential')


class HistoryError(Exception):
    pass


def log_err(message):
    sys.stderr.write('bkb-diagnostics: %s\n' % message)


def to_text(value):
    if value is None:
        return None
    if isinstance(value, text_type):
        return value
    if isinstance(value, binary_type):
        return value.decode('utf-8', 'replace')
    return text_type(value)


def is_finite(value):
    return value == value and value not in (float('inf'), float('-inf'))


def parse_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not is_finite(value) or value != math.trunc(value):
            return None
        return int(value)
    if isinstance(value, int) or type(value).__name__ == 'long':
        return int(value)
    if isinstance(value, string_types):
        text = to_text(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return parse_int(number)
    return None


def parse_float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or type(value).__name__ == 'long':
        number = float(value)
        if not is_finite(number):
            return None
        return number
    if isinstance(value, string_types):
        text = to_text(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if not is_finite(number):
            return None
        return number
    return None


def parse_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) or type(value).__name__ == 'long':
        return value != 0
    if isinstance(value, string_types):
        text = to_text(value).strip().lower()
        if text in ('1', 'y', 'yes', 'true', 't'):
            return True
        if text in ('0', 'n', 'no', 'false', 'f'):
            return False
    return None


def bit(value):
    parsed = parse_bool(value)
    if parsed is None:
        return None
    return 1 if parsed else 0


def sensitive_key(key):
    text = to_text(key).lower()
    for part in SENSITIVE_PARTS:
        if part in text:
            return True
    return False


def lookup(row, names):
    """Return (value, present). Null and absent are not present."""
    if not isinstance(row, dict):
        return None, False
    for name in names:
        if name in row and row[name] is not None:
            return row[name], True
    return None, False


def known_stat_keys():
    keys = set(['ID', 'ASC', 'Name'])
    for names in API_ALIASES.values():
        keys.update(names)
    for names in DIAG_ALIASES.values():
        keys.update(names)
    keys.update(IGNORED_VALUE_KEYS)
    keys.update(KNOWN_EXTRA_KEYS)
    return keys


KNOWN_STAT_KEYS = known_stat_keys()


def strip_wrappers(text):
    """Remove the http:// and quota:N; wrappers sgminer stores around stratum URLs."""
    changed = True
    while changed and text:
        changed = False
        lower = text.lower()
        if lower.startswith('https://'):
            text = text[8:]
            changed = True
            continue
        if lower.startswith('http://'):
            text = text[7:]
            changed = True
            continue
        if lower.startswith('quota:'):
            rest = text[6:]
            digits = 0
            while digits < len(rest) and rest[digits].isdigit():
                digits += 1
            if digits and digits < len(rest) and rest[digits] == ';':
                text = rest[digits + 1:]
                changed = True
    return text.strip()


def endpoint_from_url(url):
    if url is None:
        return None
    text = to_text(url).strip()
    if not text:
        return None
    text = strip_wrappers(text)
    text = text.split('#', 1)[0].split('?', 1)[0].strip()
    if '://' in text:
        text = text.split('://', 1)[1]
    text = text.split('/', 1)[0].strip()
    if not text:
        return None
    if text.startswith('['):
        bracket = text.find(']')
        if bracket < 0:
            return None
        hostport = text
    elif '@' in text:
        hostport = text.rsplit('@', 1)[1].strip()
    else:
        hostport = text
    if not hostport or '@' in hostport or '://' in hostport or len(hostport) > 128:
        return None
    return hostport


def userinfo_secrets(url):
    if url is None:
        return []
    text = strip_wrappers(to_text(url).strip())
    text = text.split('#', 1)[0].split('?', 1)[0]
    if '://' in text:
        text = text.split('://', 1)[1]
    authority = text.split('/', 1)[0]
    if '@' not in authority:
        return []
    userinfo = authority.rsplit('@', 1)[0]
    if ':' not in userinfo:
        return []
    password = userinfo.split(':', 1)[1]
    if password:
        return [password]
    return []


def harvest_secrets(pools_resp):
    found = []
    for pool in (pools_resp or {}).get('POOLS', []) if isinstance(pools_resp, dict) else []:
        if not isinstance(pool, dict):
            continue
        found.extend(userinfo_secrets(pool.get('URL')))
        found.extend(userinfo_secrets(pool.get('Stratum URL')))
        for key, value in pool.items():
            if sensitive_key(key) and isinstance(value, string_types):
                text = to_text(value)
                if len(text) >= 4:
                    found.append(text)
    unique = []
    for secret in found:
        if secret not in unique:
            unique.append(secret)
    return unique


def looks_hex_digest(text):
    if len(text) != 64:
        return False
    hexdigits = '0123456789abcdef'
    lowered = text.lower()
    for char in lowered:
        if char not in hexdigits:
            return False
    return True


def lowercase_sha256(value):
    """64 lowercase hex, or None. Empty and uppercase are not a digest."""
    if not isinstance(value, string_types):
        return None
    text = to_text(value)
    if not text or len(text) != 64 or text != text.lower() or not looks_hex_digest(text):
        return None
    return text


def scrub_string(text, secrets):
    if looks_hex_digest(text):
        return text
    for secret in secrets:
        if not secret:
            continue
        if text == secret:
            return ''
        if len(secret) >= 4 and secret in text:
            text = text.replace(secret, '')
    return text


def scrub(obj, secrets):
    if isinstance(obj, dict):
        cleaned = {}
        for key, value in obj.items():
            if sensitive_key(key):
                continue
            key_text = to_text(key)
            if scrub_string(key_text, secrets) != key_text:
                continue
            if key_text == 'pool' and isinstance(value, string_types):
                text = to_text(value)
                if (not text) or text in secrets or '@' in text or '://' in text:
                    cleaned[key] = None
                else:
                    cleaned[key] = text
                continue
            cleaned[key] = scrub(value, secrets)
        return cleaned
    if isinstance(obj, list):
        cleaned = []
        for item in obj:
            value = scrub(item, secrets)
            if value == '':
                continue
            cleaned.append(value)
        return cleaned
    if isinstance(obj, string_types):
        return scrub_string(to_text(obj), secrets)
    return obj


def scrub_sample(sample, secrets):
    """Scrub one new sample. The retained history is not cloned."""
    pool = sample.get('pool')
    cleaned = scrub(sample, secrets)
    if isinstance(pool, string_types):
        text = to_text(pool)
        if (not text) or text in secrets or '@' in text or '://' in text:
            cleaned['pool'] = None
        else:
            cleaned['pool'] = text
    elif pool is None:
        cleaned['pool'] = None
    for key in ('wk', 'fl', 'cm', 'pe', 'pt', 'ps', 'hp', 'sync'):
        value = cleaned.get(key)
        if isinstance(value, string_types) and to_text(value).strip() == '':
            cleaned[key] = None
    return cleaned


def ensure_dir(path):
    if not path or os.path.isdir(path):
        return
    try:
        os.makedirs(path)
    except OSError:
        if not os.path.isdir(path):
            raise


def write_json_atomic(path, obj):
    parent = os.path.dirname(path)
    ensure_dir(parent)
    tmp = '%s.%s.tmp' % (path, os.getpid())
    try:
        with open(tmp, 'w') as handle:
            write_history_json(handle, obj, sort_keys=True, allow_nan=False)
        try:
            os.chmod(tmp, 0o664)
        except OSError:
            pass
        os.rename(tmp, path)
        try:
            os.chmod(path, 0o664)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_json(path, compact=False):
    with open(path, 'rb') as handle:
        text = handle.read().decode('utf-8')
    hook = compact_sample_hook(required=('t', 'a'), nested=('diag',)) if compact else None
    return json.loads(text, object_hook=hook)


def default_paths():
    runtime = os.environ.get('BKB_DIAG_RUNTIME') or '/run/blakestream'
    firmware = os.environ.get('BKB_DIAG_FIRMWARE_DIR') or '/opt/scripta/firmware'
    host = os.environ.get('BKB_DIAG_HOST_PATH') or '/opt/scripta/bin/sgminer'
    return {
        'runtime': runtime,
        'history': os.path.join(runtime, 'bkb-diagnostics-history.json'),
        'latest': os.path.join(runtime, 'bkb-diagnostics-latest.json'),
        'contrib': os.path.join(runtime, 'bkb-diagnostics-contrib'),
        'hash_cache': os.path.join(runtime, 'bkb-diagnostics-hash-cache.json'),
        'host_path': host,
        'firmware_dir': firmware,
    }


def read_bounded(sock, max_bytes=None, max_seconds=None, clock=None):
    """Stop on a total byte cap and a total deadline, not only one recv timeout."""
    if max_bytes is None:
        max_bytes = MAX_API_BYTES
    if max_seconds is None:
        max_seconds = MAX_API_SECONDS
    if clock is None:
        clock = monotonic_seconds
    start = clock()
    if start is None:
        return None
    chunks = []
    total = 0
    while True:
        now = clock()
        if now is None or (now - start) >= max_seconds:
            return None
        remaining = max_seconds - (now - start)
        try:
            sock.settimeout(2.0 if remaining > 2.0 else remaining)
            chunk = sock.recv(8192)
        except Exception:
            return None
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b''.join(chunks)


def call_api(cmd):
    if cmd not in READ_COMMANDS:
        raise ValueError('refusing non-read sgminer command')
    sock = None
    try:
        sock = socket.socket()
        sock.settimeout(MAX_API_SECONDS)
        sock.connect(('127.0.0.1', 4028))
        sock.sendall(('{"command":"%s"}' % cmd).encode('ascii'))
        try:
            sock.shutdown(socket.SHUT_WR)
        except socket.error:
            pass
        data = read_bounded(sock)
    except Exception as exc:
        log_err('cannot query %s: %s' % (cmd, exc))
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    if data is None:
        log_err('query %s exceeded size or time bound' % cmd)
        return None
    text = data.decode('utf-8', 'replace').rstrip('\x00').strip()
    try:
        return json.loads(text)
    except ValueError:
        log_err('cannot parse %s response' % cmd)
        return None


_MONOTONIC_NATIVE = None
_MONOTONIC_TRIED = False


def monotonic_seconds():
    global _MONOTONIC_NATIVE, _MONOTONIC_TRIED
    if hasattr(time, 'monotonic'):
        return float(time.monotonic())
    if not _MONOTONIC_TRIED:
        _MONOTONIC_TRIED = True
        try:
            import ctypes
            class Timespec(ctypes.Structure):
                _fields_ = [('tv_sec', ctypes.c_long), ('tv_nsec', ctypes.c_long)]
            # The controller's Linux ABI supplies librt.so.1. Library discovery
            # shells out on Python 2 and can fail while history occupies RAM.
            librt = ctypes.CDLL('librt.so.1', use_errno=True)
            clock_gettime = librt.clock_gettime
            clock_gettime.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
            clock_gettime.restype = ctypes.c_int

            def native_clock():
                ts = Timespec()
                if clock_gettime(1, ctypes.byref(ts)) == 0:
                    return float(ts.tv_sec) + float(ts.tv_nsec) / 1e9
                return None
            _MONOTONIC_NATIVE = native_clock
        except Exception:
            pass
    if _MONOTONIC_NATIVE is not None:
        value = _MONOTONIC_NATIVE()
        if value is not None:
            return value
    try:
        with open('/proc/uptime', 'rb') as handle:
            return float(handle.read().split()[0])
    except (IOError, OSError, ValueError, IndexError):
        return None


def empty_history(retain):
    return {
        'v': HISTORY_VERSION,
        'step': STEP_SECONDS,
        'retain': retain,
        'updated': 0,
        'host': [],
        'artifacts': [],
        'boards': [],
    }


def normalize_history(data, retain):
    if not isinstance(data, dict):
        raise HistoryError('history is not an object')
    if data.get('v') not in (None, HISTORY_VERSION):
        raise HistoryError('unsupported history version')
    boards = []
    for board in data.get('boards') or []:
        if not isinstance(board, dict):
            continue
        bid = parse_int(board.get('id'))
        if bid is None:
            continue
        samples = []
        for sample in board.get('samples') or []:
            if isinstance(sample, (dict, HistorySample)) and 't' in sample:
                samples.append(sample)
        boards.append({'id': bid, 'samples': samples})
    history = empty_history(retain)
    history['updated'] = data.get('updated') or 0
    history['host'] = data.get('host') if isinstance(data.get('host'), list) else []
    history['artifacts'] = data.get('artifacts') if isinstance(data.get('artifacts'), list) else []
    history['boards'] = boards
    return history


def load_history(path, retain):
    if not os.path.exists(path):
        return empty_history(retain)
    try:
        data = read_json(path, compact=True)
    except (IOError, OSError, ValueError):
        raise HistoryError('unreadable history')
    return normalize_history(data, retain)


def index_stats(stats_resp):
    out = {}
    rows = (stats_resp or {}).get('STATS', []) if isinstance(stats_resp, dict) else []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ident = row.get('ID', '')
        bid = None
        if isinstance(ident, string_types) and to_text(ident).startswith('BKLU'):
            bid = parse_int(to_text(ident)[4:])
        if bid is None and 'ASC' in row:
            bid = parse_int(row.get('ASC'))
        if bid is None:
            continue
        out[bid] = row
    return out


def index_devs(devs_resp):
    out = {}
    rows = (devs_resp or {}).get('DEVS', []) if isinstance(devs_resp, dict) else []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        bid = parse_int(row.get('ASC'))
        if bid is None:
            continue
        name = row.get('Name')
        if isinstance(name, string_types) and to_text(name) not in ('', 'BKLU'):
            continue
        out[bid] = row
    return out


def index_pools(pools_resp):
    out = {}
    rows = (pools_resp or {}).get('POOLS', []) if isinstance(pools_resp, dict) else []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        number = parse_int(row.get('POOL'))
        if number is None:
            continue
        worker = row.get('User')
        worker_text = to_text(worker).strip() if isinstance(worker, string_types) else None
        if worker_text == '':
            worker_text = None
        out[number] = {
            'pool': endpoint_from_url(row.get('URL')),
            'wk': worker_text,
        }
    return out


def parse_grid(value):
    if value is None:
        return None
    numbers = None
    if isinstance(value, string_types):
        text = to_text(value).strip()
        if not text:
            return None
        parts = text.split(',')
        numbers = []
        for part in parts:
            number = parse_int(part.strip())
            if number is None or number < 0:
                return None
            numbers.append(number)
    elif isinstance(value, list):
        if len(value) == 4 and all(isinstance(row, list) for row in value):
            numbers = []
            for row in value:
                for item in row:
                    number = parse_int(item)
                    if number is None or number < 0:
                        return None
                    numbers.append(number)
        else:
            numbers = []
            for item in value:
                number = parse_int(item)
                if number is None or number < 0:
                    return None
                numbers.append(number)
    if numbers is None or len(numbers) != 64:
        return None
    return numbers


def pick(stat, dev, logical, names=None):
    """Prefer the stats row so one stats object wins over a second API call."""
    chosen = API_ALIASES[logical] if names is None else names
    if not chosen:
        return None, None
    value, present = lookup(stat, chosen)
    if present:
        return value, 'stats'
    value, present = lookup(dev, chosen)
    if present:
        return value, 'devs'
    return None, None


def epoch_pid(epoch):
    text = to_text(epoch) if epoch else None
    if not text or ':' not in text:
        return None
    return parse_int(text.split(':', 1)[0])


def parse_whole_or_float(value):
    number = parse_float(value)
    if number is None:
        return None
    if number == math.trunc(number) and abs(number) < 9007199254740992.0:
        return int(number)
    return number


def share_snapshot_ready(stat):
    """Both validity flags and the whole Diag share set, or not a co-snapshot."""
    share_raw, _share_ok = lookup(stat, API_ALIASES['share_snap_valid'])
    elapsed_raw, _elapsed_ok = lookup(stat, API_ALIASES['device_elapsed_valid'])
    if bit(share_raw) != 1 or bit(elapsed_raw) != 1:
        return False
    for logical in SHARE_FIELDS:
        _raw, present = lookup(stat, SHARE_DIAG_NAMES[logical])
        if not present:
            return False
    return True


def ignored_stat_keys(stat):
    ignored = []
    if not isinstance(stat, dict):
        return ignored
    for key in stat.keys():
        if key in KNOWN_STAT_KEYS or sensitive_key(key):
            continue
        ignored.append(to_text(key))
    ignored.sort()
    return ignored[:50]


def build_one(bid, stat, dev, pools, proc, wall, mono):
    missing = []
    bad = []

    def require_missing(logical, present):
        if not present:
            missing.append(logical)

    ss_raw, ss_ok = lookup(stat, API_ALIASES['startup_success'])
    require_missing('startup_success', ss_ok)
    ac_raw, ac_src = pick(stat, dev, 'chip_count')
    require_missing('chip_count', ac_src is not None)
    av_raw, av_ok = lookup(stat, API_ALIASES['asic_version'])
    require_missing('asic_version', av_ok)
    fw_raw, fw_ok = lookup(stat, API_ALIASES['firmware_version'])
    require_missing('firmware_version', fw_ok)
    fl_raw, fl_ok = lookup(stat, API_ALIASES['firmware_label'])
    require_missing('firmware_label', fl_ok)
    hwv_raw, hwv_ok = lookup(stat, API_ALIASES['hardware_version'])
    require_missing('hardware_version', hwv_ok)

    nonce_raw, nonce_ok = lookup(stat, API_ALIASES['nonce_grid'])
    error_raw, error_ok = lookup(stat, API_ALIASES['error_grid'])
    nonce = parse_grid(nonce_raw) if nonce_ok else None
    error = parse_grid(error_raw) if error_ok else None
    if not nonce_ok or (nonce_ok and nonce is None):
        missing.append('nonce_grid')
        nonce = None
    if not error_ok or (error_ok and error is None):
        missing.append('error_grid')
        error = None

    pid, pid_ok = lookup(stat, API_ALIASES['process_pid'])
    start, start_ok = lookup(stat, API_ALIASES['process_start'])
    pe = None
    ps = None
    pt = None
    api_pe = None
    if pid_ok and start_ok and parse_int(pid) is not None and parse_int(start) is not None:
        api_pe = '%s:%s' % (parse_int(pid), parse_int(start))
    proc_pe = stable_proc_epoch(proc)
    # proc epoch is pid plus /proc starttime in clock ticks since boot.
    # api epoch is pid plus DiagStartUnix (time(NULL)). Never compare them.
    disagreed = bool(proc and proc.get('epoch') and proc.get('epoch_after') not in (None, proc.get('epoch')))
    api_pid = parse_int(pid) if pid_ok else None
    proc_pid = None
    if proc and proc.get('pid') is not None:
        proc_pid = parse_int(proc.get('pid'))
    elif proc_pe:
        proc_pid = epoch_pid(proc_pe)
    pid_conflict = api_pid is not None and proc_pid is not None and api_pid != proc_pid
    unstable = bool(proc and proc.get('unstable')) or disagreed
    if unstable:
        pe = None
        pt = None
    elif pid_conflict:
        pe = None
        pt = proc_pe
    elif api_pe:
        pe = api_pe
        ps = 'api'
        pt = proc_pe
    elif proc_pe:
        pe = proc_pe
        ps = (proc.get('source') if proc else None) or 'proc'
        pt = proc_pe
    if pe is None:
        missing.append('process_epoch')

    dms_raw, dms_ok = lookup(stat, API_ALIASES['snap_mono_us'])
    dms = parse_int(dms_raw) if dms_ok else None
    if dms is not None and dms <= 0:
        dms = None
    if dms is None:
        missing.append('snap_mono')

    driver_se, se_ok = lookup(stat, API_ALIASES['stat_epoch'])
    driver_re, re_ok = lookup(stat, API_ALIASES['route_epoch'])
    driver_ee, ee_ok = lookup(stat, API_ALIASES['enable_epoch'])
    if not se_ok:
        missing.append('stat_epoch')
    if not re_ok:
        missing.append('route_epoch')
    if not ee_ok:
        missing.append('enable_epoch')
    _share_raw, share_ok = lookup(stat, API_ALIASES['share_snap_valid'])
    if not share_ok:
        missing.append('share_snap_valid')
    _elapsed_flag_raw, elapsed_flag_ok = lookup(stat, API_ALIASES['device_elapsed_valid'])
    if not elapsed_flag_ok:
        missing.append('device_elapsed_valid')

    cm_raw, cm_ok = lookup(stat, API_ALIASES['clock_mode'])
    rc_raw, rc_ok = lookup(stat, API_ALIASES['requested_clock'])
    cg_raw, cg_ok = lookup(stat, API_ALIASES['clock_generation'])
    vg_raw, vg_ok = lookup(stat, API_ALIASES['verified_generation'])
    cv_raw, cv_ok = lookup(stat, API_ALIASES['command_verified'])
    if not cm_ok:
        missing.append('clock_mode')
    if not rc_ok:
        missing.append('requested_clock')
    if not cg_ok:
        missing.append('clock_generation')
    if not vg_ok:
        missing.append('verified_generation')
    if not cv_ok:
        missing.append('command_verified')

    pending_bits = []
    for logical in ('global_pending', 'retry_pending', 'reapply_pending'):
        raw, present = lookup(stat, API_ALIASES[logical])
        if not present:
            pending_bits.append(None)
        else:
            pending_bits.append(parse_bool(raw))
    if any(item is True for item in pending_bits):
        pend = 1
    elif any(item is None for item in pending_bits):
        pend = None
        missing.append('clock_pending')
    else:
        pend = 0

    inf_raw, inf_ok = lookup(stat, API_ALIASES['global_inflight'])
    inf = bit(inf_raw) if inf_ok else None
    if inf is None:
        missing.append('clock_inflight')
    sc_raw, sc_ok = lookup(stat, API_ALIASES['sent_clock_index'])
    if not sc_ok:
        missing.append('sent_clock_index')

    # A locked share sample is the Diag* set only. Anything else stays on the
    # legacy counters and is not labeled synchronized.
    ready = share_snapshot_ready(stat)
    chosen = SHARE_DIAG_NAMES if ready else SHARE_LEGACY_NAMES
    sync = 'co-snapshot' if ready else 'unsynchronized'
    da_raw, da_src = pick(stat, dev, 'difficulty_accepted', chosen.get('difficulty_accepted'))
    a_raw, a_src = pick(stat, dev, 'accepted', chosen.get('accepted'))
    rj_raw, rj_src = pick(stat, dev, 'rejected', chosen.get('rejected'))
    hw_raw, hw_src = pick(stat, dev, 'hardware_errors', chosen.get('hardware_errors'))
    dr_names = chosen.get('difficulty_rejected')
    if dr_names:
        dr_raw, dr_src = pick(stat, dev, 'difficulty_rejected', dr_names)
    else:
        dr_raw, dr_src = None, None
    tmp_raw, tmp_src = pick(stat, dev, 'temperature')
    de_raw, de_src = pick(stat, dev, 'device_elapsed', chosen.get('device_elapsed'))
    en_raw, en_src = pick(stat, dev, 'enabled')
    st_raw, st_src = pick(stat, dev, 'status')
    ep_raw, ep_src = pick(stat, dev, 'effective_pool')
    fo_raw, fo_src = pick(stat, dev, 'failover')
    fan_raw, fan_src = pick(stat, dev, 'fan_percent')

    for logical, src in (
        ('difficulty_accepted', da_src),
        ('accepted', a_src),
        ('rejected', rj_src),
        ('hardware_errors', hw_src),
        ('temperature', tmp_src),
        ('device_elapsed', de_src),
        ('difficulty_rejected', dr_src),
        ('enabled', en_src),
        ('alive', st_src),
        ('effective_pool', ep_src),
        ('failover', fo_src),
        ('fan', fan_src),
    ):
        if src is None:
            missing.append(logical)

    accepted = parse_int(a_raw) if a_src else None
    if a_src and accepted is None:
        bad.append('accepted')
    difficulty = parse_float(da_raw) if da_src else None
    if da_src and difficulty is None:
        bad.append('difficulty_accepted')

    ep = parse_int(ep_raw) if ep_src else None
    pool_info = pools.get(ep) if ep is not None else None
    pool = pool_info.get('pool') if pool_info else None
    worker = pool_info.get('wk') if pool_info else None
    if pool is None:
        missing.append('pool')
    if worker is None:
        missing.append('worker')

    status_text = to_text(st_raw).strip().lower() if st_src and isinstance(st_raw, string_types) else None
    if status_text == 'alive':
        alive = 1
    elif status_text is None:
        alive = None
    else:
        alive = 0

    label = to_text(fl_raw).strip() if fl_ok and isinstance(fl_raw, string_types) else None
    if label == '':
        label = None
        missing.append('firmware_label')
    mode = to_text(cm_raw).strip() if cm_ok and isinstance(cm_raw, string_types) else None
    if mode == '':
        mode = None

    diag = {}
    for logical, names in DIAG_ALIASES.items():
        raw, present = lookup(stat, names)
        if not present:
            missing.append('diag.' + logical)
            continue
        if logical == 'attempted_clock_code':
            number = parse_int(raw)
        else:
            number = parse_int(raw)
        if number is None or number < 0:
            bad.append('diag.' + logical)
            continue
        diag[logical] = number

    sample = {
        't': wall,
        'tm': mono,
        'dms': dms,
        'pe': pe,
        'pt': pt,
        'ps': ps,
        'de': parse_whole_or_float(de_raw) if de_src else None,
        'a': accepted,
        'da': difficulty,
        'dr': parse_float(dr_raw) if dr_src else None,
        'rj': parse_int(rj_raw) if rj_src else None,
        'hw': parse_int(hw_raw) if hw_src else None,
        'tmp': parse_float(tmp_raw) if tmp_src else None,
        'fan': parse_int(fan_raw) if fan_src else None,
        'al': alive,
        'en': bit(en_raw) if en_src else None,
        'cm': mode,
        'rc': parse_int(rc_raw) if rc_ok else None,
        'cg': parse_int(cg_raw) if cg_ok else None,
        'vg': parse_int(vg_raw) if vg_ok else None,
        'cv': bit(cv_raw) if cv_ok else None,
        'pend': pend,
        'inf': inf,
        'sc': parse_int(sc_raw) if sc_ok else None,
        'ep': ep,
        'fo': bit(fo_raw) if fo_src else None,
        'pool': pool,
        'wk': worker,
        'ss': parse_int(ss_raw) if ss_ok else None,
        'ac': parse_int(ac_raw) if ac_src else None,
        'av': parse_int(av_raw) if av_ok else None,
        'fw': parse_int(fw_raw) if fw_ok else None,
        'hwv': parse_int(hwv_raw) if hwv_ok else None,
        'fl': label,
        'sync': sync,
        'diag': diag,
    }
    if bad:
        sample['bad'] = sorted(set(bad))
    extra = {
        'id': bid,
        'missing': sorted(set(missing)),
        'ignored_stats_keys': ignored_stat_keys(stat),
        'nonce': nonce,
        'error': error,
        'driver_se': parse_int(driver_se) if se_ok else None,
        'driver_re': parse_int(driver_re) if re_ok else None,
        'driver_ee': parse_int(driver_ee) if ee_ok else None,
        'se_bad': se_ok and parse_int(driver_se) is None,
        're_bad': re_ok and parse_int(driver_re) is None,
        'ee_bad': ee_ok and parse_int(driver_ee) is None,
        'se_from_driver': se_ok and parse_int(driver_se) is not None,
        're_from_driver': re_ok and parse_int(driver_re) is not None,
        'ee_from_driver': ee_ok and parse_int(driver_ee) is not None,
    }
    return sample, extra


def decreased(previous, current):
    if isinstance(previous, float) or isinstance(current, float):
        return float(current) < float(previous) - 1e-6
    return int(current) < int(previous)


def counters_decreased(previous, sample):
    if not previous:
        return False
    for key in CORE_MONOTONIC:
        old = previous.get(key)
        new = sample.get(key)
        if old is None or new is None:
            continue
        if decreased(old, new):
            return True
    old_diag = previous.get('diag') or {}
    new_diag = sample.get('diag') or {}
    for key in DIAG_MONOTONIC:
        if key in old_diag and key in new_diag and decreased(old_diag[key], new_diag[key]):
            return True
    return False


def route_key(sample):
    return (sample.get('pool'), sample.get('wk'), sample.get('ep'), sample.get('fo'))


def enable_key(sample):
    return (sample.get('en'), sample.get('al'))


def stable_proc_epoch(proc):
    if not proc or proc.get('unstable'):
        return None
    epoch = proc.get('epoch')
    if not epoch:
        return None
    after = proc.get('epoch_after')
    if after is not None and after != epoch:
        return None
    return to_text(epoch)


def matched_proc_epoch(proc):
    """Tick epoch only when both reads exist and are the same pid:ticks string."""
    if not proc or proc.get('unstable'):
        return None
    if not proc.get('epoch') or proc.get('epoch_after') is None:
        return None
    epoch = to_text(proc.get('epoch'))
    after = to_text(proc.get('epoch_after'))
    if not epoch or epoch != after or epoch_pid(epoch) is None:
        return None
    return epoch


def finish_epochs(sample, extra, previous):
    if extra.get('re_bad'):
        sample['re'] = None
        sample['rs'] = None
    elif extra['re_from_driver']:
        sample['re'] = extra['driver_re']
        sample['rs'] = 'd'
    elif previous and previous.get('rs') == 'd':
        sample['re'] = None
        sample['rs'] = None
    else:
        base = int(previous.get('re')) if previous and previous.get('re') is not None else 0
        if previous and route_key(previous) != route_key(sample):
            base += 1
        sample['re'] = base
        sample['rs'] = 'c'
    if extra.get('ee_bad'):
        sample['ee'] = None
        sample['es'] = None
    elif extra['ee_from_driver']:
        sample['ee'] = extra['driver_ee']
        sample['es'] = 'd'
    elif previous and previous.get('es') == 'd':
        sample['ee'] = None
        sample['es'] = None
    else:
        base = int(previous.get('ee')) if previous and previous.get('ee') is not None else 0
        if previous and enable_key(previous) != enable_key(sample):
            base += 1
        sample['ee'] = base
        sample['es'] = 'c'
    if extra.get('se_bad'):
        sample['se'] = None
        sample['ses'] = None
    elif extra['se_from_driver']:
        sample['se'] = extra['driver_se']
        sample['ses'] = 'd'
    elif previous and previous.get('ses') == 'd':
        sample['se'] = None
        sample['ses'] = None
    else:
        if not previous:
            sample['se'] = 0
        else:
            sample['se'] = int(previous.get('se') or 0)
            if previous.get('pe') != sample.get('pe') or counters_decreased(previous, sample):
                sample['se'] += 1
        sample['ses'] = 'c'
    return sample


def prune_samples(samples, retain, max_samples):
    kept = [sample for sample in samples if isinstance(sample, (dict, HistorySample)) and 't' in sample]
    kept.sort(key=lambda sample: float(sample.get('t') or 0))
    if not kept:
        return kept
    latest = float(kept[-1]['t'])
    cutoff = latest - float(retain)
    kept = [sample for sample in kept if float(sample['t']) >= cutoff]
    if len(kept) > max_samples:
        kept = kept[-max_samples:]
    return kept


def identity_from_stat(st):
    if hasattr(st, 'st_mtime_ns'):
        mtime_us = int(st.st_mtime_ns // 1000)
    else:
        mtime_us = int(round(st.st_mtime * 1000000.0))
    return {
        'dev': int(st.st_dev),
        'ino': int(st.st_ino),
        'size': int(st.st_size),
        'mtime_us': int(mtime_us),
    }


def file_identity(path):
    return identity_from_stat(os.stat(path))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_cache(path):
    if not os.path.exists(path):
        return {'v': 1, 'files': {}}
    try:
        data = read_json(path)
    except (IOError, OSError, ValueError):
        return {'v': 1, 'files': {}}
    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return {'v': 1, 'files': {}}
    data['v'] = 1
    return data


def cached_sha(cache, path, identity):
    row = (cache.get('files') or {}).get(path)
    if not isinstance(row, dict):
        return None
    for key in ('dev', 'ino', 'size', 'mtime_us'):
        if row.get(key) != identity.get(key):
            return None
    sha = row.get('sha256')
    if not isinstance(sha, string_types) or not looks_hex_digest(to_text(sha)):
        return None
    return to_text(sha)


def store_sha(cache, path, identity, sha, provenance):
    files = cache.setdefault('files', {})
    row = {
        'sha256': sha,
        'provenance': provenance,
    }
    row.update(identity)
    files[path] = row


def hash_identity(path, cache, provenance):
    if not path or not os.path.isfile(path):
        return None
    try:
        identity = file_identity(path)
    except OSError:
        return None
    if identity['size'] > MAX_HASH_BYTES or identity['size'] < 0:
        return None
    sha = cached_sha(cache, path, identity)
    if sha is None:
        try:
            sha = sha256_file(path)
        except (IOError, OSError):
            return None
        store_sha(cache, path, identity, sha, provenance)
    return {
        'path': path,
        'sha256': sha,
        'provenance': provenance,
        'identity': identity,
    }


def sha256_fd(fd):
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def hash_proc_exe(pid, cache, expected_epoch):
    """Hash the inode open() sees through /proc/<pid>/exe.

    readlink's pathname can already point at a replacement, or at a path
    marked deleted, while this process is still running the previous inode.
    expected_epoch is the proc starttime in clock ticks (pid:ticks), not
    DiagStartUnix. Those units are not comparable.
    """
    if not expected_epoch or read_proc_epoch(pid) != expected_epoch:
        return None
    link = '/proc/%d/exe' % int(pid)
    try:
        fd = os.open(link, os.O_RDONLY)
    except (IOError, OSError):
        return None
    try:
        if read_proc_epoch(pid) != expected_epoch:
            return None
        try:
            identity = identity_from_stat(os.fstat(fd))
        except (IOError, OSError):
            return None
        if identity['size'] > MAX_HASH_BYTES or identity['size'] < 0:
            return None
        sha = cached_sha(cache, link, identity)
        if sha is None:
            try:
                sha = sha256_fd(fd)
            except (IOError, OSError):
                return None
            if read_proc_epoch(pid) != expected_epoch:
                return None
            try:
                after = identity_from_stat(os.fstat(fd))
            except (IOError, OSError):
                return None
            if (after['dev'], after['ino'], after['size']) != (
                    identity['dev'], identity['ino'], identity['size']):
                return None
            store_sha(cache, link, identity, sha, PROVENANCE_HOST_RUNNING)
        elif read_proc_epoch(pid) != expected_epoch:
            return None
        return {
            'path': link,
            'sha256': sha,
            'provenance': PROVENANCE_HOST_RUNNING,
            'identity': identity,
            'epoch': expected_epoch,
        }
    finally:
        try:
            os.close(fd)
        except (IOError, OSError):
            pass


def driver_running_host(stats_resp, proc):
    """Use DiagHostSha256 only when every board agrees and the proc epoch held.

    The collector runs as www-data and often cannot open root's /proc/PID/exe.
    The host process can hash /proc/self/exe once. That digest is the running
    image only if the driver PID is the stable proc PID, the before/after tick
    epoch matches a fresh /proc read, and hash, PID, and DiagStartUnix are the
    same on every board row. Malformed, empty, missing, or conflicting values
    are not a running hash.
    """
    epoch = matched_proc_epoch(proc)
    if epoch is None or proc.get('pid') is None:
        return None
    pid = parse_int(proc.get('pid'))
    if pid is None or pid != epoch_pid(epoch):
        return None
    if read_proc_epoch(pid) != epoch:
        return None
    indexed = index_stats(stats_resp)
    if not indexed:
        return None
    hashes = []
    pids = []
    starts = []
    for bid in sorted(indexed):
        row = indexed[bid]
        raw_sha, sha_ok = lookup(row, API_ALIASES['host_sha256'])
        digest = lowercase_sha256(raw_sha) if sha_ok else None
        raw_pid, pid_ok = lookup(row, API_ALIASES['process_pid'])
        raw_start, start_ok = lookup(row, API_ALIASES['process_start'])
        board_pid = parse_int(raw_pid) if pid_ok else None
        board_start = parse_int(raw_start) if start_ok else None
        if digest is None or board_pid is None or board_start is None:
            return None
        hashes.append(digest)
        pids.append(board_pid)
        starts.append(board_start)
    if len(set(hashes)) != 1 or len(set(pids)) != 1 or len(set(starts)) != 1:
        return None
    if pids[0] != pid:
        return None
    return {
        'path': None,
        'sha256': hashes[0],
        'provenance': PROVENANCE_HOST_RUNNING,
        'source': DRIVER_HOST_SHA,
        'identity': {
            'kind': 'diag-host-sha256',
            'pid': pids[0],
            'start': starts[0],
            'epoch': epoch,
        },
        'epoch': epoch,
    }


def resolve_host(paths, proc, cache, stats_resp=None):
    # proc['exe'] is intentionally ignored: it is a pathname, not the inode.
    # Open /proc/PID/exe when this user can. www-data usually cannot; the
    # driver digest is the running-image fallback. The configured file is not.
    if proc and proc.get('pid') and not proc.get('unstable'):
        expected = stable_proc_epoch(proc)
        if expected:
            hashed = hash_proc_exe(int(proc['pid']), cache, expected)
            if hashed is not None:
                return hashed
    hashed = driver_running_host(stats_resp, proc)
    if hashed is not None:
        return hashed
    host_path = paths.get('host_path')
    if host_path and os.path.isfile(host_path):
        row = hash_identity(host_path, cache, PROVENANCE_HOST_CONFIGURED)
        if row is not None:
            row['epoch'] = None
        return row
    return None


def resolve_artifacts(paths, cache):
    directory = paths.get('firmware_dir')
    found = []
    if not directory or not os.path.isdir(directory):
        return found
    for name in ('custom.bin', 'stock.bin'):
        path = os.path.join(directory, name)
        row = hash_identity(path, cache, PROVENANCE_ARTIFACT)
        if row is not None:
            found.append(row)
    return found


def upsert_host(history, host, wall):
    if not host:
        return None
    hosts = history.setdefault('host', [])
    for row in hosts:
        if not isinstance(row, dict):
            continue
        if row.get('sha256') == host['sha256'] and row.get('identity') == host['identity']:
            if host['provenance'] == PROVENANCE_HOST_RUNNING:
                row['provenance'] = PROVENANCE_HOST_RUNNING
                row['path'] = host['path']
                if host.get('source'):
                    row['source'] = host['source']
            row['last_t'] = wall
            return row.get('id')
    next_id = 1
    for row in hosts:
        if isinstance(row, dict) and parse_int(row.get('id')) is not None:
            next_id = max(next_id, int(row['id']) + 1)
    recorded = {
        'id': next_id,
        'path': host['path'],
        'sha256': host['sha256'],
        'provenance': host['provenance'],
        'identity': host['identity'],
        'first_t': wall,
        'last_t': wall,
    }
    if host.get('source'):
        recorded['source'] = host['source']
    hosts.append(recorded)
    return next_id


def upsert_artifacts(history, artifacts, wall):
    rows = history.setdefault('artifacts', [])
    for artifact in artifacts:
        matched = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if (row.get('path') == artifact['path'] and
                    row.get('sha256') == artifact['sha256'] and
                    row.get('identity') == artifact['identity']):
                matched = row
                break
        if matched is not None:
            matched['last_t'] = wall
            matched['provenance'] = PROVENANCE_ARTIFACT
            continue
        rows.append({
            'path': artifact['path'],
            'sha256': artifact['sha256'],
            'provenance': PROVENANCE_ARTIFACT,
            'identity': artifact['identity'],
            'not_chip_readback': True,
            'first_t': wall,
            'last_t': wall,
        })


def prune_identity(history, retain):
    referenced = set()
    latest = None
    for board in history.get('boards') or []:
        for sample in board.get('samples') or []:
            if sample.get('hid') is not None:
                referenced.add(sample.get('hid'))
            if sample.get('t') is not None:
                latest = sample['t'] if latest is None else max(latest, sample['t'])
    if latest is None:
        history['host'] = []
        history['artifacts'] = []
        return
    cutoff = float(latest) - float(retain)
    history['host'] = [
        row for row in history.get('host') or []
        if isinstance(row, dict) and row.get('id') in referenced
    ]
    kept = []
    for row in history.get('artifacts') or []:
        if not isinstance(row, dict):
            continue
        if row.get('provenance') != PROVENANCE_ARTIFACT:
            continue
        if float(row.get('last_t') or 0) < cutoff:
            continue
        row['not_chip_readback'] = True
        kept.append(row)
    history['artifacts'] = kept


def board_list(history):
    boards = {}
    for board in history.get('boards') or []:
        if isinstance(board, dict) and board.get('id') is not None:
            boards[int(board['id'])] = board
    return boards


def append_jsonl(path, obj):
    ensure_dir(os.path.dirname(path))
    text = json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)
    data = (text + '\n').encode('utf-8')
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, 'O_CLOEXEC'):
        flags |= os.O_CLOEXEC
    fd = os.open(path, flags, 0o664)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass


def segment_name(wall):
    return 'c-%010d.jsonl' % int(float(wall) // 3600)


def prune_contrib(directory, latest, retain):
    if not os.path.isdir(directory):
        return
    cutoff = float(latest) - float(retain)
    files = []
    total = 0
    for name in os.listdir(directory):
        if not name.startswith('c-') or not name.endswith('.jsonl'):
            continue
        try:
            segment = int(name[2:-6])
        except ValueError:
            continue
        path = os.path.join(directory, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if (segment + 1) * 3600 <= cutoff:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        files.append((segment, size, path))
        total += size
    files.sort()
    while total > CONTRIB_MAX_BYTES and len(files) > 1:
        _segment, size, path = files.pop(0)
        try:
            os.unlink(path)
        except OSError:
            continue
        total -= size


def build_latest(wall, mono, host, artifacts, rows):
    boards = []
    for sample, extra in rows:
        boards.append({
            'id': extra['id'],
            'sample': sample,
            'grid': {'nonce': extra['nonce'], 'error': extra['error']},
            'missing': extra['missing'],
            'ignored_stats_keys': extra['ignored_stats_keys'],
        })
    return {
        'v': HISTORY_VERSION,
        'updated': wall,
        'mono': mono,
        'host': host,
        'artifacts': artifacts,
        'boards': boards,
    }


def record_once(snapshot, paths, wall, mono, proc=None, retain_seconds=None, max_samples=None):
    retain = RETENTION_SECONDS if retain_seconds is None else int(retain_seconds)
    limit = MAX_SAMPLES_PER_BOARD if max_samples is None else int(max_samples)
    cache = load_cache(paths['hash_cache'])
    host = resolve_host(paths, proc, cache, snapshot.get('stats'))
    artifacts = resolve_artifacts(paths, cache)
    secrets = harvest_secrets(snapshot.get('pools'))
    stats_by = index_stats(snapshot.get('stats'))
    devs_by = index_devs(snapshot.get('devs'))
    pools = index_pools(snapshot.get('pools'))
    ids = []
    for bid in list(stats_by.keys()) + list(devs_by.keys()):
        if bid not in ids and 0 <= bid <= MAX_BOARDS:
            ids.append(bid)
    ids.sort()
    if not ids:
        return None
    try:
        history = load_history(paths['history'], retain)
    except HistoryError as exc:
        log_err('%s; history left unchanged' % exc)
        return None
    hid = upsert_host(history, host, wall)
    previous = {}
    for board in history['boards']:
        samples = board.get('samples') or []
        previous[board['id']] = samples[-1] if samples else None
    rows = []
    for bid in ids:
        sample, extra = build_one(
            bid, stats_by.get(bid) or {}, devs_by.get(bid) or {}, pools, proc, wall, mono)
        sample['hid'] = hid
        sample['hp'] = host.get('provenance') if host else None
        finish_epochs(sample, extra, previous.get(bid))
        sample = scrub_sample(sample, secrets)
        extra['ignored_stats_keys'] = scrub(extra.get('ignored_stats_keys') or [], secrets)
        rows.append((sample, extra))
    boards = board_list(history)
    for sample, extra in rows:
        board = boards.get(extra['id'])
        if board is None:
            board = {'id': extra['id'], 'samples': []}
            boards[extra['id']] = board
        board['samples'].append(sample)
        board['samples'] = prune_samples(board['samples'], retain, limit)
    history['boards'] = [boards[bid] for bid in sorted(boards.keys())]
    history['v'] = HISTORY_VERSION
    history['step'] = STEP_SECONDS
    history['retain'] = retain
    history['updated'] = wall
    upsert_artifacts(history, artifacts, wall)
    prune_identity(history, retain)
    latest_artifacts = []
    for artifact in artifacts:
        item = dict(artifact)
        item['not_chip_readback'] = True
        item['provenance'] = PROVENANCE_ARTIFACT
        latest_artifacts.append(item)
    latest_host = None
    if host:
        latest_host = dict(host)
    latest = scrub(build_latest(wall, mono, latest_host, latest_artifacts, rows), secrets)
    write_json_atomic(paths['history'], history)
    write_json_atomic(paths['latest'], latest)
    for sample, extra in rows:
        append_jsonl(
            os.path.join(paths['contrib'], segment_name(wall)),
            scrub({
                't': wall,
                'id': extra['id'],
                'n': extra['nonce'],
                'e': extra['error'],
            }, secrets))
    prune_contrib(paths['contrib'], wall, retain)
    write_json_atomic(paths['hash_cache'], cache)
    return {'history': history, 'latest': latest}


def read_proc_epoch(pid):
    path = '/proc/%s/stat' % int(pid)
    try:
        with open(path, 'rb') as handle:
            data = handle.read().decode('utf-8', 'replace')
    except (IOError, OSError):
        return None
    mark = data.rfind(')')
    if mark < 0:
        return None
    fields = data[mark + 2:].split()
    if len(fields) < 20:
        return None
    return '%s:%s' % (int(pid), fields[19])


def find_sgminer_pid():
    try:
        names = os.listdir('/proc')
    except OSError:
        return None
    for name in names:
        if not name.isdigit():
            continue
        path = '/proc/%s/cmdline' % name
        try:
            with open(path, 'rb') as handle:
                raw = handle.read(4096)
        except (IOError, OSError):
            continue
        argv0 = raw.split(b'\x00', 1)[0].decode('utf-8', 'replace')
        if argv0.endswith('/sgminer') or argv0 == 'sgminer':
            return int(name)
    return None


def discover_process():
    pid = find_sgminer_pid()
    if pid is None:
        return None
    return {
        'pid': pid,
        'epoch': read_proc_epoch(pid),
        'source': 'proc',
    }


def main(argv=None):
    del argv
    paths = default_paths()
    started = monotonic_seconds()
    if started is None:
        log_err('monotonic clock unavailable; sample skipped')
        return 0
    proc = discover_process()
    if proc and proc.get('pid'):
        proc['epoch'] = read_proc_epoch(proc['pid'])
    stats = call_api('stats')
    devs = call_api('devs')
    pools = call_api('pools')
    finished = monotonic_seconds()
    if finished is None or (finished - started) > TOTAL_API_SECONDS:
        log_err('api snapshot exceeded %ss; sample skipped' % TOTAL_API_SECONDS)
        return 0
    if proc and proc.get('pid'):
        proc['epoch_after'] = read_proc_epoch(proc['pid'])
        if not proc.get('epoch') or proc.get('epoch') != proc.get('epoch_after'):
            proc['unstable'] = True
    if not isinstance(devs, dict) or not devs.get('DEVS'):
        return 0
    record_once(
        {'stats': stats, 'devs': devs, 'pools': pools},
        paths, time.time(), finished, proc=proc)
    return 0


if __name__ == '__main__':
    sys.exit(main())
