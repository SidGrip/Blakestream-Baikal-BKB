#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
temp-watchdog.py - Blakestream-GaintB per-board temperature watchdog.

Polls sgminer's API for current board temperatures. If a board exceeds the
configured disable_at threshold, marks it disabled in temp-state.json and
triggers a miner-restart so build-miner-runtime.py rebuilds the runtime config
without that board. Once the board cools below recover_at (with hysteresis),
marks it active again and triggers another restart.

Called by /etc/cron.d/blakestream-temp-watchdog every 30 seconds. The Scripta
    dashboard reads /run/blakestream-control/temp-state.json via f_blakestream.php to
grey out disabled rows in the Devices table.

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import errno
import grp
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

ETC = '/opt/scripta/etc'
RUNTIME_DIR = '/run/blakestream-control'
CONFIG = ETC + '/temp-config.json'
STATE = RUNTIME_DIR + '/temp-state.json'
API_STATE = RUNTIME_DIR + '/temp-api-state.json'
API_WARNING_AFTER = 120
API_WARNING_INTERVAL = 300
RESTART = '/opt/scripta/startup/miner-restart.sh'
RUNTIME_CONF = '/run/blakestream-miner/scripta-runtime-miner.conf'
DEFAULT_API_PORT = 4028

DEFAULT_CONFIG = {
    'enabled': True,
    'disable_at': 80.0,    # disable a board when temp >= this
    'recover_at': 70.0,    # re-enable when temp <= this (hysteresis)
}


