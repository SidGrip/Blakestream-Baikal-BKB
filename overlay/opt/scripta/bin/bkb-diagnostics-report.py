#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bkb-diagnostics-report.py - exact-window experiment report.

GH/s is only delta(difficulty accepted) * 2^32 / seconds / 1e9.
Invalid gaps are rejected. They are not interpolated or replaced with
the chip-clock estimate.

A settled unverified clock command does not reject a clean accepted-work
window. Equivalence is a separate, stricter claim.

Python 2.7 and Python 3, standard library only.
"""

from __future__ import print_function, division

import argparse
import json
import math
import os
import sys

try:
    string_types = (basestring,)
    text_type = unicode
except NameError:
    string_types = (str,)
    text_type = str

HASHES_PER_DIFFICULTY = 4294967296.0
GHS_SCALE = 1000000000.0
MAX_GAP_SECONDS = 120.0
TIME_JUMP_SECONDS = 1.0
ELAPSED_SKEW_SECONDS = 3.0
MIN_EQUIV_SECONDS = 6 * 3600.0
MAX_EQUIV_SECONDS = 12 * 3600.0
MIN_EQUIV_ACCEPTED = 10000
EQUIV_LO = 0.95
EQUIV_HI = 1.05
Z95 = 1.959963984540054
REQUIRED_FAN = 80
REQUIRED_REQUESTED_CLOCK = 400
REQUIRED_CLOCK_MODE = 'global'
FORMULA = 'delta_difficulty_accepted * 2^32 / seconds / 1e9'

PROVENANCE_HOST_RUNNING = 'running-binary-file-sha256'
PROVENANCE_HOST_CONFIGURED = 'configured-path-sha256-not-verified-running'
PROVENANCE_ARTIFACT = 'on-disk-artifact-not-chip-readback'
HOST_PROVENANCE = (PROVENANCE_HOST_RUNNING, PROVENANCE_HOST_CONFIGURED)
# Collector stamps this source when the running sha came from the driver,
# because www-data could not open /proc/PID/exe. It is not a third provenance.
DRIVER_HOST_SHA = 'DiagHostSha256'

DIAG_MONOTONIC = (
    'invalid_id',
    'invalid_workidx',
    'missing_work',
    'algo_mismatch',
    'short_reply',
    'reply_mismatch',
    'work_requests',
    'work_sends',
    'skip_overheat',
    'skip_no_target',
    'skip_disabled',
    'skip_empty',
    'usb_tx_error',
    'usb_rx_error',
    'work_wait_us_sum',
    'work_wait_us_max',
    'work_wait_count',
    'ack_latency_us_sum',
    'ack_latency_us_max',
    'ack_latency_count',
    'request_to_ack_us_sum',
    'request_to_ack_us_max',
    'request_to_ack_count',
    'poll_gap_us_max',
    'uptime_us',
    'trace_overwritten',
    'trace_dropped',
    'trace_recorded',
)
DIAG_LEVELS = (
    'poll_gap_us_last',
    'attempted_clock_code',
    'info_valid',
    'last_info_ok',
    'last_result_ok',
    'trace_enabled',
    'trace_live',
    'trace_used',
    'trace_capacity',
)
CORE_MONOTONIC = ('a', 'da', 'de', 'rj', 'hw', 'dr')

REASON_ORDER = (
    'window-order',
    'window-uncovered',
    'board-missing',
    'too-few-samples',
    'duplicate-sample',
    'time-missing',
    'timebase-changed',
    'non-monotonic-time',
    'time-jump',
    'long-gap',
    'restart',
    'process-epoch-missing',
    'stats-epoch',
    'epoch-source-changed',
    'counter-reset',
    'counter-wrap',
    'counter-source-changed',
    'difficulty-missing',
    'accepted-missing',
    'accepted-not-integral',
    'difficulty-zero',
    'difficulty-without-accepted',
    'elapsed-missing',
    'disabled',
    'not-alive',
    'enabled-missing',
    'alive-missing',
    'enable-epoch-missing',
    'enablement-epoch',
    'pool-missing',
    'pool-index-missing',
    'worker-missing',
    'worker-change',
    'route-change',
    'route-epoch-missing',
    'route-epoch',
    'clock-state-missing',
    'clock-mode',
    'clock-requested',
    'clock-generation',
    'clock-pending',
    'clock-inflight',
    'clock-verification-changed',
    'clock-sent-code',
    'clock-attempt',
    'host-changed',
    'firmware-changed',
    'leg-invalid',
    'host-mismatch',
    'host-identity-missing',
    'host-provenance',
    'host-provenance-mismatch',
    'host-not-running-binary',
    'firmware-mismatch',
    'firmware-identity-missing',
    'firmware-label-not-distinct',
    'exceeds-12h-cap',
    'shorter-than-6h',
    'accepted-below-10000',
    'need-two-v1-legs',
    'stock-leg-missing',
    'extra-v1-leg',
    'extra-stock-leg',
    'endpoint-mismatch',
    'worker-not-retained',
    'workers-not-unique',
    'failover-not-off',
    'fan-not-80',
    'requested-clock-not-400',
    'clock-mode-not-global',
    'windows-overlap',
    'windows-out-of-order',
    'variable-difficulty',
    'zero-accepted',
    'difficulty-mismatch',
    'verification-unknown',
    'ratio-outside',
    'ratio-overlap',
    'v1-legs-inconsistent',
    'ghs-count-ratio-mismatch',
    'collector-epoch',
    'unsynchronized-api-reads',
    'startup-changed',
    'contribution-gap',
    'host-provenance-changed',
    'artifact-file-changed',
    'new-hw-errors',
    'health-interruption',
)


def order_reasons(reasons):
    found = set(reasons or [])
    ordered = [reason for reason in REASON_ORDER if reason in found]
    for reason in reasons or []:
        if reason not in ordered:
            ordered.append(reason)
    return ordered


def is_finite(value):
    return value == value and value not in (float('inf'), float('-inf'))


def parse_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not is_finite(value) or value != math.trunc(value):
            return None
        return int(value)
    if isinstance(value, int) or type(value).__name__ == 'long':
        return int(value)
    if isinstance(value, string_types):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return parse_int(number)
    return None


def parse_float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or type(value).__name__ == 'long':
        number = float(value)
    elif isinstance(value, string_types):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not is_finite(number):
        return None
    return number


def lowercase_sha256(value):
    if not isinstance(value, string_types):
        return None
    text = to_text(value)
    if not text or len(text) != 64 or text != text.lower():
        return None
    for char in text:
        if char not in '0123456789abcdef':
            return None
    return text


def driver_reported_sha_ok(host):
    """A DiagHostSha256 host counts only with a lowercase digest and running provenance."""
    if not isinstance(host, dict) or host.get('source') != DRIVER_HOST_SHA:
        return True
    if host.get('provenance') != PROVENANCE_HOST_RUNNING:
        return False
    return lowercase_sha256(host.get('sha256')) is not None


def to_text(value):
    if value is None:
        return None
    if isinstance(value, text_type):
        return value
    try:
        binary_type = bytes
    except NameError:
        binary_type = str
    if isinstance(value, binary_type) and not isinstance(value, text_type):
        return value.decode('utf-8', 'replace')
    return text_type(value)


def strip_wrappers(text):
    """Remove the http:// and quota:N; wrappers sgminer stores around stratum URLs."""
    changed = True
    while changed and text:
        changed = False
        lower = text.lower()
        if lower.startswith('https://'):
            text = text[8:]
            changed = True
            continue
        if lower.startswith('http://'):
            text = text[7:]
            changed = True
            continue
        if lower.startswith('quota:'):
            rest = text[6:]
            digits = 0
            while digits < len(rest) and rest[digits].isdigit():
                digits += 1
            if digits and digits < len(rest) and rest[digits] == ';':
                text = rest[digits + 1:]
                changed = True
    return text.strip()


