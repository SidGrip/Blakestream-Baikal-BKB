#!/usr/bin/env python
from __future__ import print_function, division

import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin')
REPORT_PATH = os.path.join(BIN, 'bkb-diagnostics-report.py')

try:
    binary_type = bytes
except NameError:
    binary_type = str


def load(name, path):
    try:
        import importlib.util
    except ImportError:
        import imp
        return imp.load_source(name, path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rep = load('bkb_diagnostics_report_only', REPORT_PATH)

SHA = 'ab' * 32
OTHER_SHA = 'cd' * 32
RUNNING = rep.PROVENANCE_HOST_RUNNING
CONFIGURED = rep.PROVENANCE_HOST_CONFIGURED
ARTIFACT = rep.PROVENANCE_ARTIFACT
EVIDENCE = {
    'deployment': 'manually-verified',
    'cold_start': 'supervised-manual',
    'warm_up': 'recorded',
    'firmware_readback': 'verified',
}


def fail(message):
    sys.stderr.write('FAIL: %s\n' % message)
    return 1


def base(stamp, index, bid=0, per_step=20, label='v1-bridge'):
    return {
        't': stamp,
        'tm': stamp,
        'pe': '9:1790640000',
        'pt': '9:7654321',
        'ps': 'api',
        'se': 3,
        'ses': 'd',
        're': 4,
        'rs': 'd',
        'ee': 1,
        'es': 'd',
        'de': 50000 + index * 60,
        'a': 1000 + index * per_step,
        'da': 32000.0 + index * per_step * 32.0,
        'rj': 0,
        'hw': 0,
        'tmp': 40.0,
        'fan': 80,
        'al': 1,
        'en': 1,
        'cm': 'global',
        'rc': 400,
        'cg': 7,
        'vg': 7,
        'cv': 1,
        'pend': 0,
        'inf': 0,
        'sc': 2,
        'ep': 0,
        'fo': 0,
        'pool': 'eu3.example:3334',
        'wk': 'w%d' % bid,
        'ss': 64,
        'ac': 64,
        'av': 3,
        'fw': 1,
        'hwv': 1,
        'fl': label,
        'sync': 'unsynchronized',
        'hid': 1,
        'hp': RUNNING,
        'diag': {
            'invalid_id': index,
            'attempted_clock_code': 9,
            'reply_mismatch': 0,
        },
    }


def history_from(samples_by_board, sha=SHA, provenance=RUNNING, artifacts=None):
    boards = []
    for bid in sorted(samples_by_board):
        boards.append({'id': bid, 'samples': samples_by_board[bid]})
    return {
        'v': 1,
        'host': [{
            'id': 1,
            'sha256': sha,
            'provenance': provenance,
            'path': '/proc/9/exe',
            'first_t': 0,
            'last_t': 1,
        }],
        'artifacts': artifacts or [],
        'boards': boards,
    }


def series(start, seconds, per_step, label, board_ids=(0,), **over):
    steps = int(seconds // 60)
    grouped = {}
    for bid in board_ids:
        rows = []
        for index in range(steps + 1):
            row = base(start + index * 60, index, bid, per_step, label)
            row.update(over)
            rows.append(row)
        grouped[bid] = rows
    return history_from(grouped)


def three(span, rates, board_ids=(0,), **over):
    gap = 60
    specs = (
        ('v1', 0, rates[0], 'v1-bridge'),
        ('stock', span + gap, rates[1], 'stock-bridge'),
        ('v1', span + gap + span + gap, rates[2], 'v1-bridge'),
    )
    legs = []
    for role, start, per_step, label in specs:
        legs.append({
            'role': role,
            'history': series(start, span, per_step, label, board_ids, **over),
            'start': start,
            'end': start + span,
        })
    return legs


def compare(span, rates, board_ids=(0,), evidence=None, **over):
    return rep.compare_legs(three(span, rates, board_ids, **over), boards=board_ids, evidence=evidence)


def window_of(samples, start, end):
    return rep.exact_window(history_from({0: samples}), start, end, boards=(0,))


def test_formula_bounds():
    expected = (9600.0 * 4294967296.0) / 900.0 / 1e9
    if abs(rep.measured_ghs(9600, 900) - expected) > 1e-12:
        return fail('GH/s formula drifted')
    if rep.duration_class(21600) != 'standard' or rep.duration_class(21599.9) != 'screening':
        return fail('6h duration boundary drifted')
    if rep.duration_class(43200) != 'standard' or rep.duration_class(43200.1) != 'over-cap':
        return fail('12h duration boundary drifted')
    if rep.band_class(0.95, 1.05) != 'inside':
        return fail('closed band was not inside')
    if rep.band_class(0.949, 1.0) != 'overlap' or rep.band_class(0.90, 0.94) != 'outside':
        return fail('band class drifted')
    ci = rep.poisson_rate_ratio_ci(10000, 21600, 10000, 21600)
    se = math.sqrt(0.0002)
    lo = math.exp(-rep.Z95 * se)
    hi = math.exp(rep.Z95 * se)
    if ci is None or abs(ci['lo'] - lo) > 1e-12 or abs(ci['hi'] - hi) > 1e-12:
        return fail('equal-rate CI drifted')
    if rep.band_class(ci['lo'], ci['hi']) != 'inside':
        return fail('equal 10000-share CI was not inside the band')
    if 'flashed' in open(REPORT_PATH, 'rb').read().decode('utf-8'):
        return fail('report source claims a flashed hash')
    return 0


def test_window_faults():
    clean = [base(0, 0), base(60, 1)]
    report = window_of(clean, 0, 60)
    expected = (640.0 * 4294967296.0) / 60.0 / 1e9
    if not report['valid'] or abs(report['boards']['0']['ghs'] - expected) > 1e-9:
        return fail('60s window GH/s drifted: %s' % report['reasons'])
    if report['boards']['0']['firmware']['label_provenance'] != 'api-field-not-chip-readback':
        return fail('firmware label was treated as chip read-back')
    if 'flashed' in json.dumps(report):
        return fail('window JSON contains flashed')

    gap = [base(0, 0), base(120, 1)]
    gap[1]['de'] = gap[0]['de'] + 120
    gap[1]['tm'] = 120
    gap[1]['t'] = 120
    allowed = window_of(gap, 0, 120)
    if not allowed['valid'] or allowed['boards']['0']['seconds'] != 120:
        return fail('exactly 120s was rejected: %s' % allowed['reasons'])
    late = [base(0, 0), base(121, 1)]
    late[1]['t'] = 121
    late[1]['tm'] = 121
    late[1]['de'] = late[0]['de'] + 121
    rejected = window_of(late, 0, 121)
    if rejected['valid'] or rejected['boards']['0']['ghs'] is not None or 'long-gap' not in rejected['reasons']:
        return fail('121s gap was measured')

    hole = [base(0, 0), base(60, 1), base(200, 2)]
    hole[2]['t'] = 200
    hole[2]['tm'] = 200
    hole[2]['de'] = hole[0]['de'] + 200
    naive = (hole[2]['da'] - hole[0]['da']) * 4294967296.0 / 200.0 / 1e9
    holed = window_of(hole, 0, 200)
    if holed['valid'] or holed['boards']['0']['ghs'] is not None:
        return fail('a middle hole was interpolated')
    if holed['boards']['0']['ghs'] == naive:
        return fail('middle hole used the endpoint formula')

    jump = [base(0, 0), base(70, 1)]
    jump[1]['t'] = 70
    jump[1]['tm'] = 60
    jump[1]['de'] = jump[0]['de'] + 60
    jumped = window_of(jump, 0, 70)
    if jumped['valid'] or 'time-jump' not in jumped['reasons'] or jumped['boards']['0']['ghs'] is not None:
        return fail('wall/mono jump was accepted')

    reset = [base(0, 0), base(60, 1)]
    reset[1]['a'] = reset[0]['a'] - 1
    if 'counter-reset' not in window_of(reset, 0, 60)['reasons']:
        return fail('accepted decrease was not a reset')
    wrapped = [base(0, 0), base(60, 1)]
    wrapped[0]['diag'] = dict(wrapped[0]['diag'])
    wrapped[1]['diag'] = dict(wrapped[1]['diag'])
    wrapped[0]['diag']['invalid_id'] = 5
    wrapped[1]['diag']['invalid_id'] = 4
    if 'counter-wrap' not in window_of(wrapped, 0, 60)['reasons']:
        return fail('diag counter decrease was not a wrap')

    zero = [base(0, 0), base(60, 1)]
    zero[1]['da'] = zero[0]['da']
    zero_report = window_of(zero, 0, 60)
    if zero_report['valid'] or 'difficulty-zero' not in zero_report['reasons']:
        return fail('accepts with zero difficulty were measured')
    bare = [base(0, 0), base(60, 1)]
    bare[1]['a'] = bare[0]['a']
    if 'difficulty-without-accepted' not in window_of(bare, 0, 60)['reasons']:
        return fail('difficulty without accepts was accepted')

    disabled = [base(0, 0), base(60, 1)]
    disabled[1]['en'] = 0
    disabled_report = window_of(disabled, 0, 60)
    if disabled_report['valid'] or 'disabled' not in disabled_report['reasons']:
        return fail('disabled sample stayed valid')
    if 'health-interruption' not in disabled_report['notes']:
        return fail('disabled sample was dropped from the health note')
    dead = [base(0, 0), base(60, 1)]
    dead[1]['al'] = 0
    if 'not-alive' not in window_of(dead, 0, 60)['reasons']:
        return fail('not-alive sample stayed valid')
    restart = [base(0, 0), base(60, 1)]
    restart[1]['pe'] = '9:1790640060'
    if 'restart' not in window_of(restart, 0, 60)['reasons']:
        return fail('process epoch change was not a restart')

    def changed(key, value, reason):
        rows = [base(0, 0), base(60, 1)]
        rows[1][key] = value
        found = window_of(rows, 0, 60)
        if found['valid'] or reason not in found['reasons']:
            return fail('%s did not invalidate the window' % reason)
        return 0

    for key, value, reason in (
        ('cm', 'individual', 'clock-mode'),
        ('rc', 401, 'clock-requested'),
        ('cg', 8, 'clock-generation'),
        ('pend', 1, 'clock-pending'),
        ('inf', 1, 'clock-inflight'),
        ('cv', 0, 'clock-verification-changed'),
        ('sc', 3, 'clock-sent-code'),
        ('pool', 'other.example:3334', 'route-change'),
        ('wk', 'other', 'worker-change'),
        ('re', 5, 'route-epoch'),
        ('ee', 2, 'enablement-epoch'),
    ):
        result = changed(key, value, reason)
        if result:
            return result
    route = [base(0, 0), base(60, 1)]
    route[1]['re'] = 5
    route_report = window_of(route, 0, 60)
    if 'route-change' in route_report['reasons']:
        return fail('stable endpoint was called a route change')
    enable = [base(0, 0), base(60, 1)]
    enable[1]['ee'] = 2
    enable_report = window_of(enable, 0, 60)
    if 'disabled' in enable_report['reasons']:
        return fail('enable epoch change was called disabled')
    attempt = [base(0, 0), base(60, 1)]
    attempt[1]['diag'] = dict(attempt[1]['diag'])
    attempt[1]['diag']['attempted_clock_code'] = 10
    if 'clock-attempt' not in window_of(attempt, 0, 60)['reasons']:
        return fail('attempted clock code change was ignored')

    startup = [base(0, 0), base(60, 1)]
    startup[1]['ss'] = 60
    startup_report = window_of(startup, 0, 60)
    if not startup_report['valid'] or 'startup-changed' not in startup_report['notes']:
        return fail('startup change was fatal or hidden')
    if 'startup-changed' in startup_report['reasons']:
        return fail('startup change invalidated accepted work')

    pending = [base(0, 0, per_step=20), base(60, 1, per_step=20)]
    pending[1]['pend'] = 1
    pending_report = window_of(pending, 0, 60)
    if pending_report['valid'] or pending_report['boards']['0']['ghs'] is not None or pending_report['unverified'] is not None:
        return fail('pending clock still produced a measurement')

    unverified_rows = [base(0, 0), base(60, 1)]
    for row in unverified_rows:
        row['cv'] = 0
        row['vg'] = 0
        row['cg'] = 1
    unverified = window_of(unverified_rows, 0, 60)
    if not unverified['valid'] or unverified['unverified'] is not True:
        return fail('settled unverified command rejected accepted work')
    if abs(unverified['boards']['0']['ghs'] - expected) > 1e-9:
        return fail('unverified window changed the GH/s')

    fan = [base(0, 0), base(60, 1)]
    fan[1]['fan'] = 81
    fan[1]['tmp'] = 55
    if not window_of(fan, 0, 60)['valid']:
        return fail('fan or temperature change invalidated the window')
    estimate = [base(0, 0), base(60, 1)]
    for row in estimate:
        row['MHS av'] = 1e9
    estimated = window_of(estimate, 0, 60)
    if not estimated['valid'] or abs(estimated['boards']['0']['ghs'] - expected) > 1e-9:
        return fail('MHS estimate replaced measured GH/s')

    secret = 'SuperSecret9'
    hidden = [base(0, 0), base(60, 1)]
    for row in hidden:
        row['pool'] = 'stratum+tcp://user:%s@eu3.example:3334/x?y=1#z' % secret
    hidden_report = window_of(hidden, 0, 60)
    if not hidden_report['valid'] or hidden_report['boards']['0']['route']['pool'] != 'eu3.example:3334':
        return fail('report did not re-sanitize the pool URL')
    if secret in json.dumps(hidden_report):
        return fail('password survived prepare_sample')

    artifacts = [
        {'path': '/opt/scripta/firmware/custom.bin', 'sha256': SHA, 'provenance': ARTIFACT, 'first_t': 0, 'last_t': 60},
        {'path': '/opt/scripta/firmware/custom.bin', 'sha256': OTHER_SHA, 'provenance': ARTIFACT, 'first_t': 0, 'last_t': 60},
    ]
    changed_file = rep.exact_window(history_from({0: [base(0, 0), base(60, 1)]}, artifacts=artifacts), 0, 60, boards=(0,))
    if not changed_file['valid'] or 'artifact-file-changed' not in changed_file['notes']:
        return fail('artifact hash change was not a note')
    if 'artifact-file-changed' in changed_file['reasons']:
        return fail('artifact hash change invalidated the window')
    return 0


def test_comparisons():
    locked = {'sync': 'co-snapshot'}
    good = compare(21600, (50, 50, 50), evidence=EVIDENCE, **locked)
    if good['status'] != 'equivalent' or good['conclusion'] != 'definitive' or good['deployment_gates']:
        return fail('fully locked 6h comparison was not definitive: %s %s %s' % (
            good['status'], good['conclusion'], good['reasons']))
    if 'unsynchronized-counters' in good['caveats'] or 'collector-epoch' in good['caveats']:
        return fail('clean locked comparison carried a blocking caveat: %s' % good['caveats'])
    ratio = good['boards']['0']['v1a_over_stock']
    if ratio['class'] != 'inside' or abs(ratio['ghs_ratio'] - ratio['ci']['ratio']) > 1e-9:
        return fail('equal-rate band was not inside')

    full = compare(21600, (50, 50, 50), board_ids=(0, 1, 2), evidence=EVIDENCE, **locked)
    controls = full['controls']
    if full['status'] != 'equivalent' or full['conclusion'] != 'definitive':
        return fail('three-board controlled comparison was not definitive: %s %s' % (full['status'], full['reasons']))
    if not controls['workers_unique'] or controls['endpoint'] != 'eu3.example:3334':
        return fail('three-board controls were not recorded')
    if controls['fan'] != 80 or controls['requested_clock'] != 400 or controls['clock_mode'] != 'global':
        return fail('clock or fan controls were not recorded')
    if not controls['chronological'] or controls['epoch_source'] != 'driver' or controls['failover'] != 0:
        return fail('chronology or epoch source was not driver-controlled')

    middle = three(21600, (50, 50, 50), evidence=None, **locked)
    middle[0]['history']['boards'][0]['samples'][180]['sync'] = 'stats-row'
    middle_result = rep.compare_legs(middle, boards=(0,), evidence=EVIDENCE)
    if middle_result['conclusion'] == 'definitive' or middle_result['status'] == 'equivalent':
        return fail('one unsynchronized middle sample stayed definitive')
    if 'unsynchronized-counters' not in middle_result['caveats']:
        return fail('middle sample did not flag unsynchronized counters')
    if not middle_result['legs']['v1a']['valid']:
        return fail('unsynchronized sample invalidated a clean rate window')

    plain = compare(21600, (50, 50, 50))
    if plain['status'] != 'statistical-equivalent' or plain['conclusion'] != 'statistical-only':
        return fail('rates without evidence were called definitive: %s' % plain['status'])
    if 'unsynchronized-counters' not in plain['caveats'] or not plain['deployment_gates']:
        return fail('statistical result hid the unsynchronized or deployment gate')

    hw = three(21600, (50, 50, 50), **locked)
    hw[1]['history']['boards'][0]['samples'][-1]['hw'] = 3
    hw_result = rep.compare_legs(hw, boards=(0,), evidence=EVIDENCE)
    if hw_result['conclusion'] == 'definitive' or 'new-hw-errors' not in hw_result['caveats']:
        return fail('new hardware errors stayed a definitive conclusion')
    if '0' not in hw_result['health']['new_hw_errors']:
        return fail('new hardware errors were not preserved on the comparison')

    sick = three(21600, (50, 50, 50), **locked)
    sample = sick[0]['history']['boards'][0]['samples'][180]
    sample['diag'] = dict(sample['diag'])
    sample['diag']['last_result_ok'] = 0
    sick_result = rep.compare_legs(sick, boards=(0,), evidence=EVIDENCE)
    if sick_result['conclusion'] == 'definitive' or 'health-interruption' not in sick_result['caveats']:
        return fail('health interruption stayed definitive')

    screened = compare(3600, (10, 100, 10), **locked)
    if screened['status'] != 'screening' or 'ratio-outside' not in screened['reasons']:
        return fail('short outside CI was not screening: %s' % screened['status'])
    over = compare(43260, (10, 50, 10), **locked)
    if over['status'] != 'inconclusive' or 'exceeds-12h-cap' not in over['reasons'] or over['status'] == 'not-equivalent':
        return fail('over-cap leg became a rate conclusion: %s' % over['status'])
    low = compare(21600, (27, 27, 27), **locked)
    low_ci = low['boards']['0']['v1a_over_stock']['class']
    if low['status'] != 'inconclusive' or 'accepted-below-10000' not in low['reasons'] or low_ci != 'inside':
        return fail('inside CI below 10000 accepts was not inconclusive')
    overlap = compare(21600, (48, 50, 48), **locked)
    if overlap['status'] != 'inconclusive' or overlap['boards']['0']['v1a_over_stock']['class'] != 'overlap':
        return fail('overlap band was decisive: %s' % overlap['status'])
    outside = compare(21600, (45, 50, 45), evidence=EVIDENCE, **locked)
    outside_ci = outside['boards']['0']['v1a_over_stock']['ci']
    if outside['status'] != 'not-equivalent' or outside['conclusion'] != 'definitive':
        return fail('outside band with evidence was not a definitive mismatch: %s %s' % (
            outside['status'], outside['conclusion']))
    if outside_ci is None or not (outside_ci['hi'] < 0.95 and abs(outside_ci['ratio'] - 0.9) < 1e-9):
        return fail('0.9 rate ratio CI drifted')
    outside_stat = compare(21600, (45, 50, 45), **locked)
    if outside_stat['status'] != 'not-equivalent' or outside_stat['conclusion'] != 'statistical-only':
        return fail('outside band without evidence was not statistical-only')

    weak = compare(21600, (50, 50, 50), evidence=EVIDENCE, ses='c', rs='c', es='c', **locked)
    if weak['status'] == 'equivalent' or weak['conclusion'] == 'definitive' or 'collector-epoch' not in weak['caveats']:
        return fail('collector epochs became definitive')
    mixed = three(21600, (50, 50, 50), board_ids=(0, 1), **locked)
    for leg in mixed:
        for sample in leg['history']['boards'][1]['samples']:
            sample['ses'] = 'c'
            sample['rs'] = 'c'
            sample['es'] = 'c'
    mixed_result = rep.compare_legs(mixed, boards=(0, 1), evidence=EVIDENCE)
    if mixed_result['conclusion'] == 'definitive' or mixed_result['controls']['epoch_source'] == 'driver':
        return fail('mixed epoch sources became definitive')

    configured = three(21600, (50, 50, 50), **locked)
    for leg in configured:
        leg['history']['host'][0]['provenance'] = CONFIGURED
        for board in leg['history']['boards']:
            for sample in board['samples']:
                sample['hp'] = CONFIGURED
    configured_result = rep.compare_legs(configured, boards=(0,), evidence=EVIDENCE)
    if configured_result['status'] == 'statistical-equivalent' or 'host-not-running-binary' not in configured_result['reasons']:
        return fail('configured-path hash established equivalence')

    mismatch = three(21600, (50, 50, 50), **locked)
    mismatch[1]['history']['host'][0]['sha256'] = OTHER_SHA
    mismatch_result = rep.compare_legs(mismatch, boards=(0,), evidence=EVIDENCE)
    if mismatch_result['status'] != 'invalid' or mismatch_result['conclusion'] != 'none' or 'host-mismatch' not in mismatch_result['reasons']:
        return fail('host sha mismatch was not invalid')

    labels = three(21600, (50, 50, 50), **locked)
    for sample in labels[2]['history']['boards'][0]['samples']:
        sample['fl'] = 'v1-other'
    if rep.compare_legs(labels, boards=(0,), evidence=EVIDENCE)['status'] != 'invalid':
        return fail('v1 label mismatch was not invalid')
    same = three(21600, (50, 50, 50), fl='bridge', **locked)
    same_result = rep.compare_legs(same, boards=(0,), evidence=EVIDENCE)
    if same_result['status'] != 'inconclusive' or 'firmware-label-not-distinct' not in same_result['reasons']:
        return fail('identical firmware labels were distinct')
    blank = three(21600, (50, 50, 50), fl=None, **locked)
    blank_result = rep.compare_legs(blank, boards=(0,), evidence=EVIDENCE)
    if blank_result['status'] != 'inconclusive' or 'firmware-identity-missing' not in blank_result['reasons']:
        return fail('missing firmware label was accepted')
    if blank_result['legs']['v1a']['boards']['0']['firmware']['label_provenance'] is not None:
        return fail('missing label invented provenance')

    for reason, over in (
        ('fan-not-80', {'fan': 79, 'sync': 'co-snapshot'}),
        ('requested-clock-not-400', {'rc': 399, 'sync': 'co-snapshot'}),
        ('clock-mode-not-global', {'cm': 'individual', 'sync': 'co-snapshot'}),
        ('failover-not-off', {'fo': 1, 'sync': 'co-snapshot'}),
        ('workers-not-unique', {'wk': 'same', 'sync': 'co-snapshot'}),
    ):
        board_ids = (0, 1) if reason == 'workers-not-unique' else (0,)
        result = compare(21600, (10, 50, 10), board_ids=board_ids, evidence=EVIDENCE, **over)
        if result['status'] != 'inconclusive' or reason not in result['reasons']:
            return fail('%s became %s' % (reason, result['status']))
    endpoint = three(21600, (10, 50, 10), **locked)
    for sample in endpoint[1]['history']['boards'][0]['samples']:
        sample['pool'] = 'other.example:3334'
    endpoint_result = rep.compare_legs(endpoint, boards=(0,), evidence=EVIDENCE)
    if endpoint_result['status'] != 'inconclusive' or 'endpoint-mismatch' not in endpoint_result['reasons']:
        return fail('endpoint mismatch became %s' % endpoint_result['status'])
    retained = three(21600, (10, 50, 10), **locked)
    for sample in retained[1]['history']['boards'][0]['samples']:
        sample['wk'] = 'other-worker'
    retained_result = rep.compare_legs(retained, boards=(0,), evidence=EVIDENCE)
    if retained_result['status'] != 'inconclusive' or 'worker-not-retained' not in retained_result['reasons']:
        return fail('worker change across legs was a rate result')

    touch = [
        {'role': 'v1', 'history': series(0, 21600, 10, 'v1-bridge', **locked), 'start': 0, 'end': 21600},
        {'role': 'stock', 'history': series(21600, 21600, 50, 'stock-bridge', **locked), 'start': 21600, 'end': 43200},
        {'role': 'v1', 'history': series(43260, 21600, 10, 'v1-bridge', **locked), 'start': 43260, 'end': 64860},
    ]
    touch_result = rep.compare_legs(touch, boards=(0,), evidence=EVIDENCE)
    if touch_result['status'] != 'inconclusive' or 'windows-overlap' not in touch_result['reasons']:
        return fail('touching windows became %s' % touch_result['status'])
    ordered = [
        {'role': 'v1', 'history': series(21660, 21600, 10, 'v1-bridge', **locked), 'start': 21660, 'end': 43260},
        {'role': 'stock', 'history': series(0, 21600, 50, 'stock-bridge', **locked), 'start': 0, 'end': 21600},
        {'role': 'v1', 'history': series(43320, 21600, 10, 'v1-bridge', **locked), 'start': 43320, 'end': 64920},
    ]
    ordered_result = rep.compare_legs(ordered, boards=(0,), evidence=EVIDENCE)
    if ordered_result['status'] != 'inconclusive' or 'windows-out-of-order' not in ordered_result['reasons']:
        return fail('out-of-order windows became %s' % ordered_result['status'])

    varied = three(21600, (50, 50, 50), **locked)
    for leg in varied:
        for board in leg['history']['boards']:
            for index, sample in enumerate(board['samples']):
                if index > 180:
                    sample['da'] = sample['da'] + (index - 180) * 32.0
    varied_result = rep.compare_legs(varied, boards=(0,), evidence=EVIDENCE)
    if varied_result['status'] != 'inconclusive' or 'variable-difficulty' not in varied_result['reasons']:
        return fail('variable difficulty was decisive')
    flat = compare(21600, (0, 0, 0), **locked)
    if flat['status'] != 'inconclusive' or 'zero-accepted' not in flat['reasons']:
        return fail('zero accepted was decisive: %s' % flat['reasons'])
    if flat['legs']['stock']['boards']['0']['ghs'] != 0.0:
        return fail('flat counters did not measure 0 GH/s')

    unverified = compare(21600, (50, 50, 50), cv=0, vg=0, cg=1)
    if unverified['status'] != 'statistical-equivalent' or unverified['unverified'] is not True:
        return fail('settled unverified legs could not stay statistical')
    return 0


def write_json(path, obj):
    text = json.dumps(obj)
    if not isinstance(text, binary_type):
        text = text.encode('utf-8')
    with open(path, 'wb') as handle:
        handle.write(text)


class Capture(object):
    def __init__(self):
        self.parts = []

    def write(self, data):
        if not isinstance(data, binary_type) and not isinstance(data, str):
            data = str(data)
        if not isinstance(data, str):
            data = data.decode('utf-8')
        self.parts.append(data)

    def flush(self):
        return None


def test_cli():
    directory = tempfile.mkdtemp(prefix='bkb-report-')
    try:
        short = {'sync': 'co-snapshot'}
        paths = {}
        for name, start, label in (
            ('v1', 0, 'v1-bridge'),
            ('stock', 180, 'stock-bridge'),
            ('v1b', 360, 'v1-bridge'),
        ):
            path = os.path.join(directory, name + '.json')
            write_json(path, series(start, 60, 20, label, **short))
            paths[name] = path
        evidence = os.path.join(directory, 'evidence.json')
        write_json(evidence, EVIDENCE)
        stdout = sys.stdout
        captured = Capture()
        sys.stdout = captured
        try:
            code = rep.main([
                'window', '--history', paths['v1'], '--start', '0', '--end', '60', '--boards', '0',
            ])
        finally:
            sys.stdout = stdout
        if code != 0:
            return fail('window CLI failed')
        payload = json.loads(''.join(captured.parts))
        if payload.get('kind') != 'exact-window' or not payload.get('valid'):
            return fail('window CLI did not emit a valid window')
        captured = Capture()
        sys.stdout = captured
        try:
            code = rep.main([
                'compare', '--boards', '0',
                '--stock-history', paths['stock'], '--stock-start', '180', '--stock-end', '240',
                '--v1-history', paths['v1'], '--v1-start', '0', '--v1-end', '60',
                '--v1b-history', paths['v1b'], '--v1b-start', '360', '--v1b-end', '420',
                '--evidence', evidence,
            ])
        finally:
            sys.stdout = stdout
        if code != 0:
            return fail('compare CLI failed')
        payload = json.loads(''.join(captured.parts))
        if payload.get('status') != 'screening' or payload.get('deployment_gates'):
            return fail('evidence did not clear deployment gates on a short compare: %s' % payload.get('status'))
        missing = rep.main(['window', '--history', os.path.join(directory, 'missing.json'), '--start', '0', '--end', '60'])
        if missing != 2:
            return fail('missing history did not exit 2')
        try:
            bare = rep.main([])
        except SystemExit as exc:
            bare = exc.code
        if bare != 2:
            return fail('missing subcommand did not exit 2')
    finally:
        for name in os.listdir(directory):
            os.remove(os.path.join(directory, name))
        os.rmdir(directory)
    return 0


def test_driver_host_sha_is_running():
    legs = three(21600, (50, 50, 50), sync='co-snapshot')
    for leg in legs:
        host = leg['history']['host'][0]
        host['source'] = rep.DRIVER_HOST_SHA
        host['path'] = None
        host['identity'] = {
            'kind': 'diag-host-sha256',
            'pid': 9,
            'start': 1790640000,
            'epoch': '9:7654321',
        }
    result = rep.compare_legs(legs, boards=(0,), evidence=EVIDENCE)
    if result['status'] != 'equivalent' or result['conclusion'] != 'definitive':
        return fail('DiagHostSha256 running hash was not definitive: %s %s' % (
            result['status'], result['reasons']))
    shown = result['legs']['v1a']['boards']['0']['host']
    if (shown.get('source') != rep.DRIVER_HOST_SHA or
            shown.get('provenance') != RUNNING or shown.get('path') is not None):
        return fail('report dropped the driver running-hash source: %s' % shown)

    malformed = three(21600, (50, 50, 50), sync='co-snapshot')
    for leg in malformed:
        leg['history']['host'][0]['source'] = rep.DRIVER_HOST_SHA
        leg['history']['host'][0]['sha256'] = 'AB' * 32
    bad = rep.compare_legs(malformed, boards=(0,), evidence=EVIDENCE)
    if bad['status'] == 'equivalent' or bad['conclusion'] == 'definitive' or (
            'host-not-running-binary' not in bad['reasons']):
        return fail('malformed DiagHostSha256 stayed definitive: %s' % bad['status'])

    configured = three(21600, (50, 50, 50), sync='co-snapshot')
    for leg in configured:
        leg['history']['host'][0]['source'] = rep.DRIVER_HOST_SHA
        leg['history']['host'][0]['provenance'] = CONFIGURED
        for board in leg['history']['boards']:
            for sample in board['samples']:
                sample['hp'] = CONFIGURED
    downgraded = rep.compare_legs(configured, boards=(0,), evidence=EVIDENCE)
    if downgraded['status'] == 'equivalent' or 'host-not-running-binary' not in downgraded['reasons']:
        return fail('configured path was promoted by a DiagHostSha256 label')
    return 0


def main():
    for check in (
            test_formula_bounds, test_window_faults, test_comparisons,
            test_driver_host_sha_is_running, test_cli):
        result = check()
        if result:
            return result
    print('PASS bkb diagnostics report')
    return 0


if __name__ == '__main__':
    sys.exit(main())
