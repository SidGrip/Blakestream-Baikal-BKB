#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
failover-history-recorder.py - Blakestream-GaintB failover event recorder.

Derives per-board failover transitions from the per-minute samples written
by hashrate-history-recorder.py and persists them as discrete events. Runs
every minute via /etc/cron.d/blakestream-failover-history.

Event shape: each completed failover is one row with the board id, the
start/end unix timestamps, the duration in seconds, and the pool URLs/names
for both the primary that went down and the failover destination that took
over. In-progress failovers live in a separate `open` map until they close.

Retention is 7 days, matching pool-history. The "Clear" button in the
dashboard wipes the current Settings -> Persist History -> Failover target via
f_blakestream.php.

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import json
import os
import re
import sys
import time

ETC = '/opt/scripta/etc'
SCRIPTA_CONF = os.path.join(ETC, 'scripta.conf')
RUNTIME_RAM = '/run/blakestream'
RUNTIME_SD = '/opt/scripta/var'


def _load_cfg():
    try:
        with open(SCRIPTA_CONF, 'r') as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def _dir_for(cfg, key, default=False):
    """SD when settings[key] is on; RAM otherwise. default is the OFF fallback."""
    on = cfg.get(key, default)
    return RUNTIME_SD if on else RUNTIME_RAM


def _hashrate_dir_for(cfg):
    """Settings -> Charts -> Save To controls hashrate-history.json."""
    return RUNTIME_RAM if cfg.get('userChartsSaveTo', 'RAM') == 'RAM' else RUNTIME_SD


_cfg = _load_cfg()
# hashrate-history.json location is governed by userChartsSaveTo. The
# userCharts flag only hides/shows chart cards in the UI.
HASHRATE_HISTORY = os.path.join(_hashrate_dir_for(_cfg), 'hashrate-history.json')
SAVED_POOLS = os.path.join(ETC, 'saved-pools.json')
ASSIGNMENTS = os.path.join(ETC, 'board-assignments.json')
# failover-events.json location is governed by userPersistFailoverHistory.
EVENTS = os.path.join(_dir_for(_cfg, 'userPersistFailoverHistory'), 'failover-events.json')
RETENTION_DAYS = 7


def _drop_stale_events():
    """Remove the failover-events copy left in the inactive location after a
    RAM<->SD switch. Guarded by a successful config read."""
    try:
        with open(SCRIPTA_CONF, 'r') as f:
            json.load(f)
    except (IOError, ValueError):
        return
    active = os.path.dirname(EVENTS)
    other = RUNTIME_SD if active == RUNTIME_RAM else RUNTIME_RAM
    try:
        os.remove(os.path.join(other, 'failover-events.json'))
    except OSError:
        pass


_drop_stale_events()


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


def clean_pool_url(url):
    if not url:
        return ''
    s = re.sub(r'^https?://', '', url)
    s = re.sub(r'^quota:\d+;', '', s)
    return s


def index_saved_pools(saved):
    """clean_url -> friendly name."""
    out = {}
    for cat in saved.get('categories', []):
        for p in cat.get('pools', []):
            u = p.get('url', '')
            if u:
                out[u] = p.get('name', '')
    return out


def index_pools_by_id(saved):
    """pool_id -> {name, url}."""
    out = {}
    for cat in saved.get('categories', []):
        for p in cat.get('pools', []):
            pid = p.get('id')
            if pid:
                out[pid] = p
    return out


def primary_pool_for_board(assignments, pool_by_id, bid):
    """Resolve a board's saved primary pool. Returns (name, url) or ('','')."""
    a = assignments.get(str(bid)) or assignments.get(bid)
    if isinstance(a, dict):
        pid = a.get('primary')
    else:
        pid = a
    if not pid:
        return ('', '')
    p = pool_by_id.get(pid)
    if not p:
        return ('', '')
    return (p.get('name', ''), p.get('url', ''))


