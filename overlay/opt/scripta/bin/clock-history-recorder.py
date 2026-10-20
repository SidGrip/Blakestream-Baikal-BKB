#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
clock-history-recorder.py - BK-B clock diagnostic history recorder.

When Settings -> Adjustable Clocks is enabled, polls sgminer's devs/stats APIs
once per minute and appends compact per-board samples to clock-history.json.
The file is kept to a 7-day rolling window. RAM mode writes directly to tmpfs.
SD mode records each minute into a tmpfs buffer, then flushes that buffer to
SD periodically so short-term diagnostics stay detailed without constant flash
writes.

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measured_rate_service import Service
from history_json import (HistorySample, compact_sample_hook,
                          encode_history_sample, write_history_json)

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)

ETC = '/opt/scripta/etc'
SCRIPTA_CONF = os.path.join(ETC, 'scripta.conf')
MINER_OPTIONS = os.path.join(ETC, 'miner.options.json')
RUNTIME_RAM = '/run/blakestream'
RUNTIME_SD = '/opt/scripta/var'
RAM_HISTORY = os.path.join(RUNTIME_RAM, 'clock-history.json')
SD_HISTORY = os.path.join(RUNTIME_SD, 'clock-history.json')
SD_BUFFER_HISTORY = os.path.join(RUNTIME_RAM, 'clock-history-sd-buffer.json')
ACCEPTED_RATES = os.path.join(RUNTIME_RAM, 'accepted-work-rates.json')
FAN_STATE = os.path.join(RUNTIME_RAM, 'fan-control-state.json')
RETENTION_DAYS = 7
STEP_SECONDS = 60
SD_FLUSH_SECONDS = 300
RATE_MIN_SECONDS = 300
RATE_MIN_ACCEPTED = 20
TEMP_MIN = 1
TEMP_MAX = 120


def load_json(path, default, compact=False):
    try:
        with open(path, 'r') as f:
            return json.load(f, object_hook=compact_sample_hook() if compact else None)
    except (IOError, ValueError):
        return default


def _try_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def miner_option_value(options, key, default=''):
    if not isinstance(options, list):
        return default
    for opt in options:
        if isinstance(opt, dict) and opt.get('key') == key:
            return opt.get('value', default)
    return default


