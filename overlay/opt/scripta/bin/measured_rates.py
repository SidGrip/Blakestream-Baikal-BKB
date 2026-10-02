from __future__ import division

import hashlib
import hmac
import json
import math
import re

MAPPING = 'bkb-v1-clock-index-1'
SETTLE_SECONDS = 120
MAX_GAP = 120
LIVE_MAX_GAP = MAX_GAP + 5
LIVE_RESTART_GAP = 900
HASHES_PER_DIFFICULTY = 4294967296.0
IDENTITY = ('DiagPid', 'DiagStartUnix', 'DiagHostSha256', 'DiagStatEpoch',
            'DiagRouteEpoch', 'DiagEnableEpoch', 'ClockGeneration', 'SentClockIndex',
            'ExperimentalClock', 'RequestedClockIndex', 'StartupSuccessCount',
            'Chip Count', 'ClockMode', 'Algo', 'CommandVerified', 'RequestedClock')
PENDING = ('GlobalClockPending', 'GlobalClockInflight', 'ClockRetryPending',
           'ClockReapplyPending', 'PendingClockReapply')


def number(value, integer=False, minimum=0, maximum=9007199254740991):
    if value is None or isinstance(value, bool):
        raise ValueError('missing numeric telemetry')
    n = float(value)
    if math.isnan(n) or math.isinf(n) or n < minimum or n > maximum:
        raise ValueError('invalid numeric telemetry')
    if integer and int(n) != n:
        raise ValueError('non-integer counter')
    return int(n) if integer else n


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def flag(value):
    return value is True or value == 1 or value == '1'


def rows(response, key):
    values = response.get(key) if isinstance(response, dict) else None
    return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []


def by_board(response):
    result = {}
    for row in rows(response, 'STATS'):
        match = re.match(r'^BKLU([0-2])$', str(row.get('ID', '')))
        if match:
            result[int(match.group(1))] = row
    return result