def endpoint_from_url(url):
    if url is None:
        return None
    text = to_text(url).strip()
    if not text:
        return None
    text = strip_wrappers(text)
    text = text.split('#', 1)[0].split('?', 1)[0].strip()
    if '://' in text:
        text = text.split('://', 1)[1]
    text = text.split('/', 1)[0].strip()
    if not text:
        return None
    if text.startswith('['):
        if text.find(']') < 0:
            return None
        hostport = text
    elif '@' in text:
        hostport = text.rsplit('@', 1)[1].strip()
    else:
        hostport = text
    if not hostport or '@' in hostport or '://' in hostport or len(hostport) > 128:
        return None
    return hostport


def measured_ghs(difficulty_delta, seconds):
    if difficulty_delta is None or seconds is None:
        return None
    if seconds <= 0 or difficulty_delta < 0:
        return None
    return (float(difficulty_delta) * HASHES_PER_DIFFICULTY) / float(seconds) / GHS_SCALE


def duration_class(seconds):
    if seconds is None:
        return None
    if seconds > MAX_EQUIV_SECONDS:
        return 'over-cap'
    if seconds < MIN_EQUIV_SECONDS:
        return 'screening'
    return 'standard'


def band_class(lo, hi):
    if lo is None or hi is None:
        return None
    if lo >= EQUIV_LO and hi <= EQUIV_HI:
        return 'inside'
    if hi < EQUIV_LO or lo > EQUIV_HI:
        return 'outside'
    return 'overlap'


def poisson_rate_ratio_ci(n1, t1, n2, t2):
    if n1 is None or n2 is None or t1 is None or t2 is None:
        return None
    if n1 <= 0 or n2 <= 0 or t1 <= 0 or t2 <= 0:
        return None
    ratio = (float(n1) / float(t1)) / (float(n2) / float(t2))
    if not is_finite(ratio) or ratio <= 0:
        return None
    se = math.sqrt((1.0 / float(n1)) + (1.0 / float(n2)))
    log_r = math.log(ratio)
    return {
        'ratio': ratio,
        'lo': math.exp(log_r - Z95 * se),
        'hi': math.exp(log_r + Z95 * se),
        'n1': int(n1),
        'n2': int(n2),
        't1': float(t1),
        't2': float(t2),
        'method': 'wald-log-poisson-rate-ratio',
        'z': Z95,
    }


def time_key(value):
    number = parse_float(value)
    if number is None:
        return None
    return int(round(number * 1000.0))


def decreased(previous, current):
    if isinstance(previous, float) or isinstance(current, float):
        return float(current) < float(previous) - 1e-6
    return int(current) < int(previous)


def mono_keyset(diag):
    if not isinstance(diag, dict):
        return ()
    return tuple(key for key in DIAG_MONOTONIC if key in diag)


def core_keyset(sample):
    return tuple(key for key in CORE_MONOTONIC if sample.get(key) is not None)


def prepare_sample(sample):
    if not isinstance(sample, dict):
        return None
    prepared = dict(sample)
    prepared['pool'] = endpoint_from_url(sample.get('pool'))
    if isinstance(sample.get('wk'), string_types):
        worker = to_text(sample.get('wk')).strip()
        prepared['wk'] = worker if worker else None
    return prepared


def board_samples(history, bid, start, end):
    for board in (history or {}).get('boards') or []:
        if not isinstance(board, dict) or parse_int(board.get('id')) != int(bid):
            continue
        selected = []
        for sample in board.get('samples') or []:
            prepared = prepare_sample(sample)
            if prepared is None or prepared.get('t') is None:
                continue
            stamp = float(prepared['t'])
            if stamp + 1e-9 >= float(start) and stamp - 1e-9 <= float(end):
                selected.append(prepared)
        selected.sort(key=lambda item: float(item['t']))
        return selected
    return None


def timebase_delta(older, newer):
    if (older.get('dms') is None) != (newer.get('dms') is None):
        return None, 'timebase-changed'
    if older.get('t') is None or newer.get('t') is None or older.get('tm') is None or newer.get('tm') is None:
        return None, 'time-missing'
    if older.get('dms') is not None:
        delta = (float(newer['dms']) - float(older['dms'])) / 1e6
    else:
        delta = float(newer['tm']) - float(older['tm'])
    return delta, None