def int_or_default(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def float_or_default(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def bool_or_default(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, string_types):
        text = value.strip().lower()
        if text in ('true', 'yes', 'y', '1'):
            return True
        if text in ('false', 'no', 'n', '0', ''):
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return default


def parse_baikal_options(value):
    clock = 400
    recover = 40
    cutoff = 55
    parts = str(value or '').split(':')

    if len(parts) >= 3:
        clock = int_or_default(parts[0], clock)
        recover = int_or_default(parts[1], recover)
        cutoff = int_or_default(parts[2], cutoff)
    elif len(parts) >= 2 and str(value or ''):
        # Backward-compatible old format: recover:cutoff.
        recover = int_or_default(parts[0], recover)
        cutoff = int_or_default(parts[1], cutoff)
    elif len(parts) == 1 and parts[0] != '':
        clock = int_or_default(parts[0], clock)

    if clock != 400:
        clock = 400
    if recover < TEMP_MIN:
        recover = TEMP_MIN
    if recover > TEMP_MAX:
        recover = TEMP_MAX
    if cutoff < TEMP_MIN:
        cutoff = TEMP_MIN
    if cutoff > TEMP_MAX:
        cutoff = TEMP_MAX
    if recover >= cutoff:
        recover = 40
        cutoff = 55
    return clock, recover, cutoff


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
            write_history_json(f, data)
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


def call_api(cmd):
    s = None
    try:
        s = socket.socket()
        s.settimeout(4)
        s.connect(('127.0.0.1', 4028))
        payload = ('{"command":"' + cmd + '"}').encode('ascii')
        s.sendall(payload)
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
        sys.stderr.write('clock-history: cannot query %s: %s\n' % (cmd, e))
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


def index_stats(stats_resp):
    out = {}
    for row in (stats_resp or {}).get('STATS', []) or []:
        ident = row.get('ID', '')
        if isinstance(ident, string_types) and ident.startswith('BKLU'):
            bid = ident[4:]
            try:
                out[int(bid)] = row
            except ValueError:
                pass
    return out


def pool_url_by_no(pools_resp):
    out = {}
    for p in (pools_resp or {}).get('POOLS', []) or []:
        no = p.get('POOL')
        try:
            out[int(no)] = p.get('URL', '')
        except (TypeError, ValueError):
            pass
    return out


def clean_pool_url(url):
    if not url:
        return ''
    s = str(url)
    if s.startswith('http://'):
        s = s[7:]
    elif s.startswith('https://'):
        s = s[8:]
    if s.startswith('quota:'):
        parts = s.split(';', 1)
        if len(parts) == 2:
            s = parts[1]
    for prefix in ('stratum+tcp://', 'stratum+ssl://', 'stratum+tls://'):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def empty_history():
    return {
        'version': 2,
        'step_seconds': STEP_SECONDS,
        'max_age_seconds': RETENTION_DAYS * 86400,
        'updated': 0,
        'boards': [],
    }


def newest_existing_path(paths):
    best_path = ''
    best_mtime = -1
    for path in paths:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = path
    return best_path


def load_clock_history(persist_sd):
    if persist_sd:
        newest = newest_existing_path([SD_BUFFER_HISTORY, SD_HISTORY])
        if newest:
            return load_json(newest, empty_history(), compact=True)
        return empty_history()
    return load_json(RAM_HISTORY, empty_history(), compact=True)


def should_flush_sd(force, now):
    if force or not os.path.exists(SD_HISTORY):
        return True
    try:
        return now - os.path.getmtime(SD_HISTORY) >= SD_FLUSH_SECONDS
    except OSError:
        return True


def empty_accepted_rate(reason='measuring'):
    return {
        'valid': False,
        'ghs': None,
        'seconds': 0,
        'accepted_delta': 0,
        'difficulty_delta': 0,
        'reason': reason,
    }


def accepted_work_rate(samples, window_seconds):
    result = empty_accepted_rate()
    valid_samples = [s for s in (samples or [])
                     if isinstance(s, (dict, HistorySample)) and 't' in s]
    if len(valid_samples) < 2:
        return result
    valid_samples.sort(key=lambda s: int_or_default(s.get('t'), 0))

    latest = valid_samples[-1]
    if not bool_or_default(latest.get('en'), False):
        return empty_accepted_rate('board-disabled')

    latest_t = int_or_default(latest.get('t'), 0)
    cutoff = latest_t - max(1, int(window_seconds))
    candidate = None
    for sample in reversed(valid_samples[:-1]):
        sample_t = int_or_default(sample.get('t'), 0)
        if sample_t < cutoff:
            break
        if not bool_or_default(sample.get('en'), False):
            break
        if int_or_default(sample.get('se'), -1) != int_or_default(latest.get('se'), -2):
            break
        if int_or_default(sample.get('cg'), -1) != int_or_default(latest.get('cg'), -2):
            break
        if any(sample.get(key) != latest.get(key)
               for key in ('xp', 'pi', 'scc', 'pid', 'hs', 'ds', 're', 'ee')):
            break
        if int_or_default(sample.get('ep'), -1) != int_or_default(latest.get('ep'), -2):
            break
        if str(sample.get('pool', '')) != str(latest.get('pool', '')):
            break
        if float_or_default(sample.get('da'), -1) > float_or_default(latest.get('da'), -2):
            break
        if int_or_default(sample.get('de'), -1) > int_or_default(latest.get('de'), -2):
            break
        candidate = sample

    if candidate is None:
        return result

    seconds = latest_t - int_or_default(candidate.get('t'), latest_t)
    accepted_delta = (int_or_default(latest.get('a'), 0) -
                      int_or_default(candidate.get('a'), 0))
    difficulty_delta = (float_or_default(latest.get('da'), 0.0) -
                        float_or_default(candidate.get('da'), 0.0))
    result['seconds'] = max(0, seconds)
    result['accepted_delta'] = max(0, accepted_delta)
    result['difficulty_delta'] = max(0.0, difficulty_delta)
    if (seconds < RATE_MIN_SECONDS or
            accepted_delta < RATE_MIN_ACCEPTED or
            difficulty_delta <= 0):
        return result

    result['valid'] = True
    result['ghs'] = (difficulty_delta * 4294967296.0) / seconds / 1000000000.0
    result['reason'] = ''
    return result


def accepted_rates_summary(history, now):
    summary = {
        'version': 1,
        'updated': int(now),
        'boards': {},
    }
    for board in history.get('boards', []) if isinstance(history, dict) else []:
        if not isinstance(board, dict) or 'id' not in board:
            continue
        bid = int_or_default(board.get('id'), -1)
        if bid < 0 or bid > 2:
            continue
        samples = board.get('samples', [])
        summary['boards'][str(bid)] = {
            '15m': accepted_work_rate(samples, 15 * 60),
            '1h': accepted_work_rate(samples, 60 * 60),
            'context': dict((key, samples[-1].get(key)) for key in
                            ('pid', 'hs', 'ds', 'cg', 're', 'ee', 'xp', 'pi', 'scc', 'ep', 'de')) if samples else {},
        }
    return summary


def main():
    force = '--force' in sys.argv[1:]
    cfg = load_json(SCRIPTA_CONF, {})
    persist_sd = bool(cfg.get('userPersistClockHistory'))
    history_path = SD_BUFFER_HISTORY if persist_sd else RAM_HISTORY

    options = load_json(MINER_OPTIONS, [])
    configured_fan = int_or_default(miner_option_value(options, 'baikal-fan', 0), 0)
    fan_state = load_json(FAN_STATE, {})
    auto_fan_enabled = bool(fan_state.get('enabled', cfg.get('userAutoFan', False)))
    fan_mode = fan_state.get('mode', 'auto' if auto_fan_enabled else 'manual')
    live_fan = int_or_default(fan_state.get('fan'), configured_fan)
    baikal_options = str(miner_option_value(options, 'baikal-options', '400:40:55'))
    requested_clock, recover_temp, cutoff_temp = parse_baikal_options(baikal_options)

    before = call_api('stats')
    devs_resp = call_api('devs')
    pools_resp = call_api('pools')
    after = call_api('stats')
    try:
        Service().record(before, after, devs_resp, pools_resp, cfg, options,
                         int(time.time()), force=force)
    except Exception as error:
        sys.stderr.write('measured-rates: %s\n' % error)
    if not cfg.get('userAdjustableClocks', False):
        return 0
    # Leave unrelated raw history untouched when its recorder is disabled.
    if persist_sd:
        _try_remove(RAM_HISTORY)
    else:
        _try_remove(SD_HISTORY)
        _try_remove(SD_BUFFER_HISTORY)
    devs = (devs_resp or {}).get('DEVS') or []
    if not devs:
        return 0
    stats_by_id = index_stats(after or {})
    pools_by_no = pool_url_by_no(pools_resp or {})

    history = load_clock_history(persist_sd)
    if 'boards' not in history or not isinstance(history['boards'], list):
        history['boards'] = []

    boards_by_id = {}
    for board in history.get('boards', []):
        if isinstance(board, dict) and 'id' in board:
            boards_by_id[board.get('id')] = board

    now = int(time.time())
    cutoff = now - (RETENTION_DAYS * 86400)

    for dev in devs:
        asc = dev.get('ASC')
        if asc is None:
            continue
        bid = int_or_default(asc, -1)
        if bid < 0 or bid > 2:
            continue
        stat = stats_by_id.get(bid, {})
        configured_clock = int_or_default(stat.get('Clock', dev.get('Clock', requested_clock)), requested_clock)
        requested = int_or_default(stat.get('RequestedClock', configured_clock), configured_clock)
        reported = int_or_default(stat.get('ReportedClock', configured_clock), configured_clock)
        reported_code = int_or_default(stat.get('ReportedClockCode', 0), 0)
        sent_clock_index = int_or_default(stat.get('SentClockIndex', 0), 0)
        experimental = bool_or_default(stat.get('ExperimentalClock', False))
        requested_clock_index = int_or_default(stat.get('RequestedClockIndex', 0), 0)
        live_clock_mode = str(stat.get('ClockMode', '') or '')
        if live_clock_mode not in ('global', 'individual'):
            live_clock_mode = 'incompatible'
        command_verified = bool_or_default(
            stat.get('CommandVerified', stat.get('AppliedClockValid', False)))
        if live_clock_mode == 'incompatible':
            command_verified = False
        fan = int_or_default(stat.get('FanPercent', live_fan), live_fan)
        profile_key = '%s:%d:%d:%d:%d:%s' % (
            live_clock_mode, requested, recover_temp, cutoff_temp, fan, fan_mode)
        if experimental:
            profile_key += ':clock_index:%d' % requested_clock_index
        temp = float_or_default(dev.get('Temperature', 0), 0.0)
        mhs5s = float_or_default(dev.get('MHS 5s', 0), 0.0)
        mhsav = float_or_default(dev.get('MHS av', 0), 0.0)
        accepted = int_or_default(dev.get('Accepted', 0), 0)
        rejected = int_or_default(dev.get('Rejected', 0), 0)
        hw = int_or_default(dev.get('Hardware Errors', 0), 0)
        utility = float_or_default(dev.get('Utility', 0), 0.0)
        primary = int_or_default(dev.get('Primary Pool', -1), -1)
        effective = int_or_default(dev.get('Effective Pool', primary), primary)

        board = boards_by_id.get(bid)
        if board is None:
            board = {'id': bid, 'samples': []}
            boards_by_id[bid] = board
        previous = board['samples'][-1] if board.get('samples') else {}
        stats_epoch = int_or_default(previous.get('se', 0), 0)
        diff_accepted = float_or_default(dev.get('Difficulty Accepted', 0), 0.0)
        device_elapsed = int_or_default(dev.get('Device Elapsed', 0), 0)
        clock_generation = int_or_default(stat.get('ClockGeneration', 0), 0)
        if previous:
            if accepted < int_or_default(previous.get('a', 0), 0):
                stats_epoch += 1
            elif diff_accepted < float_or_default(previous.get('da', 0), 0.0):
                stats_epoch += 1
            elif device_elapsed < int_or_default(previous.get('de', 0), 0):
                stats_epoch += 1

        board['enabled'] = (dev.get('Enabled') == 'Y')
        board['status'] = dev.get('Status', '')
        board['last_pool_url'] = clean_pool_url(pools_by_no.get(effective, ''))
        board['samples'].append({
            't': now,
            'c': reported,
            'rc': requested,
            'cc': configured_clock,
            'fcc': reported_code,
            'scc': sent_clock_index,
            'xp': 1 if experimental else 0,
            'pi': requested_clock_index,
            'pid': int_or_default(stat.get('DiagPid', 0), 0),
            'hs': str(stat.get('DiagHostSha256', '')),
            'ds': int_or_default(stat.get('DiagStatEpoch', 0), 0),
            're': int_or_default(stat.get('DiagRouteEpoch', 0), 0),
            'ee': int_or_default(stat.get('DiagEnableEpoch', 0), 0),
            'cv': 1 if command_verified else 0,
            'cm': live_clock_mode,
            'rec': recover_temp,
            'cut': cutoff_temp,
            'fan': fan,
            'fm': fan_mode,
            'prof': profile_key,
            'tmp': temp,
            'm5': mhs5s,
            'ma': mhsav,
            'a': accepted,
            'da': diff_accepted,
            'de': device_elapsed,
            'se': stats_epoch,
            'cg': clock_generation,
            'en': 1 if dev.get('Enabled') == 'Y' else 0,
            'r': rejected,
            'hw': hw,
            'u': utility,
            'pp': primary,
            'ep': effective,
            'fo': bool(dev.get('Failover Active', False)),
            'st': dev.get('Status', ''),
            'ls': int_or_default(dev.get('Last Share Time', 0), 0),
            'pool': clean_pool_url(pools_by_no.get(effective, '')),
        })
        board['samples'] = [s for s in board['samples']
                            if isinstance(s, (dict, HistorySample)) and s.get('t', 0) >= cutoff]

    history['boards'] = sorted(boards_by_id.values(), key=lambda x: x.get('id', 0))
    history['updated'] = now
    history['step_seconds'] = STEP_SECONDS
    history['max_age_seconds'] = RETENTION_DAYS * 86400
    history['version'] = 2
    write_json_atomic(history_path, history)
    if persist_sd and should_flush_sd(force, now):
        write_json_atomic(SD_HISTORY, history)
    return 0


if __name__ == '__main__':
    sys.exit(main())
