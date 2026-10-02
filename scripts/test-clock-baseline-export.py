#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('exporter', str(Path(__file__).with_name('export-clock-baseline.py')))
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
PUBLIC_BRIDGE = E.TRIAL.BRIDGE_ARTIFACT_SHA256


class Baselines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        (self.run / 'legs').mkdir(parents=True)
        self.leg = {'status': 'success', 'name': 'reference', 'indices': [30, 30, 30],
                    'measurement_seconds': 1800, 'monotonic_seconds': 1801,
                    'hw_policy': 'logged-budget-10-v1', 'boards': [
                        {'id': i, 'index': 30, 'algorithm': 'Blakecoin', 'rate_ghs': 60.0,
                         'difficulty_delta': 60.0 * 1e9 * 1801 / 2**32, 'accepted_delta': 1000,
                         'device_elapsed_seconds': 1801, 'hw_errors_delta': i,
                         'leg_hw_errors': i, 'nominal_mhz': 400, 'exploratory': True}
                        for i in range(3)]}

    def write(self):
        with (self.run / 'legs' / 'reference.json').open('w') as handle:
            json.dump(self.leg, handle)

    def collect(self, algorithm='blake256r8'):
        self.write()
        return E.collect(algorithm, [str(self.run)])

    def public_data(self):
        self.leg['host_sha256'] = 'a' * 64
        self.leg['bridge_artifact_sha256'] = PUBLIC_BRIDGE
        for row in self.leg['boards']:
            row['rejected_delta'] = 0
        data = self.collect()
        candidate = dict(data['observations'][1])
        candidate.update(index=32, indices=[30, 32, 30], difficulty_delta=candidate['difficulty_delta'] * 1.1,
                         source_report='/private/candidate/legs/clock_index32.json')
        data['observations'].append(candidate)
        return data

    def test_shared_public_example_recomputes_work_not_model_rate(self):
        data = self.public_data()
        data['observations'][-1]['rate_ghs'] = 9999
        public = E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)
        stock, candidate = public['rows']
        self.assertEqual(stock['clock_index'], 30)
        self.assertEqual(stock['estimated_mhz'], 400)
        self.assertAlmostEqual(stock['ghs'], 60)
        self.assertEqual(candidate['measured_boards'], [1])
        self.assertAlmostEqual(candidate['ghs'], 66)
        self.assertAlmostEqual(candidate['estimated_mhz'], 440)
        self.assertEqual(candidate['hw_errors'], 1)
        self.assertFalse(public['qualified'])
        self.assertIsNone(public['physical_mhz'])
        self.assertNotIn('/private/', json.dumps(public))
        self.assertNotIn('source_report', json.dumps(public))

    def test_public_projection_keeps_algorithm_and_identity_separate(self):
        data = self.public_data()
        data['observations'][-1]['host_sha256'] = 'c' * 64
        self.assertEqual(len(E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)['rows']), 1)
        with self.assertRaises(ValueError):
            E.public_baseline(data, 'a' * 64, 'b' * 64)
        data['algorithm'] = 'x11'
        with self.assertRaises(ValueError):
            E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)

    def test_bridge_compatibility_never_rewrites_measured_provenance(self):
        for bridge, md5 in E.MEASURED_BRIDGES.items():
            data = self.public_data()
            for row in data['observations']:
                row['bridge_artifact_sha256'] = bridge
            public = E.public_baseline(data, 'a' * 64, bridge)
            self.assertEqual(public['bridge_artifact_sha256'], bridge)
            self.assertEqual(public['bridge_chip_md5'], md5)
            self.assertEqual(public['compatible_bridge_chip_md5'],
                             [] if bridge == PUBLIC_BRIDGE else [E.MEASURED_BRIDGES[PUBLIC_BRIDGE]])
            self.assertAlmostEqual(public['rows'][0]['ghs'], 60)

    def test_public_mhz_requires_same_board_stock_reference(self):
        data = self.public_data()
        data['observations'] = [r for r in data['observations'] if r['indices'] != [30, 30, 30] or r['id'] != 1]
        candidate = E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)['rows'][-1]
        self.assertAlmostEqual(candidate['ghs'], 66)
        self.assertIsNone(candidate['estimated_mhz'])

    def test_public_duplicate_or_incomplete_observations_refused(self):
        data = self.public_data()
        data['observations'].append(dict(data['observations'][-1]))
        with self.assertRaises(ValueError):
            E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)
        data['observations'].pop()
        data['observations'][-1]['device_elapsed_seconds'] = 1799
        with self.assertRaises(ValueError):
            E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)

    def test_public_invalid_clock_and_false_frequency_refused(self):
        for key, value in [('index', 33), ('qualified', True), ('physical_mhz', 500),
                           ('rejected_delta', 1), ('difficulty_delta', float('nan'))]:
            data = self.public_data()
            data['observations'][-1][key] = value
            with self.assertRaises(ValueError):
                E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)

    def test_public_uses_time_weighted_same_board_reference(self):
        data = self.public_data()
        second = dict(data['observations'][-1])
        second.update(id=0, indices=[32, 30, 30], source_report='/another/window.json',
                      device_elapsed_seconds=3602, difficulty_delta=60.0 * 1e9 * 3602 / 2**32)
        data['observations'].append(second)
        row = E.public_baseline(data, 'a' * 64, PUBLIC_BRIDGE)['rows'][-1]
        self.assertAlmostEqual(row['ghs'], 62)
        self.assertAlmostEqual(row['estimated_mhz'], 400 * 62 / 60)
        self.assertEqual(row['measured_boards'], [0, 1])
        self.assertEqual(row['board_windows'], 2)

    def test_canonical_alias_and_error_evidence(self):
        data = self.collect()
        self.assertEqual(data['algorithm'], 'blake256r8')
        self.assertEqual(len(data['observations']), 3)
        for board in data['observations']:
            self.assertEqual(board['algorithm'], 'blake256r8')
            self.assertEqual(board['reported_algorithm'], 'Blakecoin')
            self.assertEqual(board['hw_errors_delta'], board['id'])
            self.assertFalse(board['qualified'])
            self.assertIsNone(board['physical_mhz'])
            self.assertEqual(board['frequency_status'], 'unverified')

    def test_mixed_or_wrong_algorithm_refused(self):
        self.leg['boards'][1]['algorithm'] = 'X11'
        with self.assertRaises(ValueError):
            self.collect()
        for board in self.leg['boards']:
            board['algorithm'] = 'X11'
        with self.assertRaises(ValueError):
            self.collect()
        self.assertEqual(len(self.collect('x11')['observations']), 3)

    def test_separate_namespaces_and_no_rewrite_for_unchanged_data(self):
        first = self.collect()
        E.save(str(self.root / 'baselines'), 'blake256r8', first)
        path = self.root / 'baselines' / 'blake256r8' / 'baseline.json'
        before = path.stat().st_mtime_ns
        E.save(str(self.root / 'baselines'), 'blake256r8', first)
        self.assertEqual(path.stat().st_mtime_ns, before)
        for board in self.leg['boards']:
            board['algorithm'] = 'X11'
        E.save(str(self.root / 'baselines'), 'x11', self.collect('x11'))
        with path.open() as handle:
            self.assertEqual(json.load(handle), first)
        with self.assertRaises(ValueError):
            E.save(str(self.root), '../escape', first)

    def test_failed_window_never_becomes_a_rate(self):
        self.leg['status'] = 'failed'
        self.leg['reasons'] = ['hw-budget-total']
        data = self.collect()
        self.assertEqual(data['observations'], [])
        self.assertEqual(data['failed_legs'][0]['reasons'], ['hw-budget-total'])

    def test_incomplete_window_refused(self):
        self.leg['boards'][0]['device_elapsed_seconds'] = 1799
        with self.assertRaises(ValueError):
            self.collect()

    def test_empty_running_dataset_is_not_a_fabricated_baseline(self):
        data = E.collect('blake256r8', [str(self.run)])
        self.assertFalse(data['qualified'])
        self.assertEqual(data['observations'], [])
        self.assertEqual(data['averages'], [])

    def test_average_uses_accepted_work_over_time_not_mean_of_rates(self):
        self.write()
        self.leg['name'] = 'long-reference'
        self.leg['measurement_seconds'] = self.leg['monotonic_seconds'] = 3600
        for board in self.leg['boards']:
            board.update({'device_elapsed_seconds': 3600, 'rate_ghs': 30.0,
                          'difficulty_delta': 30.0 * 1e9 * 3600 / 2**32})
        with (self.run / 'legs' / 'long-reference.json').open('w') as handle:
            json.dump(self.leg, handle)
        data = E.collect('blake256r8', [str(self.run), str(self.run)])
        self.assertEqual(len(data['observations']), 6, 'duplicate runs must not count twice')
        self.assertEqual(len(data['averages']), 3)
        for row in data['averages']:
            self.assertAlmostEqual(row['accepted_ghs'], (60 * 1801 + 30 * 3600) / 5401)
            self.assertEqual(row['windows'], 2)
            self.assertEqual(row['accepted_delta'], 2000)
            self.assertEqual(row['hw_errors_delta'], row['board_id'] * 2)
            self.assertIsNone(row['physical_mhz'])

    def test_average_keeps_board_tuple_and_unknown_runs_separate(self):
        data = self.collect()
        rows = data['observations']
        changed = dict(rows[0], indices=[30, 29, 28])
        other_run = dict(rows[0], source_report=str(self.root / 'another' / 'legs' / 'ref.json'))
        self.assertEqual(len(E.average_observations(rows + [changed, other_run])), 5)

    def test_estimated_mhz_is_relative_and_never_physical(self):
        row = self.collect()['observations'][0]
        candidate = dict(row, index=26, indices=[26, 25, 24],
                         difficulty_delta=row['difficulty_delta'] * 0.9, rate_ghs=54.0,
                         source_report=str(self.run / 'legs' / 'candidate.json'))
        averages = E.average_observations([row, candidate])
        estimated = next(a for a in averages if a['clock_index'] == 26)
        self.assertEqual(estimated['estimated_mhz'], 360)
        self.assertEqual(estimated['estimate_kind'], 'performance-equivalent')
        self.assertEqual(estimated['frequency_status'], 'unverified')
        self.assertIsNone(estimated['physical_mhz'])
        self.assertFalse(estimated['qualified'])
        self.assertAlmostEqual(estimated['relative_performance_pct'], 90)

    def test_estimate_requires_same_board_reference_and_context(self):
        row = self.collect()['observations'][0]
        candidate = dict(row, index=26, indices=[26, 25, 24])
        self.assertIsNone(E.average_observations([candidate])[0]['estimated_mhz'])
        other_board = dict(row, id=1)
        averages = E.average_observations([candidate, other_board])
        self.assertIsNone(next(a for a in averages if a['clock_index'] == 26)['estimated_mhz'])
        foreign = dict(row, source_report=str(self.root / 'foreign' / 'legs' / 'reference.json'))
        averages = E.average_observations([candidate, foreign])
        self.assertIsNone(next(a for a in averages if a['clock_index'] == 26)['estimated_mhz'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