def pair_faults(older, newer):
    faults = []
    delta, fault = timebase_delta(older, newer)
    if fault:
        return [fault]
    wall = float(newer['t']) - float(older['t'])
    if delta <= 0 or wall <= 0:
        return ['non-monotonic-time']
    if abs(wall - delta) > TIME_JUMP_SECONDS:
        faults.append('time-jump')
    if delta > MAX_GAP_SECONDS or wall > MAX_GAP_SECONDS:
        faults.append('long-gap')
    if older.get('de') is None or newer.get('de') is None:
        faults.append('elapsed-missing')
    else:
        elapsed = float(newer['de']) - float(older['de'])
        if elapsed < -1e-6:
            faults.append('counter-reset')
        elif abs(elapsed - delta) > ELAPSED_SKEW_SECONDS:
            faults.append('time-jump')
    if older.get('pe') is None or newer.get('pe') is None:
        faults.append('process-epoch-missing')
    elif older.get('pe') != newer.get('pe'):
        faults.append('restart')
    if older.get('se') != newer.get('se'):
        faults.append('stats-epoch')
    if (older.get('ses') != newer.get('ses') or older.get('rs') != newer.get('rs') or
            older.get('es') != newer.get('es')):
        faults.append('epoch-source-changed')
    if core_keyset(older) != core_keyset(newer) or mono_keyset(older.get('diag')) != mono_keyset(newer.get('diag')):
        faults.append('counter-source-changed')
    for key in CORE_MONOTONIC:
        if older.get(key) is None or newer.get(key) is None:
            continue
        if decreased(older.get(key), newer.get(key)):
            if key == 'de' and 'counter-reset' in faults:
                continue
            faults.append('counter-reset' if key in CORE_MONOTONIC else 'counter-wrap')
    old_diag = older.get('diag') or {}
    new_diag = newer.get('diag') or {}
    for key in DIAG_MONOTONIC:
        if key in old_diag and key in new_diag and decreased(old_diag[key], new_diag[key]):
            faults.append('counter-wrap')
    if older.get('en') != newer.get('en'):
        faults.append('enablement-epoch')
    if older.get('ee') is None or newer.get('ee') is None:
        faults.append('enable-epoch-missing')
    elif older.get('ee') != newer.get('ee'):
        faults.append('enablement-epoch')
    if older.get('pool') != newer.get('pool') or older.get('ep') != newer.get('ep') or older.get('fo') != newer.get('fo'):
        faults.append('route-change')
    if older.get('wk') != newer.get('wk'):
        faults.append('worker-change')
    if older.get('re') is None or newer.get('re') is None:
        faults.append('route-epoch-missing')
    elif older.get('re') != newer.get('re'):
        faults.append('route-epoch')
    if older.get('cm') != newer.get('cm'):
        faults.append('clock-mode')
    if older.get('rc') != newer.get('rc'):
        faults.append('clock-requested')
    if older.get('cg') != newer.get('cg'):
        faults.append('clock-generation')
    if older.get('cv') != newer.get('cv') or older.get('vg') != newer.get('vg'):
        faults.append('clock-verification-changed')
    if (older.get('sc') is None) != (newer.get('sc') is None) or (
            older.get('sc') is not None and older.get('sc') != newer.get('sc')):
        faults.append('clock-sent-code')
    old_attempt = (older.get('diag') or {}).get('attempted_clock_code') if isinstance(older.get('diag'), dict) else None
    new_attempt = (newer.get('diag') or {}).get('attempted_clock_code') if isinstance(newer.get('diag'), dict) else None
    if (old_attempt is None) != (new_attempt is None) or (
            old_attempt is not None and old_attempt != new_attempt):
        faults.append('clock-attempt')
    if older.get('hid') != newer.get('hid'):
        faults.append('host-changed')
    if older.get('fl') != newer.get('fl') or older.get('fw') != newer.get('fw'):
        faults.append('firmware-changed')
    return faults


def sample_faults(sample):
    faults = []
    if sample.get('t') is None or sample.get('tm') is None:
        faults.append('time-missing')
    if sample.get('a') is None:
        faults.append('accepted-missing')
    if sample.get('bad') and 'accepted' in (sample.get('bad') or []):
        faults.append('accepted-not-integral')
    if sample.get('da') is None:
        faults.append('difficulty-missing')
    if sample.get('de') is None:
        faults.append('elapsed-missing')
    if sample.get('pe') is None:
        faults.append('process-epoch-missing')
    if sample.get('se') is None:
        faults.append('stats-epoch')
    if (sample.get('ses') not in ('d', 'c') or sample.get('rs') not in ('d', 'c') or
            sample.get('es') not in ('d', 'c')):
        faults.append('epoch-source-changed')
    if sample.get('en') is None:
        faults.append('enabled-missing')
    elif sample.get('en') != 1:
        faults.append('disabled')
    if sample.get('al') is None:
        faults.append('alive-missing')
    elif sample.get('al') != 1:
        faults.append('not-alive')
    if sample.get('ee') is None:
        faults.append('enable-epoch-missing')
    if sample.get('pool') is None:
        faults.append('pool-missing')
    if sample.get('ep') is None:
        faults.append('pool-index-missing')
    if sample.get('wk') is None:
        faults.append('worker-missing')
    if sample.get('re') is None:
        faults.append('route-epoch-missing')
    for key, reason in (
        ('cm', 'clock-state-missing'),
        ('rc', 'clock-state-missing'),
        ('cg', 'clock-state-missing'),
        ('pend', 'clock-state-missing'),
        ('inf', 'clock-state-missing'),
    ):
        if sample.get(key) is None:
            faults.append(reason)
    if sample.get('pend') == 1:
        faults.append('clock-pending')
    if sample.get('inf') == 1:
        faults.append('clock-inflight')
    return faults


def fixed_difficulty(samples):
    ratios = []
    for index in range(1, len(samples)):
        accepted = parse_int(samples[index].get('a'))
        previous = parse_int(samples[index - 1].get('a'))
        new_da = parse_float(samples[index].get('da'))
        old_da = parse_float(samples[index - 1].get('da'))
        if None in (accepted, previous, new_da, old_da):
            continue
        count = accepted - previous
        difficulty = new_da - old_da
        if count == 0 and abs(difficulty) <= 1e-6:
            continue
        if count <= 0 or difficulty <= 0:
            continue
        ratios.append(difficulty / float(count))
    if not ratios:
        return None, 'zero-accepted'
    base = ratios[0]
    for ratio in ratios[1:]:
        if abs(ratio - base) > 1e-4:
            return None, 'variable-difficulty'
    return base, None


def verification_state(samples, faults):
    blocking = set(faults or [])
    if blocking.intersection(set([
            'clock-pending', 'clock-inflight', 'clock-generation', 'clock-mode',
            'clock-requested', 'clock-verification-changed', 'clock-sent-code',
            'clock-attempt', 'clock-state-missing'])):
        return None
    if any(sample.get('cv') is None for sample in samples):
        return None
    if any(sample.get('cv') != 1 for sample in samples):
        return True
    for sample in samples:
        if sample.get('vg') is None or sample.get('cg') is None or sample.get('vg') != sample.get('cg'):
            return True
    return False