def samples_from_api(before, after, devs, pools, cfg, options, firmware,
                     miner_config, meta, now, boot_id):
    """Only co-snapshotted Diag counters qualify; devs supplies observations."""
    first, last = by_board(before), by_board(after)
    devices = dict((int(d['ASC']), d) for d in rows(devs, 'DEVS')
                   if str(d.get('ASC', '')) in ('0', '1', '2'))
    routes = dict((int(p['POOL']), p) for p in rows(pools, 'POOLS')
                  if str(p.get('POOL', '')).isdigit())
    result = {}
    for board in range(3):
        try:
            if not all(isinstance(v, dict) for v in (cfg, firmware, miner_config, meta)) or not isinstance(options, list):
                raise ValueError('configuration-unavailable')
            if any(not isinstance(o, dict) for o in options):
                raise ValueError('configuration-unavailable')
            a, b, dev = first[board], last[board], devices[board]
            if any(k not in a or k not in b or a[k] != b[k] for k in IDENTITY):
                raise ValueError('context-changing')
            if not all(flag(row.get(k)) for row in (a, b) for k in
                       ('DiagShareSnapValid', 'DiagDeviceElapsedValid')):
                raise ValueError('unconfirmed-snapshot')
            if b['CommandVerified'] not in (True, False, '0', '1'):
                raise ValueError('invalid-clock-confirmation')
            if any(flag(row.get(k)) for row in (a, b) for k in PENDING):
                raise ValueError('command-pending')
            if dev.get('Enabled') != 'Y' or dev.get('Status') != 'Alive':
                raise ValueError('board-disabled-or-unavailable')
            for key in ('DiagPid', 'DiagStartUnix'):
                number(b[key], True, 1)
            for key in ('DiagStatEpoch', 'DiagRouteEpoch', 'DiagEnableEpoch', 'ClockGeneration'):
                number(b[key], True)
            host = str(b['DiagHostSha256'])
            if not re.match(r'^[0-9a-f]{64}$', host):
                raise ValueError('host-identity-unavailable')
            bridge = firmware.get('chip_md5', '')
            bridge_verified = bool(re.match(r'^[0-9a-f]{32}$', str(bridge)) and firmware.get('at'))
            route_no = int(dev['Effective Pool'])
            route = routes[route_no]
            if not route.get('URL') or 'User' not in route:
                raise ValueError('pool-identity-unavailable')
            # HMAC prevents exported worker/config revisions from being dictionary hashes.
            route_private = {'url': route['URL'], 'worker': route['User'],
                             'pools': [{'url': p.get('url'), 'user': p.get('user')}
                                       for p in rows(miner_config, 'pools')]}
            route_revision = hmac.new(str(meta['salt']).encode('ascii'),
                                      canonical(route_private).encode('utf-8'), hashlib.sha256).hexdigest()
            cooling = dict((k, cfg.get(k)) for k in ('userAutoFan', 'userAutoFanMin',
                'userAutoFanTarget', 'userAutoFanMidTemp', 'userAutoFanMidFan', 'userAutoFanFull',
                'userAutoFanEmergency', 'userAutoFanStep', 'userAutoFanMaxDownStep'))
            cooling['options'] = [o for o in options if o.get('key') in ('baikal-options', 'baikal-fan')]
            startup = number(b['StartupSuccessCount'], True, 1, 64)
            total = number(b['Chip Count'], True, startup, 64)
            indices = [number(last[i]['SentClockIndex'], True, 0, 32) for i in range(3)]
            if any(0 < index < 8 for index in indices):
                raise ValueError('invalid-clock-index')
            # Zero means no acknowledged clock command, not an observed index.
            indices = [index if index else None for index in indices]
            clock_verified = flag(b['CommandVerified'])
            history_verified = bridge_verified and clock_verified and all(index is not None for index in indices)
            context = {'board': board, 'installation': meta['board_ids'][str(board)],
                'algorithm': str(b['Algo']), 'host': host,
                'host_provenance': 'driver-running-binary-sha256',
                'bridge': str(bridge) if bridge_verified else None,
                'bridge_verified_at': firmware.get('at') if bridge_verified else None,
                'bridge_provenance': 'saved-chip-verification-record' if bridge_verified else 'unverified',
                'clock_verified': clock_verified,
                'mode': b['ClockMode'], 'experimental': flag(b['ExperimentalClock']),
                'index': indices[board], 'indices': indices, 'mapping': MAPPING,
                'startup': startup, 'total': total, 'cooling': cooling, 'route': route_revision}
            if context['mode'] not in ('global', 'individual') or not context['algorithm']:
                raise ValueError('unknown-operating-profile')
            live_context = dict((short, b[long]) for short, long in (
                ('pid', 'DiagPid'), ('start', 'DiagStartUnix'), ('hs', 'DiagHostSha256'),
                ('ds', 'DiagStatEpoch'), ('cg', 'ClockGeneration'), ('re', 'DiagRouteEpoch'),
                ('ee', 'DiagEnableEpoch'), ('scc', 'SentClockIndex'), ('pi', 'RequestedClockIndex')))
            live_context.update({'cv': 1 if clock_verified else 0,
                                 'xp': 1 if flag(b['ExperimentalClock']) else 0,
                                 'ep': route_no, 'startup': startup, 'chips': total,
                                 'algo': b['Algo'], 'mode': b['ClockMode']})
            requested = number(b['RequestedClock'], True)
            requested_index = number(b['RequestedClockIndex'], True, 0, 32)
            # This is a requested operating profile, never proof of an applied clock.
            target_index = requested_index if flag(b['ExperimentalClock']) else (30 if requested == 400 else None)
            if target_index is not None and not 8 <= target_index <= 32:
                target_index = None
            live_profile = dict((key, context[key]) for key in
                ('board', 'installation', 'algorithm', 'host', 'mapping', 'startup', 'total', 'route'))
            live_profile['target_index'] = target_index
            sample = {'t': now, 'context': context, 'context_id': digest(context),
                      'history_verified': history_verified,
                      'boot_id': boot_id,
                      'live_context': live_context,
                      'live_profile': live_profile,
                      'segment': digest([live_context, boot_id]),
                      'de': number(b['DiagDeviceElapsed']),
                      'mono': number(b['DiagSnapMonoUs']) / 1000000.0,
                      'a': number(b['DiagAccepted'], True),
                      'da': number(b['DiagDiffAccepted']),
                      'r': number(b['DiagRejected'], True),
                      'hw': number(b['DiagHwErrors'], True),
                      'temp': number(dev['Temperature'], minimum=-20, maximum=150),
                      'fan': number(b['FanPercent'], maximum=100)}
            # An interval's average difficulty alone cannot prove fixed difficulty.
            sample['fixed32'] = (route.get('Minimum Difficulty') == 32 and
                                 route.get('Maximum Difficulty') == 32)
            result[str(board)] = sample
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            reason = str(error) if isinstance(error, ValueError) else 'telemetry-unavailable'
            result[str(board)] = {'t': now, 'error': reason}
    return result


