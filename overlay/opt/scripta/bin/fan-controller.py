#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fan-controller.py - BK-B automatic fan controller.

When Settings -> Auto Fan Control is enabled, polls sgminer's live board
temperatures and queues a global fan target through sgminer. sgminer applies
the queued fan update from the Baikal polling path so the web/API thread never
performs synchronous USB fan writes. temp-watchdog.py remains the hard
over-temperature disable/recover layer.

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import errno
import json
import os
import socket
import sys
import tempfile
import time

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)

ETC = '/opt/scripta/etc'
RUNTIME_DIR = '/run/blakestream-control'
SCRIPTA_CONF = os.path.join(ETC, 'scripta.conf')
MINER_OPTIONS = os.path.join(ETC, 'miner.options.json')
STATE = os.path.join(RUNTIME_DIR, 'fan-control-state.json')
LOCK = os.path.join(RUNTIME_DIR, 'fan-controller.lock')
DEFAULT_API_PORT = 4028
LIVE_FAN_API_ENABLED = True
LIVE_FAN_API_DISABLED_REASON = (
    'live fan PWM disabled; saved fan value applies on miner restart'
)

DEFAULTS = {
    'enabled': False,
    'min_fan': 40,
    'target_temp': 36.0,
    'mid_temp': None,
    'mid_fan': None,
    'full_temp': 50.0,
    'emergency_temp': 60.0,
    'step_percent': 5,
    'max_down_step': 10,
}