def host_record(history, sample):
    hid = sample.get('hid')
    if hid is None:
        return None
    for row in (history or {}).get('host') or []:
        if isinstance(row, dict) and row.get('id') == hid:
            return row
    return None


def sync_of(samples):
    """Co-snapshot only when every sample in the window says so."""
    if not samples:
        return None
    for sample in samples:
        if sample.get('sync') != 'co-snapshot':
            return 'unsynchronized'
    return 'co-snapshot'


def new_hw_errors(samples):
    for index in range(1, len(samples)):
        old = parse_float(samples[index - 1].get('hw'))
        new = parse_float(samples[index].get('hw'))
        if old is None or new is None:
            continue
        if new > old + 1e-9:
            return True
    return False


def health_interrupted(samples):
    for sample in samples:
        if sample.get('en') == 0 or sample.get('al') == 0:
            return True
        diag = sample.get('diag')
        if not isinstance(diag, dict):
            continue
        for key in ('info_valid', 'last_info_ok', 'last_result_ok'):
            if diag.get(key) == 0:
                return True
    return False


def observation(samples):
    temps = [sample.get('tmp') for sample in samples if sample.get('tmp') is not None]
    fans = [sample.get('fan') for sample in samples if sample.get('fan') is not None]

    def delta(key):
        if samples[0].get(key) is None or samples[-1].get(key) is None:
            return None
        if decreased(samples[0].get(key), samples[-1].get(key)):
            return None
        return samples[-1].get(key) - samples[0].get(key)

    startup = [sample.get('ss') for sample in samples]
    changed = len(set([json.dumps(value) for value in startup])) > 1
    chip = [sample.get('ac') for sample in samples]
    version = [sample.get('av') for sample in samples]
    return {
        'temp_min': min(temps) if temps else None,
        'temp_max': max(temps) if temps else None,
        'fan_min': min(fans) if fans else None,
        'fan_max': max(fans) if fans else None,
        'rejected_delta': delta('rj'),
        'hw_delta': delta('hw'),
        'startup_success_changed': changed or len(set([json.dumps(v) for v in chip])) > 1 or len(set([json.dumps(v) for v in version])) > 1,
        'startup_success': samples[-1].get('ss'),
        'chip_count': samples[-1].get('ac'),
        'asic_version': samples[-1].get('av'),
    }


def constant_value(values):
    if not values:
        return None
    first = values[0]
    for value in values[1:]:
        if value != first:
            return None
    return first


def summarize_controls(samples):
    if not samples:
        return None
    flags = []
    for sample in samples:
        flags.append(sample.get('ses'))
        flags.append(sample.get('rs'))
        flags.append(sample.get('es'))
    if any(flag is None for flag in flags):
        epoch_source = 'missing'
    elif all(flag == 'd' for flag in flags):
        epoch_source = 'driver'
    elif all(flag == 'c' for flag in flags):
        epoch_source = 'collector'
    else:
        epoch_source = 'mixed'
    return {
        'pool': constant_value([sample.get('pool') for sample in samples]),
        'worker': constant_value([sample.get('wk') for sample in samples]),
        'failover': constant_value([sample.get('fo') for sample in samples]),
        'fan': constant_value([sample.get('fan') for sample in samples]),
        'requested': constant_value([sample.get('rc') for sample in samples]),
        'mode': constant_value([sample.get('cm') for sample in samples]),
        'epoch_source': epoch_source,
    }


def contrib_faults(samples, contrib_rows, bid):
    faults = []
    notes = []
    if contrib_rows is None:
        return faults, notes
    matched = {}
    for row in contrib_rows:
        if not isinstance(row, dict) or parse_int(row.get('id')) != int(bid):
            continue
        key = time_key(row.get('t'))
        if key is None:
            continue
        matched[key] = row
    previous = None
    gapped = False
    for sample in samples:
        row = matched.get(time_key(sample.get('t')))
        if row is None:
            gapped = True
            previous = None
            continue
        if previous is not None:
            for key in ('n', 'e'):
                old = previous.get(key)
                new = row.get(key)
                if old is None or new is None:
                    gapped = True
                    continue
                if not isinstance(old, list) or not isinstance(new, list) or len(old) != 64 or len(new) != 64:
                    gapped = True
                    continue
                for index in range(64):
                    if parse_int(new[index]) is None or parse_int(old[index]) is None:
                        gapped = True
                        break
                    if int(new[index]) < int(old[index]):
                        faults.append('counter-wrap')
                        break
        previous = row
    if gapped:
        notes.append('contribution-gap')
    return faults, notes


