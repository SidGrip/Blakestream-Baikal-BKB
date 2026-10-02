#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Per-board hashrate recorder. Polls sgminer's devs API every minute and
appends [unix_ts, mhs5s] samples to the Settings -> Charts -> Save To target,
trimming to a 7-day rolling window. Skips on API failure (no zero-sample spam).
Python 2.7 / 3 compatible.
"""

from __future__ import print_function
import json
import math
import os
import socket
import sys
import time

ETC = '/opt/scripta/etc'
SCRIPTA_CONF = os.path.join(ETC, 'scripta.conf')


def runtime_var_dir():
    """Settings → Charts → Save To picks RAM tmpfs (default) or SD. The
    Charts on/off toggle is purely visual and does NOT affect this path —
    the recorder keeps writing the file regardless of chart visibility."""
    try:
        with open(SCRIPTA_CONF, 'r') as f:
            cfg = json.load(f)
        if cfg.get('userChartsSaveTo', 'RAM') == 'RAM':
            return '/run/blakestream'
    except (IOError, ValueError):
        pass
    return '/opt/scripta/var'


VAR = runtime_var_dir()
HISTORY = os.path.join(VAR, 'hashrate-history.json')
RETENTION_DAYS = 7
STEP_SECONDS = 60
ACCEPTED_RATES = '/run/blakestream/accepted-work-rates.json'


def append_accepted_sample(board, summary, board_id, now, cutoff):
    if not isinstance(summary, dict):
        summary = {}
    updated = summary.get('updated', 0)
    rates = summary.get('boards', {})
    rates = rates.get(str(board_id), {}) if isinstance(rates, dict) else {}
    window = rates.get('15m', {}) if isinstance(rates, dict) else {}
    fresh = isinstance(updated, (int, float)) and 0 <= now - updated <= 120
    value = window.get('ghs') if isinstance(window, dict) and window.get('valid') and fresh else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value) or math.isinf(value) or value < 0:
        value = None
    stamp = int(updated) if fresh else now
    samples = board.setdefault('accepted_samples', [])
    if samples and stamp <= samples[-1][0]:
        return
    context = rates.get('context') if fresh else None
    if samples and board.get('accepted_context') != context:
        samples.append([stamp - 1, None])
    samples.append([stamp, value * 1000 if value is not None else None])
    board['accepted_context'] = context
    board['accepted_samples'] = [s for s in samples if s[0] >= cutoff]


def drop_stale_copy():
    """After a RAM<->SD switch the previous location still holds the old file.
    Remove it so only the active location keeps a copy. Guarded by a successful
    config read so a transient read error never deletes live data."""
    try:
        with open(SCRIPTA_CONF, 'r') as f:
            json.load(f)
    except (IOError, ValueError):
        return
    other = '/opt/scripta/var' if VAR == '/run/blakestream' else '/run/blakestream'
    try:
        os.remove(os.path.join(other, 'hashrate-history.json'))
    except OSError:
        pass


drop_stale_copy()


def write_json_atomic(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if parent:
        try:
            os.chmod(parent, 0o775)
        except OSError:
            pass
    tmp = '%s.%s.tmp' % (path, os.getpid())
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f, separators=(',', ':'))
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


def load_json(path, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (IOError, ValueError):
        return default


def call_api(cmd):
    """Return parsed sgminer API response, or None on failure."""
    s = None
    try:
        s = socket.socket()
        s.settimeout(4)
        s.connect(('127.0.0.1', 4028))
        s.sendall(('{"command":"' + cmd + '"}').encode('ascii'))
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
        sys.stderr.write('hashrate-history: cannot query %s: %s\n' % (cmd, e))
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
    txt = data.decode('utf-8', 'replace').rstrip('\x00').strip()
    try:
        return json.loads(txt)
    except ValueError:
        return None


def main():
    devs_resp = call_api('devs')
    if devs_resp is None:
        return 0  # API unreachable; cron retries next minute.
    devs = devs_resp.get('DEVS') or []
    if not devs:
        return 0

    pools_resp = call_api('pools') or {}
    pools = pools_resp.get('POOLS') or []
    pool_url_by_no = {}
    pool_health_by_no = {}
    for p in pools:
        no = p.get('POOL')
        pool_url_by_no[no] = p.get('URL', '')
        pool_health_by_no[no] = {
            'url': p.get('URL', ''),
            'status': p.get('Status', ''),
            'stratum_active': bool(p.get('Stratum Active', False)),
            'last_share_time': p.get('Last Share Time', 0),
        }

    history = load_json(HISTORY, default={
        'version': 1,
        'step_seconds': STEP_SECONDS,
        'max_age_seconds': RETENTION_DAYS * 86400,
        'updated': 0,
        'boards': [],
    })

    boards_by_id = {}
    for b in history.get('boards', []):
        boards_by_id[b.get('id')] = b

    now = int(time.time())
    cutoff = now - (RETENTION_DAYS * 86400)
    accepted = load_json(ACCEPTED_RATES, {})
    if not isinstance(accepted, dict):
        accepted = {}

    for d in devs:
        asc = d.get('ASC')
        if asc is None:
            continue
        mhs5s = int(round(float(d.get('MHS 5s', 0) or 0)))
        eff = d.get('Effective Pool', d.get('Primary Pool', -1))
        try:
            eff = int(eff)
        except (TypeError, ValueError):
            eff = -1
        prim = d.get('Primary Pool', -1)
        try:
            prim = int(prim)
        except (TypeError, ValueError):
            prim = -1
        failover = bool(d.get('Failover Active', False))
        enabled = (d.get('Enabled') == 'Y')

        b = boards_by_id.get(asc)
        if b is None:
            b = {'id': asc, 'samples': []}
            boards_by_id[asc] = b

        b['enabled'] = enabled
        b['primary_pool_no'] = prim
        b['effective_pool_no'] = eff
        b['failover_active'] = failover
        b['pool_url'] = pool_url_by_no.get(eff, '')
        b['samples'].append([now, mhs5s])
        # Trim older samples
        b['samples'] = [s for s in b['samples'] if s and s[0] >= cutoff]
        append_accepted_sample(b, accepted, asc, now, cutoff)

    history['boards'] = sorted(boards_by_id.values(), key=lambda x: x.get('id', 0))
    history['pools_health'] = pool_health_by_no
    history['updated'] = now
    write_json_atomic(HISTORY, history)
    return 0


if __name__ == '__main__':
    sys.exit(main())