def load_json(path, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (IOError, ValueError):
        return default


def write_json_atomic(path, data):
    parent = os.path.dirname(path)
    if not parent or os.path.islink(parent) or not os.path.isdir(parent):
        raise IOError(errno.EPERM, 'unsafe control runtime directory')
    fd, tmp = tempfile.mkstemp(prefix='.fan-state.', dir=parent)
    try:
        with os.fdopen(fd, 'w') as f:
            fd = -1
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
            os.fchown(f.fileno(), 0, 33)
            os.fchmod(f.fileno(), 0o640)
        os.rename(tmp, path)
        tmp = None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def int_or_default(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def float_or_default(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def acquire_lock():
    if os.path.islink(RUNTIME_DIR) or not os.path.isdir(RUNTIME_DIR):
        raise IOError(errno.EPERM, 'unsafe control runtime directory')
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.write(fd, str(os.getpid()).encode('ascii'))
        os.close(fd)
        return True
    except OSError as e:
        if e.errno == errno.EEXIST:
            try:
                if time.time() - os.path.getmtime(LOCK) > 120:
                    try:
                        with open(LOCK, 'r') as f:
                            old_pid = int(f.read().strip())
                        if old_pid != os.getpid():
                            try:
                                os.kill(old_pid, 0)
                                return False
                            except OSError as kill_error:
                                if kill_error.errno != errno.ESRCH:
                                    return False
                    except (IOError, ValueError):
                        pass
                    os.unlink(LOCK)
                    return acquire_lock()
            except OSError:
                pass
            return False
        raise


def release_lock():
    try:
        os.unlink(LOCK)
    except OSError:
        pass


def miner_option_value(options, key, default=''):
    if not isinstance(options, list):
        return default
    for opt in options:
        if isinstance(opt, dict) and opt.get('key') == key:
            return opt.get('value', default)
    return default


def parse_baikal_options(value):
    recover = 40
    cutoff = 55
    parts = str(value or '').split(':')
    if len(parts) >= 3:
        recover = int_or_default(parts[1], recover)
        cutoff = int_or_default(parts[2], cutoff)
    elif len(parts) >= 2 and str(value or ''):
        recover = int_or_default(parts[0], recover)
        cutoff = int_or_default(parts[1], cutoff)
    recover = clamp(recover, 1, 99)
    cutoff = clamp(cutoff, 1, 99)
    if recover >= cutoff:
        recover = 40
        cutoff = 55
    return recover, cutoff


def default_mid_temp(target, full):
    return target + ((full - target) / 2.0)


def default_mid_fan(min_fan):
    return clamp(75, min_fan, 100)


def normalize_midpoint(out, cfg):
    target = out['target_temp']
    full = out['full_temp']
    min_fan = out['min_fan']
    default_temp = default_mid_temp(target, full)
    mid_temp = float_or_default(cfg.get('userAutoFanMidTemp'), default_temp)

    # Keep the extra point inside the ramp. If the span is narrow, fall back to
    # the midpoint instead of creating a zero-width interpolation segment.
    low = target + 1.0
    high = full - 1.0
    if low <= high:
        mid_temp = clamp(mid_temp, low, high)
    else:
        mid_temp = max(20.0, default_temp)

    default_fan = default_mid_fan(min_fan)
    mid_fan = clamp(int_or_default(cfg.get('userAutoFanMidFan'), default_fan), min_fan, 100)

    out['mid_temp'] = mid_temp
    out['mid_fan'] = mid_fan
    return out


def load_config():
    cfg = load_json(SCRIPTA_CONF, {})
    options = load_json(MINER_OPTIONS, [])
    manual_fan = clamp(int_or_default(miner_option_value(options, 'baikal-fan', 70), 70), 40, 100)
    _recover, cutoff = parse_baikal_options(miner_option_value(options, 'baikal-options', '400:40:55'))

    out = DEFAULTS.copy()
    out['request_revision'] = cfg.get('userAutoFanRevision', '')
    out['requested_at'] = int_or_default(cfg.get('userAutoFanRequestedAt'), 0)
    out['enabled'] = bool(cfg.get('userAutoFan', False))
    out['min_fan'] = clamp(int_or_default(cfg.get('userAutoFanMin'), out['min_fan']), 20, 100)
    out['target_temp'] = clamp(float_or_default(cfg.get('userAutoFanTarget'), out['target_temp']), 20.0, 120.0)
    out['full_temp'] = float_or_default(cfg.get('userAutoFanFull'), out['full_temp'])
    out['emergency_temp'] = float_or_default(cfg.get('userAutoFanEmergency'), out['emergency_temp'])
    out['manual_fan'] = manual_fan
    out['cutoff_temp'] = cutoff

    if out['full_temp'] <= out['target_temp']:
        out['full_temp'] = out['target_temp'] + 5.0
    # Do not wait past the configured Baikal cutoff before reaching full fan.
    if out['full_temp'] > cutoff:
        out['full_temp'] = float(cutoff)
    if out['full_temp'] <= out['target_temp']:
        out['target_temp'] = max(20.0, out['full_temp'] - 5.0)
    if out['emergency_temp'] <= out['full_temp']:
        out['emergency_temp'] = out['full_temp'] + 5.0
    out = normalize_midpoint(out, cfg)
    out['step_percent'] = clamp(int_or_default(cfg.get('userAutoFanStep'), out['step_percent']), 1, 25)
    out['max_down_step'] = clamp(int_or_default(cfg.get('userAutoFanMaxDownStep'), out['max_down_step']), 1, 25)
    return out


def call_api(command, parameter=None, timeout=4):
    payload = {'command': command}
    if parameter is not None:
        payload['parameter'] = parameter
    raw = json.dumps(payload).encode('ascii')
    s = None
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(('127.0.0.1', DEFAULT_API_PORT))
        s.sendall(raw)
        try:
            s.shutdown(socket.SHUT_WR)
        except socket.error:
            pass
        data = b''
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
    except Exception as e:
        return None, str(e)
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    text = data.decode('utf-8', 'replace').rstrip('\x00').strip()
    try:
        return json.loads(text), None
    except ValueError:
        return text, None


def api_status_ok(resp):
    if resp is None:
        return False
    if isinstance(resp, dict):
        rows = resp.get('STATUS') or []
        if rows and isinstance(rows, list):
            status = rows[0].get('STATUS', '')
            code = int_or_default(rows[0].get('Code'), 0)
            return status != 'E' and code != 14
        return True
    text = str(resp)
    return 'STATUS=E' not in text and 'set OK' in text


def query_devs():
    resp, err = call_api('devs')
    if err:
        return None, err
    if not isinstance(resp, dict):
        return None, 'unexpected devs response'
    devs = resp.get('DEVS') or []
    out = []
    for dev in devs:
        try:
            bid = int(dev.get('ASC'))
            temp = float(dev.get('Temperature'))
        except (TypeError, ValueError):
            return None, 'missing board temperature'
        out.append({'id': bid, 'temp': temp})
    if not out:
        return None, 'no boards in devs response'
    return out, None


def query_current_fan(default_fan):
    resp, err = call_api('stats')
    if err or not isinstance(resp, dict):
        return default_fan
    for row in resp.get('STATS', []) or []:
        ident = row.get('ID', '')
        if isinstance(ident, string_types) and ident.startswith('BKLU') and 'FanPercent' in row:
            return clamp(int_or_default(row.get('FanPercent'), default_fan), 0, 100)
    return default_fan


def interpolate_fan(temp, low_temp, high_temp, low_fan, high_fan):
    if high_temp <= low_temp:
        return high_fan
    pct = (temp - low_temp) / (high_temp - low_temp)
    return low_fan + ((high_fan - low_fan) * pct)


def compute_fan(cfg, max_temp, current_fan):
    min_fan = cfg['min_fan']
    target = cfg['target_temp']
    mid = cfg['mid_temp']
    mid_fan = cfg['mid_fan']
    full = cfg['full_temp']
    step = cfg['step_percent']

    if max_temp >= cfg['emergency_temp']:
        return 100, 'emergency %.1f C >= %.1f C' % (max_temp, cfg['emergency_temp'])
    if max_temp >= full:
        desired = 100
        reason = 'full fan %.1f C >= %.1f C' % (max_temp, full)
    elif max_temp <= target:
        desired = min_fan
        reason = 'at/below target %.1f C <= %.1f C' % (max_temp, target)
    elif max_temp <= mid:
        raw = interpolate_fan(max_temp, target, mid, min_fan, mid_fan)
        desired = int(round(raw / step) * step)
        desired = clamp(desired, min_fan, 100)
        reason = 'curve %.1f C between target %.1f and mid %.1f C' % (max_temp, target, mid)
    else:
        raw = interpolate_fan(max_temp, mid, full, mid_fan, 100)
        desired = int(round(raw / step) * step)
        desired = clamp(desired, min_fan, 100)
        reason = 'curve %.1f C between mid %.1f and full %.1f C' % (max_temp, mid, full)

    if desired < current_fan:
        desired = max(desired, current_fan - cfg['max_down_step'])
        desired = int(round(float(desired) / step) * step)
    return clamp(desired, min_fan, 100), reason


def set_fan_for_boards(board_ids, fan):
    if not LIVE_FAN_API_ENABLED:
        return False, LIVE_FAN_API_DISABLED_REASON
    if not board_ids:
        return False, 'no boards available for fan request'
    # BK-B has one shared fan rail behind one bridge. Queue one global request;
    # sgminer applies it from the Baikal polling path to avoid API-thread USB IO.
    bid = min(board_ids)
    resp, err = call_api('bkbfan', '%s,%d' % (bid, fan))
    if err or not api_status_ok(resp):
        return False, '%s:%s' % (bid, err or resp)
    return True, 'queued'


def write_state(enabled, status, cfg, fan, max_temp=0.0, temps=None, reason=''):
    now = int(time.time())
    previous = load_json(STATE, {})
    revision = cfg.get('request_revision', '')
    pending_since = 0
    if status == 'queued':
        pending_since = now
        if revision and previous.get('request_revision') != revision:
            pending_since = cfg.get('requested_at') or now
        elif previous.get('status') == 'queued' and previous.get('fan') == fan:
            pending_since = previous.get('pending_since') or now
    write_json_atomic(STATE, {
        'version': 1,
        'updated': now,
        'request_revision': revision,
        'pending_since': pending_since,
        'enabled': bool(enabled),
        'mode': 'auto' if enabled else 'manual',
        'status': status,
        'fan': int(fan),
        'max_temp': max_temp,
        'temps': temps or {},
        'reason': reason,
        'config': {
            'min_fan': cfg.get('min_fan'),
            'target_temp': cfg.get('target_temp'),
            'mid_temp': cfg.get('mid_temp'),
            'mid_fan': cfg.get('mid_fan'),
            'full_temp': cfg.get('full_temp'),
            'emergency_temp': cfg.get('emergency_temp'),
            'manual_fan': cfg.get('manual_fan'),
            'cutoff_temp': cfg.get('cutoff_temp'),
        }
    })


def main():
    if not acquire_lock():
        return 0
    try:
        cfg = load_config()
        state = load_json(STATE, {})
        if not cfg['enabled']:
            write_state(False, 'disabled', cfg, cfg['manual_fan'], reason='auto fan disabled')
            return 0

        current_fan = query_current_fan(int_or_default(state.get('fan'), cfg['manual_fan']))
        devs, err = query_devs()
        if err:
            write_state(True, 'error', cfg, current_fan,
                        reason='temperature unavailable: ' + err)
            sys.stderr.write('fan-controller: %s\n' % err)
            return 1

        temps = dict((str(d['id']), d['temp']) for d in devs)
        board_ids = [d['id'] for d in devs]
        max_temp = max(d['temp'] for d in devs)
        target_fan, reason = compute_fan(cfg, max_temp, current_fan)

        if not LIVE_FAN_API_ENABLED:
            write_state(True, 'unsupported', cfg, current_fan, max_temp, temps,
                        '%s; target fan %d%% (%s)' %
                        (LIVE_FAN_API_DISABLED_REASON, target_fan, reason))
            return 0

        queued = False
        if target_fan != current_fan:
            ok, error = set_fan_for_boards(board_ids, target_fan)
            if not ok:
                set_fan_for_boards(board_ids, 100)
                write_state(True, 'error', cfg, 100, max_temp, temps,
                            'failed to set %d%%: %s' % (target_fan, error))
                sys.stderr.write('fan-controller: failed to set fan: %s\n' % error)
                return 1
            queued = True
        status = 'emergency' if target_fan == 100 and max_temp >= cfg['emergency_temp'] else ('queued' if queued else 'ok')
        write_state(True, status, cfg, target_fan, max_temp, temps, reason)
        return 0
    finally:
        release_lock()


if __name__ == '__main__':
    sys.exit(main())