def measure_board(history, bid, start, end, contrib_rows):
    result = {
        'id': int(bid),
        'valid': False,
        'unverified': None,
        'reasons': [],
        'notes': [],
        'seconds': None,
        'accepted_delta': None,
        'difficulty_delta': None,
        'ghs': None,
        'accepted_per_second': None,
        'fixed_difficulty': None,
        'fixed_difficulty_ok': False,
        'duration_class': None,
        'accepted_ok': False,
        'clock': None,
        'controls': None,
        'host': None,
        'firmware': None,
        'observations': None,
        'sync': None,
    }
    if float(end) < float(start):
        result['reasons'] = ['window-order']
        return result
    selected = board_samples(history, bid, start, end)
    if selected is None:
        result['reasons'] = ['board-missing']
        return result
    faults = []
    if len(selected) < 2:
        faults.append('too-few-samples')
    else:
        if abs(float(selected[0]['t']) - float(start)) > 1e-6 or abs(float(selected[-1]['t']) - float(end)) > 1e-6:
            faults.append('window-uncovered')
        seen = set()
        for sample in selected:
            key = time_key(sample.get('t'))
            if key in seen:
                faults.append('duplicate-sample')
            seen.add(key)
            faults.extend(sample_faults(sample))
        for index in range(1, len(selected)):
            faults.extend(pair_faults(selected[index - 1], selected[index]))
    grid_faults, grid_notes = contrib_faults(selected, contrib_rows, bid)
    faults.extend(grid_faults)
    notes = list(grid_notes)
    if len(selected) >= 2:
        _delta, time_fault = timebase_delta(selected[0], selected[-1])
        seconds = None if time_fault else _delta
        accepted_delta = None
        difficulty_delta = None
        if selected[0].get('a') is not None and selected[-1].get('a') is not None:
            accepted_delta = parse_int(selected[-1]['a']) - parse_int(selected[0]['a'])
        if selected[0].get('da') is not None and selected[-1].get('da') is not None:
            difficulty_delta = float(selected[-1]['da']) - float(selected[0]['da'])
        if accepted_delta is not None and accepted_delta > 0 and difficulty_delta is not None and abs(difficulty_delta) <= 1e-6:
            faults.append('difficulty-zero')
        if accepted_delta == 0 and difficulty_delta is not None and difficulty_delta > 1e-6:
            faults.append('difficulty-without-accepted')
        result['seconds'] = seconds
        result['accepted_delta'] = accepted_delta
        result['difficulty_delta'] = difficulty_delta
        obs = observation(selected)
        result['observations'] = obs
        if obs.get('startup_success_changed'):
            notes.append('startup-changed')
        controls = summarize_controls(selected)
        result['controls'] = controls
        if controls and controls.get('epoch_source') == 'collector':
            notes.append('collector-epoch')
        result['sync'] = sync_of(selected)
        if result['sync'] != 'co-snapshot':
            notes.append('unsynchronized-api-reads')
        if new_hw_errors(selected):
            notes.append('new-hw-errors')
        if health_interrupted(selected):
            notes.append('health-interruption')
        result['clock'] = {
            'mode': selected[-1].get('cm'),
            'requested': selected[-1].get('rc'),
            'generation': selected[-1].get('cg'),
            'verified_generation': selected[-1].get('vg'),
            'command_verified': selected[-1].get('cv'),
            'pending': selected[-1].get('pend'),
            'inflight': selected[-1].get('inf'),
        }
        result['route'] = {
            'pool': selected[-1].get('pool'),
            'worker': selected[-1].get('wk'),
            'pool_index': selected[-1].get('ep'),
            'epoch': selected[-1].get('re'),
        }
        result['firmware'] = {
            'label': selected[-1].get('fl'),
            'version': selected[-1].get('fw'),
            'label_provenance': 'api-field-not-chip-readback' if selected[-1].get('fl') else None,
            'not_chip_readback': True,
            'startup_success': selected[-1].get('ss'),
            'chip_count': selected[-1].get('ac'),
            'asic_version': selected[-1].get('av'),
        }
        host = host_record(history, selected[-1])
        if host:
            shown = {
                'sha256': host.get('sha256'),
                'provenance': selected[-1].get('hp') or host.get('provenance'),
                'path': host.get('path'),
            }
            if host.get('source'):
                shown['source'] = host.get('source')
            result['host'] = shown
        provenances = []
        for sample in selected:
            provenances.append(sample.get('hp'))
        if len(set(provenances)) > 1:
            notes.append('host-provenance-changed')
    faults = order_reasons(faults)
    notes = order_reasons(notes)
    result['reasons'] = faults
    result['notes'] = notes
    result['unverified'] = verification_state(selected, faults) if len(selected) >= 2 else None
    valid = not faults and len(selected) >= 2
    result['valid'] = valid
    if not valid:
        result['seconds'] = None
        result['accepted_delta'] = None
        result['difficulty_delta'] = None
        result['ghs'] = None
        return result
    seconds = result['seconds']
    result['duration_class'] = duration_class(seconds)
    result['ghs'] = measured_ghs(result['difficulty_delta'], seconds)
    if result['accepted_delta'] is not None and seconds:
        result['accepted_per_second'] = float(result['accepted_delta']) / float(seconds)
    result['accepted_ok'] = result['accepted_delta'] is not None and result['accepted_delta'] >= MIN_EQUIV_ACCEPTED
    difficulty, difficulty_reason = fixed_difficulty(selected)
    if difficulty_reason == 'zero-accepted':
        result['fixed_difficulty_ok'] = False
    elif difficulty_reason == 'variable-difficulty':
        result['fixed_difficulty_ok'] = False
        result['notes'] = order_reasons(list(result['notes']) + ['variable-difficulty'])
    else:
        result['fixed_difficulty'] = difficulty
        result['fixed_difficulty_ok'] = True
    return result


def artifacts_for_window(history, start, end):
    rows = []
    for row in (history or {}).get('artifacts') or []:
        if not isinstance(row, dict):
            continue
        if row.get('provenance') != PROVENANCE_ARTIFACT:
            continue
        first = parse_float(row.get('first_t'))
        last = parse_float(row.get('last_t'))
        if first is None or last is None:
            continue
        if last < float(start) or first > float(end):
            continue
        rows.append({
            'path': row.get('path'),
            'sha256': row.get('sha256'),
            'provenance': PROVENANCE_ARTIFACT,
            'not_chip_readback': True,
        })
    return rows


def exact_window(history, start, end, boards=(0, 1, 2), contrib_rows=None):
    board_ids = [int(board) for board in boards]
    measured = []
    for bid in board_ids:
        measured.append(measure_board(history, bid, start, end, contrib_rows))
    valid = bool(measured) and all(board['valid'] for board in measured)
    reasons = []
    notes = []
    unverified_values = []
    for board in measured:
        for reason in board['reasons']:
            if reason not in reasons:
                reasons.append(reason)
        for note in board['notes']:
            if note not in notes:
                notes.append(note)
        unverified_values.append(board['unverified'])
    if any(value is True for value in unverified_values):
        unverified = True
    elif unverified_values and all(value is False for value in unverified_values):
        unverified = False
    else:
        unverified = None
    artifact_rows = artifacts_for_window(history, start, end)
    paths = {}
    for row in artifact_rows:
        paths.setdefault(row.get('path'), set()).add(row.get('sha256'))
    if any(len(shas) > 1 for shas in paths.values()):
        notes.append('artifact-file-changed')
    return {
        'v': 1,
        'kind': 'exact-window',
        'formula': FORMULA,
        'valid': valid,
        'unverified': unverified,
        'reasons': order_reasons(reasons),
        'notes': order_reasons(notes),
        'start': start,
        'end': end,
        'artifacts': artifact_rows,
        'boards': dict((str(board['id']), board) for board in measured),
    }


def leg_summary(report):
    return {
        'valid': report['valid'],
        'unverified': report['unverified'],
        'reasons': report['reasons'],
        'notes': report['notes'],
        'boards': report['boards'],
    }