def interval(previous, current, live=False):
    if previous.get('live_profile') and current.get('live_profile') and previous['live_profile'] != current['live_profile']:
        return None
    same_segment = previous['segment'] == current['segment']
    if live and not same_segment and previous.get('boot_id') and previous.get('boot_id') == current.get('boot_id'):
        before = dict(previous['live_context'])
        after = dict(current['live_context'])
        old_route, new_route = before.pop('re'), after.pop('re')
        # A reconnect to the same pool does not reset cumulative accepted work.
        same_segment = before == after and new_route >= old_route
    if not same_segment or previous['context_id'] != current['context_id']:
        return None
    seconds = current['de'] - previous['de']
    mono = current['mono'] - previous['mono']
    wall = current['t'] - previous['t']
    max_gap = LIVE_MAX_GAP if live else MAX_GAP
    if not (0 < seconds <= max_gap and 0 < mono <= max_gap and 0 < wall <= max_gap):
        return None
    if abs(seconds - mono) > 5 or abs(seconds - wall) > 5:
        return None
    deltas = dict((k, current[k] - previous[k]) for k in ('a', 'da', 'r', 'hw'))
    if any(v < 0 for v in deltas.values()):
        return None
    if (deltas['a'] == 0) != (deltas['da'] == 0):
        return None
    # Fail closed on counter jumps orders of magnitude beyond this hardware.
    if deltas['a'] / seconds > 100000 or deltas['da'] * HASHES_PER_DIFFICULTY / seconds > 1e14:
        return None
    temp = (previous['temp'] + current['temp']) / 2
    fan = (previous['fan'] + current['fan']) / 2
    return {'context_id': current['context_id'], 'context': current['context'],
            'end': current['t'], 'seconds': seconds, 'difficulty': deltas['da'],
            'accepted': deltas['a'], 'rejected': deltas['r'], 'hw': deltas['hw'],
            'temp_sum': temp * seconds, 'temp_min': min(previous['temp'], current['temp']),
            'temp_max': max(previous['temp'], current['temp']), 'fan_sum': fan * seconds,
            'fixed32': bool(previous.get('fixed32') and current.get('fixed32') and
                            abs(deltas['da'] - 32 * deltas['a']) < 1e-8)}


def compatible_live_restart(previous, current):
    if not previous or not previous.get('boot_id') or previous['boot_id'] != current.get('boot_id'):
        return False
    profile = current.get('live_profile', {})
    if profile.get('target_index') is None or profile != previous.get('live_profile'):
        return False
    wall, mono = current['t'] - previous['t'], current['mono'] - previous['mono']
    if not (0 < wall <= LIVE_RESTART_GAP and 0 < mono <= LIVE_RESTART_GAP and abs(wall - mono) <= 5):
        return False
    before, after = previous['live_context'], current['live_context']
    for sample in (previous, current):
        sent = sample['live_context']['scc']
        if sent and sent != profile['target_index']:
            return False
    if (before['pid'], before['start']) != (after['pid'], after['start']):
        return after['start'] >= before['start']
    # A changed verification/cooling record can re-anchor the live window, but
    # unexplained counter resets, clock commands and enable epochs cannot.
    if any(before[key] != after[key] for key in ('ds', 'cg', 'ee')) or after['re'] < before['re']:
        return False
    if any(current[key] < previous[key] for key in ('de', 'a', 'da', 'r', 'hw')):
        return False
    return previous['context_id'] != current['context_id'] or before != after


def age_live_window(state, previous, now):
    window = []
    for value in state.get('window', []):
        item = dict(value)
        if 'end' not in item and previous and 'de' in item:
            # Upgrade existing RAM windows without resetting their observations.
            item['end'] = previous['mono'] + item['de'] - previous['de']
        item.pop('de', None)
        if 'end' in item and 0 <= now - item['end'] < 3600:
            window.append(item)
    state['window'] = window[-360:]


