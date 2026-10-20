#!/usr/bin/env python3
"""Compare a full week's recorder memory in isolated child interpreters."""
from __future__ import print_function
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDER = os.path.join(ROOT, 'overlay/opt/scripta/bin/clock-history-recorder.py')
try:
    import importlib.util
except ImportError:
    import imp
    recorder = imp.load_source('clock_memory', RECORDER)
else:
    spec = importlib.util.spec_from_file_location('clock_memory', RECORDER)
    recorder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recorder)


def sample(t):
    return dict(t=t, c=400, rc=400, cc=400, fcc=240, scc=30, xp=1, pi=30,
                pid=1234, hs='a' * 64, ds=1, re=2, ee=0, cv=1, cm='global',
                rec=40, cut=55, fan=75, fm='auto', prof='global:400:40:55:75:auto',
                tmp=40.0, m5=56000.0, ma=56000.0, a=t, da=float(t * 32),
                de=t, se=1, cg=1, en=1, r=0, hw=0, u=26.0, pp=0, ep=0,
                fo=False, st='Alive', ls=t, pool='pool.example:3334')


def write_week_fixture(path):
    history = recorder.empty_history()
    history['boards'] = [
        {'id': bid, 'samples': [sample(1000000 + i * 60) for i in range(10081)]}
        for bid in range(3)
    ]
    with open(path, 'w') as handle:
        json.dump(history, handle, separators=(',', ':'))


class ClockHistoryMemory(unittest.TestCase):
    def test_batched_atomic_output_and_interrupted_write(self):
        directory = tempfile.mkdtemp(prefix='clock batches ')
        original_dumps = json.dumps
        try:
            path = os.path.join(directory, 'history.json')
            for count in (0, 1, 127, 128, 129, 257):
                data = {'boards': [{'samples': [sample(i) for i in range(count)]}],
                        'label': 'quotes " and slash \\'}
                recorder.write_json_atomic(path, data)
                with open(path) as handle:
                    self.assertEqual(json.load(handle), data)
            with open(path, 'rb') as handle:
                previous = handle.read()
            calls = [0]

            def fail_second_batch(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 4:
                    raise RuntimeError('interrupted batch')
                return original_dumps(*args, **kwargs)

            json.dumps = fail_second_batch
            with self.assertRaises(RuntimeError):
                recorder.write_json_atomic(path, data)
            with open(path, 'rb') as handle:
                self.assertEqual(handle.read(), previous)
            self.assertEqual(os.listdir(directory), ['history.json'])
        finally:
            json.dumps = original_dumps
            shutil.rmtree(directory)

    def test_recorder_tick_preserves_history_in_ram_and_sd(self):
        directory = tempfile.mkdtemp(prefix='clock tick ')
        names = ['SCRIPTA_CONF', 'MINER_OPTIONS', 'FAN_STATE', 'RAM_HISTORY',
                 'SD_HISTORY', 'SD_BUFFER_HISTORY', 'call_api', 'Service']
        saved = dict((name, getattr(recorder, name)) for name in names)
        now = int(time.time())
        calls = []

        class FakeService(object):
            def record(self, *args, **kwargs):
                calls.append(True)

        responses = {
            'devs': {'DEVS': [{'ASC': 0, 'Enabled': 'Y', 'Accepted': now,
                               'Difficulty Accepted': now * 32, 'Device Elapsed': now}]},
            'stats': {'STATS': [{'ID': 'BKLU0', 'ClockMode': 'global'}]},
            'pools': {'POOLS': []},
        }
        try:
            for name in names[:6]:
                setattr(recorder, name, os.path.join(directory, name + '.json'))
            recorder.call_api = responses.get
            recorder.Service = FakeService
            for sd in (False, True):
                with open(recorder.SCRIPTA_CONF, 'w') as handle:
                    json.dump({'userAdjustableClocks': True, 'userPersistClockHistory': sd}, handle)
                target = recorder.SD_BUFFER_HISTORY if sd else recorder.RAM_HISTORY
                before = sample(now - 60)
                with open(target, 'w') as handle:
                    json.dump({'boards': [{'id': 0, 'samples': [
                        sample(now - 8 * 86400), before]}]}, handle)
                self.assertEqual(recorder.main(), 0)
                with open(target) as handle:
                    result = json.load(handle)
                samples = result['boards'][0]['samples']
                self.assertEqual(len(samples), 2)
                self.assertEqual(samples[0], before)
                self.assertGreaterEqual(samples[1]['t'], now)
                if sd:
                    with open(recorder.SD_HISTORY) as handle:
                        self.assertEqual(json.load(handle), result)
            self.assertEqual(len(calls), 2)
        finally:
            for name, value in saved.items():
                setattr(recorder, name, value)
            shutil.rmtree(directory)

    def test_full_week_round_trip_and_peak_memory(self):
        directory = tempfile.mkdtemp(prefix='clock memory ')
        try:
            path = os.path.join(directory, 'history.json')
            # Fixture allocation must not inflate a child's inherited RSS peak.
            subprocess.check_call([sys.executable, __file__, '--seed', path])
            results = {}
            for mode in ('plain', 'compact'):
                output = subprocess.check_output([sys.executable, __file__, '--profile', path, mode])
                results[mode] = json.loads(output.decode('utf-8'))
            self.assertLess(results['compact']['peak_kib'], results['plain']['peak_kib'] * 0.8)
            with open(path + '.plain') as handle:
                expected = json.load(handle)
            with open(path + '.compact') as handle:
                self.assertEqual(json.load(handle), expected)
            self.assertEqual([len(b['samples']) for b in expected['boards']], [10081] * 3)
            print('Week peak RSS KiB: %s' % json.dumps(results, sort_keys=True))
        finally:
            shutil.rmtree(directory)

    def test_compact_samples_keep_unknown_fields_and_rate_boundaries(self):
        source = [sample(1000 + i * 60) for i in range(16)]
        source[0]['future_field'] = {'text': 'quoted " text', 'values': [1, True, None]}
        compact = json.loads(json.dumps(source), object_hook=recorder.compact_sample_hook())
        self.assertTrue(all(isinstance(s, recorder.HistorySample) for s in compact))
        encoded = json.dumps(compact, default=recorder.encode_history_sample)
        self.assertEqual(json.loads(encoded), source)
        self.assertEqual(recorder.accepted_work_rate(compact, 900),
                         recorder.accepted_work_rate(source, 900))
        self.assertIs(compact[1].fields, compact[2].fields)
        self.assertIs(compact[1].get('pool'), compact[2].get('pool'))
        self.assertEqual(compact[0].get('missing', 42), 42)
        self.assertEqual(recorder.compact_sample_hook()({'id': 1}), {'id': 1})


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--seed':
        write_week_fixture(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == '--profile':
        path, mode = sys.argv[2:]
        data = recorder.load_json(path, {}, compact=(mode == 'compact'))
        recorder.write_json_atomic(path + '.' + mode, data)
        print(json.dumps({'peak_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
    else:
        unittest.main(verbosity=2)
