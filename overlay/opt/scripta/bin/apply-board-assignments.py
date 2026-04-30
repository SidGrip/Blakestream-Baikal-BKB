#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
apply-board-assignments.py - Blakestream-GaintB

Applies per-board enable/disable AND per-board pool pinning to a freshly-
started sgminer based on board-assignments.json (user choice) and
temp-state.json (temp watchdog).

Called by /opt/scripta/startup/miner-start.sh once sgminer's API is up.
sgminer always boots with all detected ASCs enabled and unpinned. After
boot we walk the assignments and call ascdisable + ascpool to bring the
board state into the desired configuration.

A board is idle when:
  - assignments[board_id] is null/empty/missing  (user picked "— idle —"
    in the Scripta Status page dropdown)
  OR
  - temp_state[board_id].disabled is True        (temp watchdog locked
    it out for being over the cutoff)

Otherwise the board gets ascenable to ensure it's hashing.

v2: per-ASC pool routing via the new ascpool|<asc>,<pool_no> command.
After enable/disable, we resolve each enabled board's saved-pool-id to the
runtime sgminer pool_no via /tmp/scripta-runtime-poolmap.json (the sidecar
written by build-miner-runtime.py) and call ascpool to pin the board to
that pool. Disabled boards get ascpool|<asc>,-1 to clear any stale pin
left over from a previous configuration.

Requires the v2 patched sgminer (1+2+4+5+0006) which provides ascpool and
the per-ASC routing semantics. With the v1 binary (1+2+4+5) the ascpool
calls will fail with "invalid command" — that's expected and the applier
treats it as a hard failure so the launcher fails-closed instead of
running with mis-pinned ASCs.

Sidecar schema (written by build-miner-runtime.py):
  {
    "runtime_pool_count": N,
    "saved_to_runtime": {"pool-eu3": 0, "pool-at1": 1, ...},
    "runtime_to_saved": {"0": "pool-eu3", "1": "pool-at1", ...},
    "backup_pool_nos":  [2, 3, ...]
  }

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import errno
import json
import os
import socket
import sys
import time

ETC = '/opt/scripta/etc'
ASSIGNMENTS = os.path.join(ETC, 'board-assignments.json')
TEMP_STATE = os.path.join(ETC, 'temp-state.json')
RUNTIME_POOLMAP = '/tmp/scripta-runtime-poolmap.json'
BOARD_IDS = ['0', '1', '2']
DEFAULT_ASSIGNMENTS = {"0": None, "1": None, "2": None}
DEFAULT_API_PORT = 4028
BAIKAL_POOL_UNSET = -1   # matches BAIKAL_POOL_UNSET in driver-baikal.h