def same_difficulty(boards):
    values = []
    for board in boards:
        if not board.get('fixed_difficulty_ok'):
            return None
        values.append(board.get('fixed_difficulty'))
    if not values:
        return None
    base = values[0]
    for value in values[1:]:
        if abs(float(value) - float(base)) > 1e-4:
            return None
    return base


def chronology_reasons(named):
    by = dict(named)
    if not all(key in by for key in ('v1a', 'stock', 'v1b')):
        return []
    try:
        a0 = float(by['v1a'].get('start'))
        a1 = float(by['v1a'].get('end'))
        s0 = float(by['stock'].get('start'))
        s1 = float(by['stock'].get('end'))
        b0 = float(by['v1b'].get('start'))
    except (TypeError, ValueError):
        return ['windows-out-of-order']
    if not (a0 < s0 < b0):
        return ['windows-out-of-order']
    if not (a1 < s0 and s1 < b0):
        return ['windows-overlap']
    return []


def control_reasons(reports, board_ids):
    reasons = []
    endpoints = []
    per_board = {}
    for report in reports.values():
        leg_workers = []
        for bid in board_ids:
            controls = (report['boards'].get(str(bid)) or {}).get('controls') or {}
            endpoints.append(controls.get('pool'))
            per_board.setdefault(bid, []).append(controls.get('worker'))
            leg_workers.append(controls.get('worker'))
            if controls.get('failover') != 0:
                reasons.append('failover-not-off')
            if controls.get('fan') != REQUIRED_FAN:
                reasons.append('fan-not-80')
            if controls.get('requested') != REQUIRED_REQUESTED_CLOCK:
                reasons.append('requested-clock-not-400')
            if controls.get('mode') != REQUIRED_CLOCK_MODE:
                reasons.append('clock-mode-not-global')
        if any(worker is None for worker in leg_workers) or len(set(leg_workers)) != len(board_ids):
            reasons.append('workers-not-unique')
    if (not endpoints) or any(item is None for item in endpoints) or len(set(endpoints)) != 1:
        reasons.append('endpoint-mismatch')
    for _bid, values in per_board.items():
        if (not values) or any(value is None for value in values) or len(set(values)) != 1:
            reasons.append('worker-not-retained')
    return reasons


def comparison_controls(reports, board_ids, named):
    endpoints = []
    workers = {}
    fans = []
    clocks = []
    modes = []
    failovers = []
    sources = []
    for report in reports.values():
        for bid in board_ids:
            controls = (report['boards'].get(str(bid)) or {}).get('controls') or {}
            endpoints.append(controls.get('pool'))
            workers.setdefault(str(bid), []).append(controls.get('worker'))
            fans.append(controls.get('fan'))
            clocks.append(controls.get('requested'))
            modes.append(controls.get('mode'))
            failovers.append(controls.get('failover'))
            sources.append(controls.get('epoch_source'))

    def same(values, expected):
        return bool(values) and all(value == expected for value in values)

    endpoint = None
    if endpoints and all(item == endpoints[0] and item is not None for item in endpoints):
        endpoint = endpoints[0]
    worker_out = {}
    seen = []
    unique = True
    for bid in board_ids:
        values = workers.get(str(bid)) or []
        if values and all(value == values[0] and value is not None for value in values):
            worker_out[str(bid)] = values[0]
            seen.append(values[0])
        else:
            worker_out[str(bid)] = None
            unique = False
    if len(seen) != len(board_ids) or len(set(seen)) != len(seen):
        unique = False
    if sources and all(item == 'driver' for item in sources):
        source = 'driver'
    elif sources and all(item == 'collector' for item in sources):
        source = 'collector'
    elif sources:
        source = 'mixed'
    else:
        source = None
    return {
        'endpoint': endpoint,
        'workers': worker_out,
        'workers_unique': unique,
        'fan': REQUIRED_FAN if same(fans, REQUIRED_FAN) else None,
        'requested_clock': REQUIRED_REQUESTED_CLOCK if same(clocks, REQUIRED_REQUESTED_CLOCK) else None,
        'clock_mode': REQUIRED_CLOCK_MODE if same(modes, REQUIRED_CLOCK_MODE) else None,
        'failover': 0 if same(failovers, 0) else None,
        'chronological': chronology_reasons(named) == [] and all(
            key in dict(named) for key in ('v1a', 'stock', 'v1b')),
        'epoch_source': source,
    }


def deployment_gates(evidence):
    required = (
        ('deployment', 'manually-verified', 'deployment-unverified'),
        ('cold_start', 'supervised-manual', 'cold-start-unverified'),
        ('warm_up', 'recorded', 'warm-up-unverified'),
        ('firmware_readback', 'verified', 'firmware-readback-unverified'),
    )
    if not isinstance(evidence, dict):
        return [item[2] for item in required]
    gates = []
    for key, expected, label in required:
        if evidence.get(key) != expected:
            gates.append(label)
    return gates