def load_config(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except IOError as e:
        if getattr(e, 'errno', None) != errno.ENOENT:
            sys.stderr.write('temp-watchdog: cannot read config %s, using defaults: %s\n'
                             % (path, e))
        return DEFAULT_CONFIG.copy()
    except ValueError as e:
        sys.stderr.write('temp-watchdog: invalid JSON in config %s, using defaults: %s\n'
                         % (path, e))
        return DEFAULT_CONFIG.copy()


def load_state(path):
    try:
        with open(path, 'r') as f:
            raw = f.read()
        # Dev Tools can intentionally clear this recorder state. Treat a
        # zero-byte file as no prior state so the next watchdog pass rebuilds
        # it; malformed non-empty JSON remains a hard error.
        if not raw.strip():
            return {}
        return json.loads(raw)
    except IOError as e:
        if getattr(e, 'errno', None) == errno.ENOENT:
            return {}
        sys.stderr.write('temp-watchdog: CRITICAL cannot read %s: %s\n'
                         % (path, e))
        return None
    except ValueError as e:
        sys.stderr.write('temp-watchdog: CRITICAL invalid JSON in %s: %s\n'
                         % (path, e))
        return None


def write_json_atomic(path, data):
    parent = os.path.dirname(path)
    if not parent or os.path.islink(parent) or not os.path.isdir(parent):
        raise IOError(errno.EPERM, 'unsafe control runtime directory')
    owner = None
    try:
        owner = (0, grp.getgrnam('www-data').gr_gid)
    except (KeyError, OSError):
        pass
    fd, tmp = tempfile.mkstemp(prefix='.temp-state.', dir=parent)
    try:
        with os.fdopen(fd, 'w') as f:
            fd = -1
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
            if owner:
                os.fchown(f.fileno(), owner[0], owner[1])
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


def get_api_port():
    try:
        with open(RUNTIME_CONF, 'r') as f:
            runtime = json.load(f)
        port = int(runtime.get('api-port', DEFAULT_API_PORT))
        if 1 <= port <= 65535:
            return port
    except (IOError, ValueError, TypeError):
        pass
    return DEFAULT_API_PORT


def report_api_status(api_port, error=None):
    """Record outages separately from board cutoff state and report transitions."""
    now = int(time.time())
    try:
        previous = load_state(API_STATE)
        if not isinstance(previous, dict):
            previous = {}
        since = previous.get('unavailable_since')
        if (previous.get('port') != api_port or
                not isinstance(since, (int, float)) or since > now):
            since = None
        if error is None:
            if since is not None:
                sys.stderr.write('temp-watchdog: temperature monitoring resumed '
                                 'on port %d after %ds\n' % (api_port, now - since))
                write_json_atomic(API_STATE, {})
            return
        if since is None:
            sys.stderr.write('temp-watchdog: waiting for sgminer API on port %d; '
                             'temperatures unavailable (miner may be starting, paused, or stopped)\n'
                             % api_port)
            write_json_atomic(API_STATE, {'port': api_port, 'unavailable_since': now,
                                          'last_reported': now})
        elif (now - since >= API_WARNING_AFTER and
              (previous.get('last_reported') == since or
               now - previous.get('last_reported', since) >= API_WARNING_INTERVAL)):
            sys.stderr.write('temp-watchdog: WARNING temperature monitoring unavailable '
                             'on port %d for %ds; check miner status if this is unexpected: %s\n'
                             % (api_port, now - since, error))
            previous['last_reported'] = now
            write_json_atomic(API_STATE, previous)
    except (IOError, OSError, TypeError, ValueError) as status_error:
        # Logging trouble must not prevent temperature cutoff processing.
        sys.stderr.write('temp-watchdog: cannot record API availability: %s\n' % status_error)
        if error is not None:
            sys.stderr.write('temp-watchdog: cannot query sgminer API on port %d: %s\n'
                             % (api_port, error))


def query_sgminer_devs(api_port):
    """Return dict {board_id_str: temperature_celsius_float} from sgminer API."""
    s = None
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(('127.0.0.1', api_port))
        s.sendall(b'{"command":"devs"}')
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
        report_api_status(api_port, e)
        return {}
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    txt = data.decode('utf-8', 'replace')
    out = {}
    # Match each ASC entry's ID and Temperature
    for m in re.finditer(r'"ASC":(\d+)[^}]*?"Temperature":([\d.]+)', txt):
        board_id = m.group(1)
        temp = float(m.group(2))
        out[board_id] = temp
    report_api_status(api_port, None if out else 'no board temperatures returned')
    return out


def trigger_restart():
    try:
        with open(os.devnull, 'wb') as devnull:
            rc = subprocess.call([RESTART], stdout=devnull, stderr=devnull)
    except OSError as e:
        sys.stderr.write('temp-watchdog: CRITICAL failed to exec %s: %s\n'
                         % (RESTART, e))
        return False

    if rc != 0:
        sys.stderr.write('temp-watchdog: CRITICAL %s exited rc=%s\n'
                         % (RESTART, rc))
        return False

    return True


def main():
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(load_config(CONFIG))
    if not cfg.get('enabled', True):
        return 0

    disable_at = float(cfg.get('disable_at', 80.0))
    recover_at = float(cfg.get('recover_at', 70.0))
    if recover_at >= disable_at:
        sys.stderr.write('temp-watchdog: recover_at (%s) must be < disable_at (%s)\n'
                         % (recover_at, disable_at))
        return 1

    state = load_state(STATE)
    if state is None:
        return 1

    api_port = get_api_port()
    temps = query_sgminer_devs(api_port)
    if not temps:
        # sgminer is down or no boards reporting; nothing to do
        return 0

    now = int(time.time())
    changed = False
    for board_id, temp in temps.items():
        prev = state.get(board_id, {}) or {}
        prev_disabled = prev.get('disabled', False)
        new_disabled = prev_disabled
        new_reason = prev.get('reason', '')

        if not prev_disabled and temp >= disable_at:
            new_disabled = True
            new_reason = 'Temp %.1f°C >= %.1f°C' % (temp, disable_at)
            sys.stderr.write('temp-watchdog: board %s DISABLE (%s)\n' % (board_id, new_reason))
        elif prev_disabled and temp <= recover_at:
            new_disabled = False
            new_reason = ''
            sys.stderr.write('temp-watchdog: board %s RECOVER (Temp %.1f°C <= %.1f°C)\n'
                             % (board_id, temp, recover_at))

        state[board_id] = {
            'disabled': new_disabled,
            'reason': new_reason,
            'last_temp': temp,
            'disable_at': disable_at,
            'recover_at': recover_at,
            'last_seen': now,
        }
        if new_disabled != prev_disabled:
            changed = True

    try:
        write_json_atomic(STATE, state)
    except (IOError, OSError) as e:
        sys.stderr.write('temp-watchdog: CRITICAL failed to write %s: %s\n'
                         % (STATE, e))
        return 1

    if changed:
        sys.stderr.write('temp-watchdog: state changed, restarting miner\n')
        if not trigger_restart():
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