def load_assignments(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except IOError as e:
        if getattr(e, 'errno', None) != errno.ENOENT:
            sys.stderr.write('apply-board-assignments: cannot read %s, using all-idle default: %s\n'
                             % (path, e))
        return dict(DEFAULT_ASSIGNMENTS)
    except ValueError as e:
        sys.stderr.write('apply-board-assignments: invalid JSON in %s, using all-idle default: %s\n'
                         % (path, e))
        return dict(DEFAULT_ASSIGNMENTS)


def load_temp_state(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except IOError as e:
        if getattr(e, 'errno', None) == errno.ENOENT:
            return {}
        sys.stderr.write('apply-board-assignments: CRITICAL cannot read %s: %s\n'
                         % (path, e))
        return None
    except ValueError as e:
        sys.stderr.write('apply-board-assignments: CRITICAL invalid JSON in %s: %s\n'
                         % (path, e))
        return None


def load_poolmap(path):
    """v2: read /tmp/scripta-runtime-poolmap.json sidecar.

    Returns a dict shaped like:
        {"saved_to_runtime": {pool_id: int, ...},
         "runtime_pool_count": int,
         ...}
    Returns None on any failure (missing, unreadable, malformed) — the
    applier will fail-closed in that case so the launcher refuses to
    leave sgminer running with mis-pinned ASCs.
    """
    try:
        with open(path, 'r') as f:
            d = json.load(f)
    except IOError as e:
        sys.stderr.write('apply-board-assignments: CRITICAL cannot read poolmap %s: %s\n'
                         % (path, e))
        return None
    except ValueError as e:
        sys.stderr.write('apply-board-assignments: CRITICAL invalid JSON in poolmap %s: %s\n'
                         % (path, e))
        return None

    if not isinstance(d, dict):
        sys.stderr.write('apply-board-assignments: CRITICAL poolmap %s is not a JSON object\n' % path)
        return None
    if not isinstance(d.get('saved_to_runtime'), dict):
        sys.stderr.write('apply-board-assignments: CRITICAL poolmap %s missing saved_to_runtime dict\n' % path)
        return None
    return d


def get_api_port():
    if len(sys.argv) < 2:
        return DEFAULT_API_PORT

    try:
        port = int(sys.argv[1])
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass

    sys.stderr.write('apply-board-assignments: invalid api port %r, using %d\n'
                     % (sys.argv[1], DEFAULT_API_PORT))
    return DEFAULT_API_PORT


def call_api(api_port, cmd, parameter=None):
    """Send one sgminer API command. Returns the response string or None."""
    try:
        s = socket.socket()
        s.settimeout(4)
        s.connect(('127.0.0.1', api_port))
        msg = {'command': cmd}
        if parameter is not None:
            msg['parameter'] = str(parameter)
        s.sendall(json.dumps(msg).encode())
        d = b''
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            d += chunk
        s.close()
        return d.decode('utf-8', 'replace')
    except Exception as e:
        sys.stderr.write('apply-board-assignments: %s|%s failed: %s\n'
                         % (cmd, parameter, e))
        return None


def api_ok(resp):
    if not resp:
        return False
    return '"STATUS":"S"' in resp or '"STATUS":"I"' in resp


def ascpool(api_port, board_id, pool_no):
    """Send ascpool|<board_id>,<pool_no> via the JSON API.

    Wire format is "0,1" — comma-separated, no spaces. pool_no = -1
    (BAIKAL_POOL_UNSET) clears the pin. Returns the raw response or None.
    """
    return call_api(api_port, 'ascpool', '%s,%d' % (board_id, int(pool_no)))


def ascpool_ok(resp):
    """Specifically validate an ascpool response.

    Success cases (sgminer api.c:3576):
      MSG_ASCPOOLSET — STATUS=S, "ASC %d pinned to pool %s"
      MSG_ASCPOOLCLR — STATUS=S, "ASC %d cleared pool pin"
    Anything else (including the legacy "invalid command" from a v1
    sgminer that doesn't have ascpool) is treated as a failure so the
    launcher fails-closed.
    """
    if not resp:
        return False
    if '"STATUS":"S"' not in resp:
        return False
    return ('pinned to pool' in resp) or ('cleared pool pin' in resp)


def ascfailover(api_port, board_id, pool_no):
    """Send ascfailover|<board_id>,<pool_no>. pool_no = -1 clears.

    Sets the user's explicit failover pool for this board. sgminer's
    select_baikal_failover_pool_locked prefers this pool when the primary
    dies, and returns NULL (board halts) if the explicit failover is also
    unworkable. With pool_no=-1 the board falls back to sgminer's
    automatic same-algo search (only if other same-algo pools exist in
    the runtime conf).
    """
    return call_api(api_port, 'ascfailover', '%s,%d' % (board_id, int(pool_no)))


def ascfailover_ok(resp):
    """Validate ascfailover response. Tolerates 'failover pinned' /
    'cleared failover pin' / on older sgminer without the command, treats
    'invalid command' as not-implemented (caller decides whether that's
    a hard failure or acceptable fallback)."""
    if not resp:
        return False
    if '"STATUS":"S"' not in resp:
        return False
    return ('failover pinned' in resp) or ('cleared failover pin' in resp)


def wait_for_api(api_port, timeout=15):
    for _ in range(timeout):
        r = call_api(api_port, 'version')
        if r and '"STATUS":"S"' in r:
            return True
        time.sleep(1)
    return False


def main():
    api_port = get_api_port()

    if not wait_for_api(api_port):
        sys.stderr.write('apply-board-assignments: sgminer API on port %d never came up\n'
                         % api_port)
        return 1

    assignments = load_assignments(ASSIGNMENTS)
    temp_state = load_temp_state(TEMP_STATE)
    if temp_state is None:
        return 1

    # v2: load the poolmap sidecar that build-miner-runtime.py wrote next
    # to the runtime conf. This is a hard requirement — without it we
    # can't translate saved-pool-ids to runtime pool_nos for ascpool.
    poolmap = load_poolmap(RUNTIME_POOLMAP)
    if poolmap is None:
        return 1
    saved_to_runtime = poolmap.get('saved_to_runtime', {})

    had_failures = False

    for board_id in BOARD_IDS:
        ts = temp_state.get(board_id, {}) or {}
        temp_disabled = bool(ts.get('disabled'))
        # New shape: {primary, failover}. Legacy: plain string. Coerce.
        # Truthy check (Python 2's json returns unicode, not str).
        raw = assignments.get(board_id)
        if isinstance(raw, dict):
            pool_id = raw.get('primary') or None
            failover_id = raw.get('failover') or None
        else:
            pool_id = raw or None
            failover_id = None
        user_idle = not pool_id  # null, empty string, missing

        should_disable = temp_disabled or user_idle

        # ---- Step 1: enable/disable the board ----
        if should_disable:
            reason = 'temp-locked' if temp_disabled else 'user-idle'
            r = call_api(api_port, 'ascdisable', board_id)
            ok = api_ok(r)
            sys.stderr.write('board %s: ascdisable (%s) -> %s\n'
                             % (board_id, reason, 'OK' if ok else 'FAIL'))
        else:
            r = call_api(api_port, 'ascenable', board_id)
            ok = api_ok(r)
            sys.stderr.write('board %s: ascenable (pool=%s) -> %s\n'
                             % (board_id, pool_id, 'OK' if ok else 'FAIL'))
        if not ok:
            had_failures = True
            # Don't continue with ascpool on this board if enable/disable
            # failed — the device state is already wrong.
            continue

        # ---- Step 2: ascpool — pin enabled boards, clear disabled boards ----
        if should_disable:
            # Clear primary AND failover pins (idle board has neither)
            r = ascpool(api_port, board_id, BAIKAL_POOL_UNSET)
            if ascpool_ok(r):
                sys.stderr.write('board %s: ascpool clear -> OK\n' % board_id)
            else:
                sys.stderr.write('board %s: ascpool clear -> FAIL (%r)\n' % (board_id, r))
                had_failures = True
            # ascfailover clear is best-effort: an older sgminer without the
            # command will return "invalid command" which we silently tolerate.
            ascfailover(api_port, board_id, BAIKAL_POOL_UNSET)
            continue

        # Enabled board: resolve primary pool id -> runtime pool_no
        if pool_id not in saved_to_runtime:
            sys.stderr.write('board %s: pool %r not in poolmap (assignments/runtime out of sync) -> FAIL\n'
                             % (board_id, pool_id))
            had_failures = True
            continue
        pool_no = int(saved_to_runtime[pool_id])
        r = ascpool(api_port, board_id, pool_no)
        if ascpool_ok(r):
            sys.stderr.write('board %s: ascpool set %s (pool_no=%d) -> OK\n'
                             % (board_id, pool_id, pool_no))
        else:
            sys.stderr.write('board %s: ascpool set %s (pool_no=%d) -> FAIL (%r)\n'
                             % (board_id, pool_id, pool_no, r))
            had_failures = True
            continue

        # ---- Step 3: ascfailover — pin or clear the user's explicit failover ----
        if failover_id and failover_id in saved_to_runtime:
            failover_no = int(saved_to_runtime[failover_id])
            r = ascfailover(api_port, board_id, failover_no)
            if ascfailover_ok(r):
                sys.stderr.write('board %s: ascfailover set %s (pool_no=%d) -> OK\n'
                                 % (board_id, failover_id, failover_no))
            else:
                # Tolerate older sgminer without ascfailover (returns "invalid
                # command"). The runtime conf still includes the failover pool
                # as a quota:1 backup, so sgminer's auto-search will at least
                # consider it; the user's "stop hashing if both die" guarantee
                # only applies when the new sgminer is in place.
                sys.stderr.write('board %s: ascfailover set %s -> non-fatal (%r)\n'
                                 % (board_id, failover_id, r))
        else:
            # No explicit failover for this board — clear any stale pin.
            ascfailover(api_port, board_id, BAIKAL_POOL_UNSET)

    return 1 if had_failures else 0


if __name__ == '__main__':
    sys.exit(main())