def main():
    hist = load_json(HASHRATE_HISTORY, default=None)
    if not hist or 'boards' not in hist:
        return 0

    saved = load_json(SAVED_POOLS, default={'categories': []})
    name_by_url = index_saved_pools(saved)
    pool_by_id = index_pools_by_id(saved)
    assignments = load_json(ASSIGNMENTS, default={})
    pools_health = hist.get('pools_health') or {}

    state = load_json(EVENTS, default={
        'version': 1,
        'updated': 0,
        'max_age_seconds': RETENTION_DAYS * 86400,
        'open': {},
        'events': [],
    })
    state.setdefault('open', {})
    state.setdefault('events', [])

    now = int(time.time())
    cutoff = now - (RETENTION_DAYS * 86400)

    for b in hist.get('boards', []):
        bid = b.get('id')
        if bid is None:
            continue
        bid_key = str(bid)
        failover = bool(b.get('failover_active', False))
        eff_url_raw = b.get('pool_url', '')
        eff_url = clean_pool_url(eff_url_raw)
        eff_name = name_by_url.get(eff_url, '')

        # Latest sample timestamp (use it as the event tick rather than `now`
        # so events stay consistent with the source data).
        samples = b.get('samples', [])
        if samples:
            sample_ts = samples[-1][0]
        else:
            sample_ts = now

        open_evt = state['open'].get(bid_key)

        # Only treat this as a real failover when the primary pool is
        # actually unhealthy. Sgminer reports "Failover Active" whenever
        # Effective Pool != Primary Pool — but in load-balance mode with
        # quotas, that flips on every quota tick without any pool failing.
        # Gate event opening on the primary's API status: pool must be
        # not-Alive OR have stratum down for it to count.
        primary_no = b.get('primary_pool_no', -1)
        primary_health = pools_health.get(str(primary_no)) or pools_health.get(primary_no) or {}
        primary_status = primary_health.get('status', '')
        primary_stratum_active = primary_health.get('stratum_active', None)
        primary_unhealthy = (
            (primary_status and primary_status != 'Alive') or
            (primary_stratum_active is False)
        )
        # If we have no health snapshot at all (legacy hashrate-history.json
        # without pools_health), fall back to the old behavior so we don't
        # silently lose all events. Once the hashrate recorder runs once
        # with the new code, pools_health is present and the gate engages.
        suppress_event = primary_health and not primary_unhealthy

        if failover and not open_evt and not suppress_event:
            primary_name, primary_url = primary_pool_for_board(assignments, pool_by_id, bid)
            state['open'][bid_key] = {
                'board_id': int(bid),
                'start_ts': sample_ts,
                'last_seen_ts': sample_ts,
                'primary_pool_no': int(b.get('primary_pool_no', -1) or -1),
                'effective_pool_no': int(b.get('effective_pool_no', -1) or -1),
                'primary_pool_url': primary_url,
                'primary_pool_name': primary_name,
                'effective_pool_url': eff_url,
                'effective_pool_name': eff_name,
            }
        elif failover and open_evt:
            # still in failover: refresh last_seen_ts and the effective pool
            # (in case the board re-routed mid-failover to a different alt)
            open_evt['last_seen_ts'] = sample_ts
            open_evt['effective_pool_url'] = eff_url
            open_evt['effective_pool_name'] = eff_name
            open_evt['effective_pool_no'] = int(b.get('effective_pool_no', -1) or -1)
        elif not failover and open_evt:
            # true -> false transition: close the event
            closed = dict(open_evt)
            closed['end_ts'] = sample_ts
            closed['duration_seconds'] = max(0, sample_ts - closed.get('start_ts', sample_ts))
            state['events'].append(closed)
            del state['open'][bid_key]

    # Trim retention. Open events are never trimmed (they're still active).
    state['events'] = [e for e in state['events'] if e.get('end_ts', 0) >= cutoff]
    state['updated'] = now
    state['max_age_seconds'] = RETENTION_DAYS * 86400
    state['version'] = 1

    write_json_atomic(EVENTS, state)
    return 0


if __name__ == '__main__':
    sys.exit(main())
