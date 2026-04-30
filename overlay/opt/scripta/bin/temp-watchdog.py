#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
temp-watchdog.py - Blakestream-GaintB per-board temperature watchdog.

Polls sgminer's API for current board temperatures. If a board exceeds the
configured disable_at threshold, marks it disabled in temp-state.json and
triggers a miner-restart so build-miner-runtime.py rebuilds the runtime config
without that board. Once the board cools below recover_at (with hysteresis),
marks it active again and triggers another restart.

Called by /etc/cron.d/blakestream-temp-watchdog every minute. The Scripta
dashboard reads temp-state.json via f_blakestream.php to grey out disabled
rows in the Devices table.

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import errno
import json
import os
import re
import socket
import subprocess
import sys
import time

ETC = '/opt/scripta/etc'
CONFIG = ETC + '/temp-config.json'
STATE = ETC + '/temp-state.json'
RESTART = '/opt/scripta/startup/miner-restart.sh'
RUNTIME_CONF = '/tmp/scripta-runtime-miner.conf'
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
            return json.load(f)
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
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.rename(tmp, path)


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


def query_sgminer_devs(api_port):
    """Return dict {board_id_str: temperature_celsius_float} from sgminer API."""
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect(('127.0.0.1', api_port))
        s.sendall(b'{"command":"devs"}')
        data = b''
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
        s.close()
    except Exception as e:
        sys.stderr.write('temp-watchdog: cannot query sgminer API on port %d: %s\n'
                         % (api_port, e))
        return {}

    txt = data.decode('utf-8', 'replace')
    out = {}
    # Match each ASC entry's ID and Temperature
    for m in re.finditer(r'"ASC":(\d+)[^}]*?"Temperature":([\d.]+)', txt):
        board_id = m.group(1)
        temp = float(m.group(2))
        out[board_id] = temp
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