def reduce_samples(meta, runtime, samples):
    boards = runtime.setdefault('boards', {})
    records = []
    for bid in ('0', '1', '2'):
        current = samples.get(bid, {'error': 'telemetry-unavailable'})
        state = boards.setdefault(bid, {})
        previous = state.get('anchor')
        if current.get('error'):
            if previous and 0 <= current.get('t', 0) - previous['t'] <= LIVE_RESTART_GAP:
                state.update({'reason': current['error'], 'unavailable': True,
                              'history_settling': 0})
            else:
                boards[bid] = {'reason': current['error'], 'window': []}
            continue
        record = interval(previous, current) if previous and not state.get('unavailable') else None
        history_seconds = state.get('history_settling', state.get('settling', 0))
        history_settled = record is not None and history_seconds >= SETTLE_SECONDS
        history_seconds = history_seconds + record['seconds'] if record is not None else 0
        live_record = interval(previous, current, live=True) if previous else None
        age_live_window(state, previous, current['mono'])
        if live_record is None:
            window = state['window'] if compatible_live_restart(previous, current) else []
            state = {'settling': 0, 'window': window, 'reason': 'settling'}
            boards[bid] = state
        else:
            settled = state.get('settling', 0) >= SETTLE_SECONDS
            state['settling'] = state.get('settling', 0) + live_record['seconds']
            if settled:
                # Windows contain disjoint intervals, never derived moving averages.
                item = dict((k, live_record[k]) for k in ('seconds', 'difficulty', 'accepted', 'rejected', 'hw'))
                item['end'] = current['mono']
                state['window'].append(item)
                state['window'] = state['window'][-360:]
                state['reason'] = 'measuring'
        # Verified history still requires an uninterrupted, settled pool route.
        if history_settled and previous.get('history_verified') and current.get('history_verified'):
            records.append(record)
        state['history_settling'] = history_seconds
        state['unavailable'] = False
        state['anchor'] = current
    return records


def rate(window, span, reason='measuring', now=None):
    values = []
    seconds = 0
    span_reached = False
    if now is None and window:
        now = window[-1].get('end')
    newer_start = now
    contiguous = True
    for item in reversed(window):
        if now is not None:
            end = item.get('end')
            if end is None or end > now or end <= now - span:
                continue
            if newer_start - end > 1:
                contiguous = False
            if end - item['seconds'] < now - span - 0.001:
                span_reached = True
                break
            newer_start = end - item['seconds']
        if seconds + item['seconds'] > span + 0.001:
            # Whole sample intervals rarely sum to an exact hour under jitter.
            span_reached = True
            break
        values.append(item)
        seconds += item['seconds']
    work = sum(v['difficulty'] for v in values)
    accepted = sum(v['accepted'] for v in values)
    valid = seconds >= 300 and (accepted >= 20 or work == 0)
    return {'valid': valid, 'ghs': work * HASHES_PER_DIFFICULTY / seconds / 1e9 if valid else None,
            'seconds': seconds, 'coverage': min(1, seconds / span),
            'full_window': bool(valid and contiguous and (span_reached or seconds >= span)),
            'accepted_delta': accepted, 'difficulty_delta': work,
            'reason': '' if valid else reason}


def public_summary(status, now):
    summary = dict((k, v) for k, v in status.items() if k != 'runtime')
    summary['version'] = 2
    summary['updated'] = now
    summary['boards'] = {}
    contexts = dict((c['context_id'], c) for c in status.get('contexts', []))
    for bid, state in status.get('runtime', {}).get('boards', {}).items():
        anchor = state.get('anchor', {})
        context = anchor.get('live_context', {})
        window = state.get('window', [])
        if state.get('unavailable'):
            context, window = {}, []
        if anchor and (now - anchor.get('t', 0) > MAX_GAP or now < anchor.get('t', 0)):
            context, window = {}, []
            state = {'reason': 'stale'}
        board = {'context': context, 'context_id': anchor.get('context_id'),
                 '15m': rate(window, 900, state.get('reason', 'measuring'), anchor.get('mono')),
                 '1h': rate(window, 3600, state.get('reason', 'measuring'), anchor.get('mono')),
                 'personal': None}
        historical = contexts.get(anchor.get('context_id')) if context and anchor.get('history_verified') else None
        if historical and historical['seconds'] > 0:
            personal = dict(historical)
            personal['ghs'] = personal['difficulty'] * HASHES_PER_DIFFICULTY / personal['seconds'] / 1e9
            personal['qualified'] = (personal['seconds'] >= 21600 and personal['accepted'] >= 10000
                                     and personal['fixed32'] and not status.get('error'))
            personal['source'] = 'Your history' if personal['qualified'] else 'Early history'
            board['personal'] = personal
        summary['boards'][bid] = board
    return summary
