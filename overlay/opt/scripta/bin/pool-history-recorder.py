#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pool-history-recorder.py - Blakestream-GaintB pool history snapshot recorder.

Polls sgminer's pools API every 5 minutes (via /etc/cron.d/blakestream-pool-history)
and appends a sample to the Settings -> Persist History -> Pool target. Trims
samples older than the configured retention window (default 7 days).

Each saved pool gets its own bucket keyed by pool ID. We map sgminer-API pools
back to saved-pools.json entries by matching the cleaned URL (strip
http://quota:N; prefix that sgminer adds).

Sample format (compact keys to minimize file size over 7 days of 5-min samples):
  t   = unix timestamp
  st  = "Alive" / "Dead" / etc
  a   = Accepted (counter)
  r   = Rejected (counter)
  da  = DifficultyAccepted (counter)
  dr  = DifficultyRejected (counter)
  best = BestShare

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import json
import os
import re
import socket
import sys
import time

ETC = '/opt/scripta/etc'
SCRIPTA_CONF = os.path.join(ETC, 'scripta.conf')
RUNTIME_RAM = '/run/blakestream'
RUNTIME_SD = '/opt/scripta/var'


def runtime_var_dir():
    """RAM tmpfs by default; SD when settings.userPersistPoolHistory is on."""
    try:
        with open(SCRIPTA_CONF, 'r') as f:
            cfg = json.load(f)
        if cfg.get('userPersistPoolHistory'):
            return RUNTIME_SD
    except (IOError, ValueError):
        pass
    return RUNTIME_RAM


VAR = runtime_var_dir()
SAVED_POOLS = os.path.join(ETC, 'saved-pools.json')
HISTORY = os.path.join(VAR, 'pool-history.json')
RETENTION_DAYS = 7
SAMPLE_INTERVAL_SEC = 300  # used for trim sanity, not for sleeping


def drop_stale_copy():
    """Remove the pool-history copy left in the inactive location after a
    RAM<->SD switch. Guarded by a successful config read."""
    try:
        with open(SCRIPTA_CONF, 'r') as f:
            json.load(f)
    except (IOError, ValueError):
        return
    other = RUNTIME_SD if VAR == RUNTIME_RAM else RUNTIME_RAM
    try:
        os.remove(os.path.join(other, 'pool-history.json'))
    except OSError:
        pass


drop_stale_copy()


def load_json(path, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (IOError, ValueError):
        return default


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
            json.dump(data, f, separators=(',', ':'))  # compact
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


def query_sgminer_pools():
    """Return parsed list of pool dicts from sgminer's API."""
    s = None
    try:
        s = socket.socket()
        s.settimeout(4)
        s.connect(('127.0.0.1', 4028))
        s.sendall(b'{"command":"pools"}')
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
        sys.stderr.write('pool-history-recorder: cannot query sgminer: %s\n' % e)
        return []
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    txt = data.decode('utf-8', 'replace').rstrip('\x00').strip()
    try:
        d = json.loads(txt)
    except ValueError:
        return []
    return d.get('POOLS', []) or []


def clean_pool_url(url):
    """Strip 'http://quota:N;' prefix that sgminer prepends to load-balance pools."""
    if not url:
        return url
    s = url
    s = re.sub(r'^https?://', '', s)
    s = re.sub(r'^quota:\d+;', '', s)
    return s


def index_saved_by_url(saved):
    """Return dict {clean_url: (pool_id, pool_dict, category_name)}."""
    out = {}
    for cat in saved.get('categories', []):
        for pool in cat.get('pools', []):
            url = pool.get('url', '')
            if url:
                out[url] = (pool.get('id'), pool, cat.get('name', ''))
    return out


def main():
    saved = load_json(SAVED_POOLS, default={'categories': []})
    saved_by_url = index_saved_by_url(saved)
    if not saved_by_url:
        return 0

    sg_pools = query_sgminer_pools()
    if not sg_pools:
        return 0

    history = load_json(HISTORY, default={'pools': {}, 'updated': 0})
    if 'pools' not in history:
        history['pools'] = {}

    now = int(time.time())
    cutoff = now - (RETENTION_DAYS * 86400)

    for sg in sg_pools:
        url = clean_pool_url(sg.get('URL', ''))
        match = saved_by_url.get(url)
        if not match:
            continue
        pool_id, saved_pool, cat_name = match
        if not pool_id:
            continue

        bucket = history['pools'].get(pool_id)
        if not bucket:
            bucket = {
                'name': saved_pool.get('name', pool_id),
                'category': cat_name,
                'algo': saved_pool.get('algo', ''),
                'url': url,
                'samples': [],
            }
            history['pools'][pool_id] = bucket
        else:
            # Refresh metadata in case the saved pool was renamed/moved
            bucket['name'] = saved_pool.get('name', bucket.get('name', pool_id))
            bucket['category'] = cat_name
            bucket['algo'] = saved_pool.get('algo', bucket.get('algo', ''))
            bucket['url'] = url

        sample = {
            't': now,
            'st': sg.get('Status', ''),
            'a': int(sg.get('Accepted', 0) or 0),
            'r': int(sg.get('Rejected', 0) or 0),
            'da': float(sg.get('Difficulty Accepted', 0) or 0),
            'dr': float(sg.get('Difficulty Rejected', 0) or 0),
            'best': float(sg.get('Best Share', 0) or 0),
        }
        bucket['samples'].append(sample)

        # Trim samples older than retention window
        bucket['samples'] = [s for s in bucket['samples'] if s.get('t', 0) >= cutoff]

    history['updated'] = now
    write_json_atomic(HISTORY, history)
    return 0


if __name__ == '__main__':
    sys.exit(main())
