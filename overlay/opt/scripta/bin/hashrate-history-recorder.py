#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Per-board hashrate recorder. Polls sgminer's devs API every minute and
appends [unix_ts, mhs5s] samples to /opt/scripta/var/hashrate-history.json,
trimming to a 7-day rolling window. Skips on API failure (no zero-sample
spam). Python 2.7 / 3 compatible.
"""

from __future__ import print_function
import json
import os
import socket
import sys
import time

VAR = '/opt/scripta/var'
HISTORY = os.path.join(VAR, 'hashrate-history.json')
RETENTION_DAYS = 7
STEP_SECONDS = 60


def write_json_atomic(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    os.rename(tmp, path)


def load_json(path, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (IOError, ValueError):
        return default


def call_api(cmd):
    """Return parsed sgminer API response, or None on failure."""
    try:
        s = socket.socket()
        s.settimeout(4)
        s.connect(('127.0.0.1', 4028))
        s.sendall(('{"command":"' + cmd + '"}').encode('ascii'))
        data = b''
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
        s.close()
    except Exception as e:
        sys.stderr.write('hashrate-history: cannot query %s: %s\n' % (cmd, e))
        return None
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
    for p in pools:
        pool_url_by_no[p.get('POOL')] = p.get('URL', '')

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

    history['boards'] = sorted(boards_by_id.values(), key=lambda x: x.get('id', 0))
    history['updated'] = now
    write_json_atomic(HISTORY, history)
    return 0


if __name__ == '__main__':
    sys.exit(main())
