#!/usr/bin/env python
from __future__ import print_function

import os
import json
import shutil
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDER = os.path.join(
    ROOT, 'overlay', 'opt', 'scripta', 'bin', 'clock-history-recorder.py')
try:
    import importlib.util
except ImportError:
    import imp
    module = imp.load_source('bkb_clock_history_recorder_test', RECORDER)
else:
    spec = importlib.util.spec_from_file_location(
        'bkb_clock_history_recorder_test', RECORDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
mod = module.__dict__


def fail(message):
    sys.stderr.write('FAIL: %s\n' % message)
    return 1


def sample_at(timestamp, accepted, difficulty, overrides=None):
    sample = {
        't': timestamp,
        'a': accepted,
        'da': difficulty,
        'de': timestamp,
        'se': 7,
        'cg': 12,
        'ep': 1,
        'pool': 'pool.example:3334',
        'en': 1,
    }
    sample.update(overrides or {})
    return sample


def main():
    samples = [
        sample_at(1000 + (i * 60), 100 + (i * 10), 5000 + (i * 640))
        for i in range(16)
    ]
    rate = mod['accepted_work_rate'](samples, 15 * 60)
    expected = (9600.0 * 4294967296.0) / 900.0 / 1000000000.0
    if not rate['valid'] or abs(rate['ghs'] - expected) > 0.000001:
        return fail('valid fixed-difficulty rate does not match the formula')

    generation_change = [dict(s) for s in samples]
    generation_change[14]['cg'] = 13
    generation_change[15]['cg'] = 13
    if mod['accepted_work_rate'](generation_change, 15 * 60)['valid']:
        return fail('clock-generation boundary was accepted')

    route_change = [dict(s) for s in samples]
    route_change[14]['ep'] = 2
    route_change[15]['ep'] = 2
    if mod['accepted_work_rate'](route_change, 15 * 60)['valid']:
        return fail('pool-route boundary was accepted')

    disabled = [dict(s) for s in samples]
    disabled[14]['en'] = 0
    if mod['accepted_work_rate'](disabled, 15 * 60)['valid']:
        return fail('disabled-board boundary was accepted')

    reset = [dict(s) for s in samples]
    reset[15].update({'se': 8, 'a': 1, 'da': 32})
    if mod['accepted_work_rate'](reset, 15 * 60)['valid']:
        return fail('counter-reset boundary was accepted')

    for key in ('xp', 'pi', 'scc', 'pid', 'hs', 'ds', 're', 'ee'):
        changed = [dict(s) for s in samples]
        changed[14][key] = 99
        changed[15][key] = 99
        if mod['accepted_work_rate'](changed, 900)['valid']:
            return fail('%s identity boundary was accepted' % key)

    summary = mod['accepted_rates_summary'](
        {'boards': [{'id': 0, 'samples': samples}]}, 1900)
    if not summary['boards']['0']['15m']['valid']:
        return fail('compact summary did not include a valid board rate')
    if abs(summary['boards']['0']['15m']['ghs'] - expected) > 0.000001:
        return fail('compact summary rate does not match the formula')

    directory = tempfile.mkdtemp(prefix='bkb-accepted-work-')
    original_dump = json.dump
    original_dumps = json.dumps
    try:
        path = os.path.join(directory, 'history.json')

        def reject_streaming(*args, **kwargs):
            raise AssertionError('streaming encoder exceeds the controller timeout')

        json.dump = reject_streaming
        mod['write_json_atomic'](path, summary)
        with open(path) as handle:
            if json.load(handle) != summary:
                return fail('atomic JSON write changed accepted-work data')

        def interrupt_encoding(*args, **kwargs):
            raise RuntimeError('interrupted encoding')

        json.dumps = interrupt_encoding
        try:
            mod['write_json_atomic'](path, {'updated': 2000})
            return fail('encoding interruption was ignored')
        except RuntimeError:
            pass
        with open(path) as handle:
            if json.load(handle) != summary:
                return fail('interrupted write replaced the previous history')
        if os.listdir(directory) != ['history.json']:
            return fail('interrupted write left a temporary file')
    finally:
        json.dump = original_dump
        json.dumps = original_dumps
        shutil.rmtree(directory)

    print('PASS recorder accepted-work %.6f GH/s and boundary guards' %
          rate['ghs'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