def compare_legs(legs, boards=(0, 1, 2), evidence=None):
    """legs: list of {role, history, start, end, contrib, name}.

    role is stock or v1. The first v1 leg is v1a and the second is v1b.
    Equivalence is statistical unless evidence and driver epochs are present.
    """
    board_ids = [int(board) for board in boards]
    reasons = []
    stock = [leg for leg in legs if leg.get('role') == 'stock']
    v1 = [leg for leg in legs if leg.get('role') == 'v1']
    if len(stock) < 1:
        reasons.append('stock-leg-missing')
    if len(stock) > 1:
        reasons.append('extra-stock-leg')
    if len(v1) < 2:
        reasons.append('need-two-v1-legs')
    if len(v1) > 2:
        reasons.append('extra-v1-leg')
    named = []
    if stock:
        named.append(('stock', stock[0]))
    if v1:
        named.append(('v1a', v1[0]))
    if len(v1) > 1:
        named.append(('v1b', v1[1]))
    reports = {}
    for name, leg in named:
        report = exact_window(
            leg.get('history'), leg.get('start'), leg.get('end'),
            boards=board_ids, contrib_rows=leg.get('contrib'))
        report['role'] = leg.get('role')
        report['name'] = leg.get('name') or name
        reports[name] = report
        if not report['valid']:
            reasons.append('leg-invalid')
    reasons.extend(chronology_reasons(named))
    status_reasons = list(reasons)
    board_ratios = {}
    if reports and 'leg-invalid' not in status_reasons and 'stock-leg-missing' not in status_reasons:
        status_reasons.extend(control_reasons(reports, board_ids))
        host_rows = []
        labels = {}
        for name, report in reports.items():
            shas = []
            provs = []
            flabels = []
            fwvers = []
            for bid in board_ids:
                board = report['boards'][str(bid)]
                host = board.get('host') or {}
                shas.append(host.get('sha256'))
                provs.append(host.get('provenance'))
                firmware = board.get('firmware') or {}
                flabels.append(firmware.get('label'))
                fwvers.append(firmware.get('version'))
                if board.get('duration_class') == 'over-cap':
                    status_reasons.append('exceeds-12h-cap')
                if board.get('duration_class') == 'screening':
                    status_reasons.append('shorter-than-6h')
                if board.get('valid') and not board.get('accepted_ok'):
                    status_reasons.append('accepted-below-10000')
                if 'variable-difficulty' in (board.get('notes') or []):
                    status_reasons.append('variable-difficulty')
                if 'host-provenance-changed' in (board.get('notes') or []):
                    status_reasons.append('host-provenance-mismatch')
                if board.get('valid') and board.get('accepted_delta') == 0:
                    status_reasons.append('zero-accepted')
                if board.get('unverified') is None and board.get('valid'):
                    status_reasons.append('verification-unknown')
            if any(sha is None for sha in shas) or len(set(shas)) != 1:
                status_reasons.append('host-identity-missing')
            else:
                host_rows.append(shas[0])
            if any(prov not in HOST_PROVENANCE for prov in provs) or any(prov is None for prov in provs):
                status_reasons.append('host-provenance')
            elif len(set(provs)) != 1:
                status_reasons.append('host-provenance-mismatch')
            # Inode /proc/PID/exe and DiagHostSha256 both use running provenance.
            # A configured-path hash, or a malformed driver digest, does not.
            if any(prov != PROVENANCE_HOST_RUNNING for prov in provs) or any(
                    not driver_reported_sha_ok(report['boards'][str(bid)].get('host'))
                    for bid in board_ids):
                status_reasons.append('host-not-running-binary')
            if any(label is None or label == '' for label in flabels) or len(set(flabels)) != 1:
                status_reasons.append('firmware-identity-missing')
                labels[name] = None
            else:
                labels[name] = flabels[0]
            if len(set(fwvers)) != 1:
                status_reasons.append('firmware-mismatch')
        if len(set(host_rows)) > 1:
            status_reasons.append('host-mismatch')
        if labels.get('v1a') and labels.get('v1b') and labels['v1a'] != labels['v1b']:
            status_reasons.append('firmware-mismatch')
        if labels.get('stock') and labels.get('v1a') and labels['stock'] == labels['v1a']:
            status_reasons.append('firmware-label-not-distinct')
        difficulties = []
        for name, report in reports.items():
            difficulty = same_difficulty([report['boards'][str(bid)] for bid in board_ids])
            difficulties.append(difficulty)
            if difficulty is None and 'variable-difficulty' not in status_reasons and 'zero-accepted' not in status_reasons:
                status_reasons.append('variable-difficulty')
        if all(item is not None for item in difficulties):
            base = difficulties[0]
            if any(abs(float(item) - float(base)) > 1e-4 for item in difficulties[1:]):
                status_reasons.append('difficulty-mismatch')
        if 'leg-invalid' not in status_reasons:
            for bid in board_ids:
                ratios = {}
                pairs = (
                    ('v1a_over_stock', 'v1a', 'stock'),
                    ('v1b_over_stock', 'v1b', 'stock'),
                    ('v1a_over_v1b', 'v1a', 'v1b'),
                )
                for key, left, right in pairs:
                    if left not in reports or right not in reports:
                        continue
                    lhs = reports[left]['boards'][str(bid)]
                    rhs = reports[right]['boards'][str(bid)]
                    ci = None
                    if lhs.get('fixed_difficulty_ok') and rhs.get('fixed_difficulty_ok'):
                        ci = poisson_rate_ratio_ci(
                            lhs.get('accepted_delta'), lhs.get('seconds'),
                            rhs.get('accepted_delta'), rhs.get('seconds'))
                    entry = {'ci': ci, 'class': None, 'ghs_ratio': None}
                    if ci is not None:
                        entry['class'] = band_class(ci['lo'], ci['hi'])
                        if lhs.get('ghs') and rhs.get('ghs'):
                            entry['ghs_ratio'] = float(lhs['ghs']) / float(rhs['ghs'])
                            if abs(entry['ghs_ratio'] - ci['ratio']) > 1e-6 * max(1.0, abs(ci['ratio'])):
                                status_reasons.append('ghs-count-ratio-mismatch')
                        if entry['class'] == 'outside':
                            status_reasons.append('ratio-outside')
                            if key == 'v1a_over_v1b':
                                status_reasons.append('v1-legs-inconsistent')
                        elif entry['class'] == 'overlap':
                            status_reasons.append('ratio-overlap')
                            if key == 'v1a_over_v1b':
                                status_reasons.append('v1-legs-inconsistent')
                    elif lhs.get('fixed_difficulty_ok') and rhs.get('fixed_difficulty_ok'):
                        status_reasons.append('ratio-overlap')
                    ratios[key] = entry
                board_ratios[str(bid)] = ratios
    status_reasons = order_reasons(status_reasons)
    caveats = []
    epoch_sources = []
    health = {'new_hw_errors': [], 'interruptions': []}
    unsynchronized = False
    for report in reports.values():
        for bid in board_ids:
            board = report['boards'].get(str(bid)) or {}
            controls = board.get('controls') or {}
            notes = board.get('notes') or []
            epoch_sources.append(controls.get('epoch_source'))
            if board.get('sync') != 'co-snapshot' or 'unsynchronized-api-reads' in notes:
                unsynchronized = True
            if 'new-hw-errors' in notes and str(bid) not in health['new_hw_errors']:
                health['new_hw_errors'].append(str(bid))
            if 'health-interruption' in notes and str(bid) not in health['interruptions']:
                health['interruptions'].append(str(bid))
    if unsynchronized:
        caveats.append('unsynchronized-counters')
    if health['new_hw_errors']:
        caveats.append('new-hw-errors')
    if health['interruptions']:
        caveats.append('health-interruption')
    if any(source != 'driver' for source in epoch_sources):
        caveats.append('collector-epoch')
    gates = deployment_gates(evidence)
    # One non-co-snapshot sample, new HW, or a health interruption blocks a
    # definitive claim even when the rate interval and manual evidence pass.
    definitive_blocked = bool(gates) or any(
        source != 'driver' for source in epoch_sources) or unsynchronized or bool(
            health['new_hw_errors'] or health['interruptions'])
    fatal = set(['leg-invalid', 'window-order', 'host-mismatch', 'firmware-mismatch',
                 'stock-leg-missing', 'extra-stock-leg', 'extra-v1-leg'])
    insufficient = set([
        'accepted-below-10000', 'variable-difficulty', 'zero-accepted',
        'difficulty-mismatch', 'verification-unknown', 'host-identity-missing',
        'host-provenance', 'host-provenance-mismatch', 'host-not-running-binary',
        'firmware-identity-missing', 'firmware-label-not-distinct', 'need-two-v1-legs',
        'ghs-count-ratio-mismatch', 'endpoint-mismatch', 'worker-not-retained',
        'workers-not-unique', 'failover-not-off', 'fan-not-80',
        'requested-clock-not-400', 'clock-mode-not-global', 'windows-overlap',
        'windows-out-of-order',
    ])
    # A CI entirely outside the band is decisive only after the sample-size,
    # identity, and controlled-condition gates. Short and over-cap legs stay unresolved.
    if any(reason in fatal for reason in status_reasons):
        status = 'invalid'
    elif 'exceeds-12h-cap' in status_reasons:
        status = 'inconclusive'
    elif 'shorter-than-6h' in status_reasons:
        status = 'screening'
    elif any(reason in insufficient for reason in status_reasons):
        status = 'inconclusive'
    elif 'ratio-outside' in status_reasons:
        status = 'not-equivalent'
    elif status_reasons:
        status = 'inconclusive'
    elif definitive_blocked:
        status = 'statistical-equivalent'
    else:
        status = 'equivalent'
    if status == 'invalid':
        conclusion = 'none'
    elif status in ('equivalent', 'not-equivalent') and not definitive_blocked:
        conclusion = 'definitive'
    else:
        conclusion = 'statistical-only'
    unverified = any(report.get('unverified') is True for report in reports.values())
    return {
        'v': 1,
        'kind': 'comparison',
        'status': status,
        'conclusion': conclusion,
        'reasons': status_reasons,
        'caveats': caveats,
        'health': health,
        'deployment_gates': gates,
        'unverified': unverified,
        'firmware_note': 'api-label-is-not-chip-readback',
        'formula': FORMULA,
        'band': [EQUIV_LO, EQUIV_HI],
        'min_seconds': MIN_EQUIV_SECONDS,
        'max_seconds': MAX_EQUIV_SECONDS,
        'min_accepted': MIN_EQUIV_ACCEPTED,
        'controls': comparison_controls(reports, board_ids, named),
        'ci': {
            'level': 0.95,
            'method': 'wald-log-poisson-rate-ratio',
            'z': Z95,
        },
        'legs': reports,
        'boards': board_ratios,
    }


def load_history_file(path):
    with open(path, 'rb') as handle:
        data = json.loads(handle.read().decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('history is not an object')
    return data


def load_contrib(directory, start, end):
    if not directory or not os.path.isdir(directory):
        return []
    first = int(float(start) // 3600) - 1
    last = int(float(end) // 3600) + 1
    rows = []
    for name in sorted(os.listdir(directory)):
        if not name.startswith('c-') or not name.endswith('.jsonl'):
            continue
        try:
            segment = int(name[2:-6])
        except ValueError:
            continue
        if segment < first or segment > last:
            continue
        path = os.path.join(directory, name)
        with open(path, 'rb') as handle:
            for line in handle:
                text = line.decode('utf-8', 'replace').strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def parse_boards(text):
    boards = []
    for part in (text or '').split(','):
        if not part.strip():
            continue
        number = parse_int(part.strip())
        if number is None:
            raise ValueError('bad board id')
        boards.append(number)
    if not boards:
        raise ValueError('no boards')
    return boards


def emit(report):
    sys.stdout.write(json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False))
    sys.stdout.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='bkb-diagnostics-report')
    sub = parser.add_subparsers(dest='cmd')
    window = sub.add_parser('window')
    window.add_argument('--history', required=True)
    window.add_argument('--start', required=True, type=float)
    window.add_argument('--end', required=True, type=float)
    window.add_argument('--boards', default='0,1,2')
    window.add_argument('--contrib', default='')
    compare = sub.add_parser('compare')
    compare.add_argument('--boards', default='0,1,2')
    compare.add_argument('--stock-history', required=True)
    compare.add_argument('--stock-start', required=True, type=float)
    compare.add_argument('--stock-end', required=True, type=float)
    compare.add_argument('--stock-contrib', default='')
    compare.add_argument('--v1-history', required=True)
    compare.add_argument('--v1-start', required=True, type=float)
    compare.add_argument('--v1-end', required=True, type=float)
    compare.add_argument('--v1-contrib', default='')
    compare.add_argument('--v1b-history', required=True)
    compare.add_argument('--v1b-start', required=True, type=float)
    compare.add_argument('--v1b-end', required=True, type=float)
    compare.add_argument('--v1b-contrib', default='')
    compare.add_argument('--evidence', default='')
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return 2
    try:
        boards = parse_boards(args.boards)
        if args.cmd == 'window':
            history = load_history_file(args.history)
            contrib = load_contrib(args.contrib, args.start, args.end) if args.contrib else None
            emit(exact_window(history, args.start, args.end, boards=boards, contrib_rows=contrib))
            return 0
        legs = []
        for role, prefix in (('stock', 'stock'), ('v1', 'v1'), ('v1', 'v1b')):
            history_path = getattr(args, prefix + '_history')
            start = getattr(args, prefix + '_start')
            end = getattr(args, prefix + '_end')
            contrib_path = getattr(args, prefix + '_contrib')
            legs.append({
                'role': role,
                'name': prefix,
                'history': load_history_file(history_path),
                'start': start,
                'end': end,
                'contrib': load_contrib(contrib_path, start, end) if contrib_path else None,
            })
        evidence = load_history_file(args.evidence) if args.evidence else None
        emit(compare_legs(legs, boards=boards, evidence=evidence))
        return 0
    except (IOError, OSError, ValueError) as exc:
        sys.stderr.write('bkb-diagnostics-report: %s\n' % exc)
        return 2


if __name__ == '__main__':
    sys.exit(main())
