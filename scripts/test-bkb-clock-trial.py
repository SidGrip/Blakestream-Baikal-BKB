#!/usr/bin/env python
"""Python 2.7/3 fixtures: no miner, network, root, real sleeps or restarts."""
from __future__ import print_function
import copy
import json
import os
import shutil
import signal
import tempfile
import unittest
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

ROOT = os.path.dirname(os.path.abspath(__file__))
try:
    import importlib.util
    SPEC = importlib.util.spec_from_file_location('bkb_trial', os.path.join(ROOT, 'bkb-clock-trial.py'))
    T = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(T)
except ImportError:
    import imp
    T = imp.load_source('bkb_trial', os.path.join(ROOT, 'bkb-clock-trial.py'))
SHA = 'a' * 64


def schedule():
    return {'legs': [{'name': 'board0', 'indices': [22, 30, 30],
                      'measurement_seconds': 1800, 'warmup_seconds': 120}]}


class Fixture(object):
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix='bkb trial fixture ')
        self.now = 1000.0
        self.boot = 990.0
        self.restarts = 0
        self.fired = False
        self.hook = None
        self.stat_override = {}
        self.applied = False
        self.indices = [30, 30, 30]
        self.outage = 0
        self.options = [
            {'key': 'baikal-options', 'value': '400:40:55'},
            {'key': 'baikal-board-clocks', 'value': '400:400:400'},
            {'key': 'baikal-clock-mode', 'value': 'individual'},
            {'key': 'baikal-fan', 'value': '80'},
            {'key': 'fixture-unrelated', 'value': 'preserved'},
        ]
        self.settings = T.Settings()
        self.settings.host_sha = SHA
        self.settings.options_path = os.path.join(self.root, 'options.json')
        self.settings.output_dir = os.path.join(self.root, 'results')
        self.settings.lock_path = os.path.join(self.root, 'lock')
        self.settings.stop_file = os.path.join(self.root, 'STOP')
        self.settings.backup_path = os.path.join(self.root, 'backup.json')
        self.settings.bridge_file = os.path.join(self.root, 'bridge.bin')
        with open(self.settings.bridge_file, 'wb') as handle:
            handle.write(b'fixture; hash checked through injected dependency')
        self.original = T.dump_json(self.options, pretty=True)
        with open(self.settings.options_path, 'wb') as handle:
            handle.write(self.original)
        os.chmod(self.settings.options_path, 0o640)
        self.deps = T.Dependencies()
        self.deps.monotonic = lambda: self.now
        self.deps.time = lambda: self.now
        self.deps.sleep = self.sleep
        self.deps.restart = self.restart
        self.deps.api = self.api
        self.deps.euid = lambda: 0
        self.deps.hash_file = lambda *args: T.BRIDGE_ARTIFACT_SHA256
        self.deps.stdout = StringIO()
        self.deps.stderr = StringIO()
        self.deps.install_signals = False
        self.runner = T.Runner(self.deps, self.settings)
        self.runner.schedule = T.validate_schedule(schedule())

    def close(self):
        self.runner._release()
        shutil.rmtree(self.root)

    def read_options(self):
        with open(self.settings.options_path, 'rb') as handle:
            return json.load(handle)

    def save_options(self, items):
        with open(self.settings.options_path, 'wb') as handle:
            handle.write(T.dump_json(items, pretty=True))

    def restart(self):
        mapped = T.value_map(self.read_options())
        self.applied = mapped.get('baikal-experimental-clock-index') is True
        self.indices = [int(x) for x in mapped['baikal-board-clock-index'].split(':')] if self.applied else [30] * 3
        self.restarts += 1
        self.boot = self.now - 10

    def sleep(self, seconds):
        self.now += seconds
        if self.hook and not self.fired and self.runner.phase == 'measure':
            self.fired = True
            self.hook(self)

    def api(self, command):
        if self.outage:
            self.outage -= 1
            raise T.TrialAborted('api-timeout', 'fixture transient outage')
        elapsed = int(self.now - self.boot)
        if command == 'devs':
            return {'DEVS': [dict(ASC=i, Status='Alive', Enabled='Y', Temperature=40) for i in range(3)]}
        if command != 'stats':
            raise AssertionError('mutating API command')
        rows = []
        for i in range(3):
            accepted = int(elapsed * (1.5 if i == 0 else 0.4))
            row = {
                'ID': 'BKLU%d' % i, 'DiagBoard': i, 'Chip Count': 64,
                'StartupSuccessCount': 64, 'Temperature': 40,
                'RequestedClock': 400, 'SentClockIndex': self.indices[i],
                'RequestedClockIndex': self.indices[i] if self.applied else 0,
                'ExperimentalClock': self.applied, 'CommandVerified': True,
                'ClockMode': 'individual', 'ClockGeneration': i + 1,
                'Algo': 'blake256r8',
                'GlobalClockPending': False, 'GlobalClockInflight': False,
                'ClockRetryPending': False, 'ClockReapplyPending': False,
                'Failover Active': False, 'Effective Pool': i,
                'DiagHostSha256': SHA, 'DiagPid': 100 + self.restarts,
                'DiagStartUnix': 10000 + self.restarts,
                'DiagStatEpoch': i, 'DiagRouteEpoch': i + 2, 'DiagEnableEpoch': i + 3,
                'DiagShareSnapValid': 1, 'DiagDeviceElapsedValid': 1,
                'DiagAccepted': accepted, 'DiagRejected': 0, 'DiagHwErrors': 0,
                'DiagDiffAccepted': accepted * 32.0, 'DiagDeviceElapsed': elapsed,
                'DiagUsbTxErrors': 0, 'DiagUsbRxErrors': 0, 'DiagInvalidId': 0,
                'FanPercent': 80,
            }
            if self.applied and i == 1:
                row.update(self.stat_override)
            rows.append(row)
        return {'STATS': rows}


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def assert_restored(self, exact=True):
        self.assertTrue(self.f.runner.restore_verified, self.f.deps.stderr.getvalue())
        self.assertFalse(self.f.applied)
        if exact:
            with open(self.f.settings.options_path, 'rb') as handle:
                self.assertEqual(handle.read(), self.f.original)
        self.assertEqual(os.stat(self.f.settings.options_path).st_mode & 0o777, 0o640)

    def test_normal_observe_and_independent_epochs(self):
        self.f.settings.output_dir = None
        self.assertEqual(self.f.runner.observe(), 0)
        self.assertEqual(self.f.restarts, 0)

    def test_complete_window_and_per_board_confidence(self):
        self.assertEqual(self.f.runner.run(), 0, self.f.deps.stderr.getvalue())
        self.assert_restored()
        self.assertEqual(self.f.restarts, 2)
        report = self.f.runner.legs_report[0]
        self.assertEqual(report['status'], 'success')
        self.assertGreaterEqual(report['monotonic_seconds'], 1800)
        self.assertTrue(report['exploratory'])
        self.assertFalse(report['boards'][0]['exploratory'])
        self.assertTrue(report['boards'][1]['exploratory'])
        self.assertFalse(report['power_measured'])
        for board in report['boards']:
            self.assertEqual(board['algorithm'], 'blake256r8')
            self.assertIsNone(board['physical_mhz'])
            self.assertEqual(board['frequency_status'], 'unverified')
            self.assertEqual(board['nominal_mhz'], 400 if board['index'] == 30 else None)
            self.assertGreaterEqual(board['device_elapsed_seconds'], 1800)
            self.assertAlmostEqual(board['rate_ghs'], board['difficulty_delta'] * 2**32 / board['device_elapsed_seconds'] / 1e9)
        self.assertEqual(os.stat(self.f.settings.backup_path).st_mode & 0o777, 0o600)
        self.assertEqual(T.Runner(self.f.deps, self.f.settings).restore_command(), 0)
        self.assertEqual(self.f.restarts, 2, 'idempotent recovery must not restart again')

    def test_algorithm_change_aborts_and_restores(self):
        self.f.hook = lambda f: f.stat_override.update({'Algo': 'sha256d'})
        self.assertEqual(self.f.runner.run(), T.EXIT_FAILED)
        self.assertIn('algorithm-changed', self.f.runner.failure_reasons)
        self.assert_restored()

    def test_algorithm_selection_accepts_reported_alias_but_not_another_algorithm(self):
        data = schedule()
        data['algorithm'] = 'blake256r8'
        self.f.runner.schedule = T.validate_schedule(data)
        parsed = T.parse_boards(self.f.api('devs'), self.f.api('stats'))
        expected = self.f.runner._expected([30] * 3, False, 'individual', 'precheck')
        expected['temp_limit'] = 50
        for board in parsed['boards'].values():
            board['algorithm'] = 'Blakecoin'
        self.assertEqual(T.assess_boards(parsed, expected, {}, 1000), [])
        for board in parsed['boards'].values():
            board['algorithm'] = 'X11'
        self.assertIn('algorithm-mismatch', T.assess_boards(parsed, expected, {}, 1000))
        for value in ('Blakecoin', '../blake256r8', '', 12):
            data['algorithm'] = value
            with self.assertRaises(T.TrialError):
                T.validate_schedule(data)

    def test_missing_and_mixed_algorithms_reject_precheck(self):
        devs, stats = self.f.api('devs'), self.f.api('stats')
        for value, reason in ((None, 'algorithm-missing'), ('sha256d', 'mixed-algorithms')):
            stats['STATS'][0]['Algo'] = value
            parsed = T.parse_boards(devs, stats)
            expected = self.f.runner._expected([30, 30, 30], False, 'individual', 'precheck')
            expected['temp_limit'] = 50
            self.assertIn(reason, T.assess_boards(parsed, expected, {}, self.f.now))

    def startup_algorithm(self, never_ready=False):
        original_api = self.f.deps.api

        def initializing(command):
            response = original_api(command)
            if command == 'stats' and self.f.applied and (never_ready or self.f.now - self.f.boot < 15):
                response['STATS'][1].update({'Algo': 'X11', 'GlobalClockPending': True,
                                            'CommandVerified': False, 'DiagAccepted': 0})
            return response

        self.f.deps.api = initializing

    def test_startup_algorithm_waits_only_until_clock_is_ready(self):
        self.startup_algorithm()
        self.assertEqual(self.f.runner.run(), 0)
        self.assert_restored()
        self.assertEqual(self.f.runner.legs_report[0]['boards'][1]['algorithm'], 'blake256r8')

    def test_startup_algorithm_wait_is_bounded(self):
        self.startup_algorithm(never_ready=True)
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assert_restored()
        self.assertLess(self.f.now, 1000 + T.SETTLE_DEADLINE + 60)

    def test_algorithm_pending_cannot_hide_work_or_healthy_clock(self):
        parsed = T.parse_boards(self.f.api('devs'), self.f.api('stats'))
        expected = self.f.runner._expected([30] * 3, False, 'individual', 'settle')
        expected['temp_limit'] = 50
        expected['algorithms'] = dict((i, 'blake256r8') for i in T.BOARD_IDS)
        board = parsed['boards'][0]
        board.update({'algorithm': 'X11', 'pending': True, 'command_verified': False, 'accepted': 0})
        self.assertIn('algorithm-pending', T.assess_boards(parsed, expected, {}, 1000))
        for field, value in (('accepted', 1), ('pending', False), ('command_verified', True)):
            changed = copy.deepcopy(parsed)
            changed['boards'][0][field] = value
            self.assertIn('algorithm-changed', T.assess_boards(changed, expected, {}, 1000))
        for phase in ('precheck', 'warmup', 'measure', 'verify'):
            expected['phase'] = phase
            self.assertIn('algorithm-changed', T.assess_boards(parsed, expected, {}, 1000), phase)

    def test_dry_run_has_no_side_effects(self):
        path = os.path.join(self.f.root, 'schedule.json')
        with open(path, 'w') as handle:
            json.dump(schedule(), handle)
        before = sorted(os.listdir(self.f.root))
        self.assertEqual(T.main(['run', path], self.f.deps), 0)
        self.assertEqual(sorted(os.listdir(self.f.root)), before)
        self.assertEqual(self.f.restarts, 0)

    def test_schedule_rejects_coercions_and_short_windows(self):
        for field, bad in [('indices', [True, 30, 30]), ('indices', [8.0, 30, 30]),
                           ('indices', ['8', 30, 30]), ('indices', [7, 30, 30]),
                           ('indices', [33, 30, 30]), ('indices', [30, 30]),
                           ('measurement_seconds', 1799), ('measurement_seconds', 3601),
                           ('warmup_seconds', 119)]:
            data = schedule()
            data['legs'][0][field] = bad
            with self.assertRaises(T.TrialError):
                T.validate_schedule(data)
        self.assertTrue(T.measurement_window_problems(1800, 1800, 1799))
        self.assertTrue(T.measurement_window_problems(1800, 2100, 1800))

    def test_identity_conflict_rejected(self):
        devs, stats = self.f.api('devs'), self.f.api('stats')
        stats['STATS'][0]['DiagBoard'] = 1
        self.assertTrue(T.parse_boards(devs, stats)['structural'])

    def test_wrong_host_never_mutates(self):
        self.f.settings.host_sha = 'b' * 64
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assertEqual(self.f.restarts, 0)
        self.assertFalse(self.f.runner.mutated)

    def test_concurrent_lock_and_existing_output(self):
        lock = T.FileLock(self.f.settings.lock_path).acquire()
        try:
            self.assertEqual(self.f.runner.run(), T.EXIT_LOCK)
        finally:
            lock.release()
        os.mkdir(self.f.settings.output_dir)
        self.assertEqual(self.f.runner.run(), T.EXIT_OUTPUT)

    def test_unrelated_intervention_is_preserved(self):
        def intervene(f):
            items = f.read_options()
            items.append({'key': 'user-added', 'value': 'keep'})
            f.save_options(items)
        self.f.hook = intervene
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assert_restored(exact=False)
        mapped = T.value_map(self.f.read_options())
        self.assertEqual(mapped['user-added'], 'keep')
        self.assertEqual(mapped['fixture-unrelated'], 'preserved')
        self.assertNotIn('blakestream-clock-scope', mapped)

    def test_post_rename_failure_restores(self):
        original = T.atomic_write
        fired = [False]
        def partial(path, data, *args):
            original(path, data, *args)
            if path == self.f.settings.options_path and not fired[0]:
                fired[0] = True
                raise IOError('fixture metadata failure after rename')
        T.atomic_write = partial
        try:
            self.assertNotEqual(self.f.runner.run(), 0)
        finally:
            T.atomic_write = original
        self.assertTrue(fired[0])
        self.assert_restored()

    def test_disk_full_logs_do_not_block_recovery(self):
        original = T.atomic_write
        def full(path, data, *args):
            if self.f.runner.mutated and path.startswith(self.f.settings.output_dir):
                raise IOError('fixture log partition full')
            return original(path, data, *args)
        T.atomic_write = full
        try:
            self.assertNotEqual(self.f.runner.run(), 0)
        finally:
            T.atomic_write = original
        self.assert_restored()

    def test_restart_outage_retried_only_during_settle(self):
        original = self.f.deps.restart
        def restart():
            original()
            self.f.outage = 2
        self.f.deps.restart = restart
        self.assertEqual(self.f.runner.run(), 0, self.f.deps.stderr.getvalue())
        self.assert_restored()

    def test_startup_temperature_waits_before_measurement(self):
        original = self.f.deps.api
        def starting(command):
            payload = original(command)
            if command == 'stats' and self.f.restarts and self.f.now - self.f.boot < 25:
                for row in payload['STATS']:
                    row['Temperature'] = 0
                    row['CommandVerified'] = False
                    row['ClockRetryPending'] = True
            return payload
        self.f.deps.api = starting
        self.assertEqual(self.f.runner.run(), 0, self.f.deps.stderr.getvalue())
        self.assert_restored()
        self.assertGreaterEqual(self.f.runner.legs_report[0]['monotonic_seconds'], 1800)

    def test_first_share_snapshot_waits_before_measurement(self):
        original = self.f.deps.api
        def starting(command):
            payload = original(command)
            if command == 'stats' and self.f.restarts and self.f.now - self.f.boot < 25:
                payload['STATS'][0]['DiagShareSnapValid'] = 0
            return payload
        self.f.deps.api = starting
        self.assertEqual(self.f.runner.run(), 0, self.f.deps.stderr.getvalue())
        self.assert_restored()
        self.assertGreaterEqual(self.f.runner.legs_report[0]['monotonic_seconds'], 1800)

    def test_real_startup_hardware_error_is_not_transient(self):
        self.f.stat_override['DiagHwErrors'] = 1
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assertIn('hw-error', self.f.runner.failure_reasons)
        self.assert_restored()

    def budget(self, seconds=1800):
        data = schedule()
        data['hw_policy'] = 'baseline-budget-v1'
        data['legs'][0]['measurement_seconds'] = seconds
        self.f.runner.schedule = T.validate_schedule(data)

    def test_budget_one_measured_error_is_reported(self):
        self.budget()
        self.f.hook = lambda f: f.stat_override.update({'DiagHwErrors': 1})
        self.assertEqual(self.f.runner.run(), 0)
        self.assert_restored()
        board = self.f.runner.legs_report[0]['boards'][1]
        self.assertEqual(board['startup_hw_errors'], 0)
        self.assertEqual(board['hw_errors_delta'], 1)

    def test_budget_two_errors_abort_half_hour(self):
        self.budget()
        self.f.hook = lambda f: f.stat_override.update({'DiagHwErrors': 2})
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assert_restored()
        self.assertIn('hw-budget', self.f.runner.failure_reasons)

    def test_budget_two_errors_in_full_hour_are_reported(self):
        self.budget(3600)
        self.f.hook = lambda f: f.stat_override.update({'DiagHwErrors': 2})
        self.assertEqual(self.f.runner.run(), 0)
        self.assert_restored()
        self.assertEqual(self.f.runner.legs_report[0]['boards'][1]['hw_errors_delta'], 2)

    def test_budget_three_errors_abort_full_hour(self):
        self.budget(3600)
        self.f.hook = lambda f: f.stat_override.update({'DiagHwErrors': 3})
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assert_restored()

    def test_budget_total_cap_and_startup_and_rejects(self):
        self.budget()
        parsed = T.parse_boards(self.f.api('devs'), self.f.api('stats'))
        expected = self.f.runner._expected([30] * 3, False, 'individual', 'measure')
        expected['temp_limit'] = 50
        expected['host_sha'] = SHA
        expected['measurement_seconds'] = 1800
        state = {'measure_first': copy.deepcopy(parsed['boards'])}
        for b in parsed['boards'].values():
            b['hw'] = 1
        self.assertIn('hw-budget-total', T.assess_boards(parsed, expected, state, 1000))
        expected['phase'] = 'warmup'
        self.assertIn('hw-error', T.assess_boards(parsed, expected, state, 1000))
        expected['phase'] = 'measure'
        parsed['boards'][0]['rejected'] = 1
        self.assertIn('nonzero-rejects', T.assess_boards(parsed, expected, state, 1000))

    def test_candidate_scope_is_individual_and_restore_is_exact(self):
        items = [
            {'key': 'baikal-options', 'value': '400:40:55'},
            {'key': 'baikal-clock-mode', 'value': 'individual'},
            {'key': 'blakestream-clock-scope', 'value': 'global',
             'description': 'Clock control scope (presentation only)'},
            {'key': 'fixture-unrelated', 'value': 'preserved'},
        ]
        self.assertTrue(T.backup_is_baseline(items))
        updated = T.apply_experimental(items, [22, 30, 30])
        mapped = T.value_map(updated)
        self.assertEqual(mapped['blakestream-clock-scope'], 'individual')
        self.assertEqual(mapped['baikal-board-clock-index'], '22:30:30')
        self.assertEqual(mapped['baikal-clock-mode'], 'individual')
        self.assertEqual(mapped['fixture-unrelated'], 'preserved')
        for item in updated:
            if item.get('key') == 'blakestream-clock-scope':
                self.assertEqual(item.get('description'), 'Clock control scope (presentation only)')
        runtime_path = os.path.join(os.path.dirname(ROOT), 'overlay/opt/scripta/bin/build-miner-runtime.py')
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location('build_miner_runtime_scope', runtime_path)
            runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runtime)
        except ImportError:
            import imp
            runtime = imp.load_source('build_miner_runtime_scope', runtime_path)
        accepted = runtime.load_saved_options(updated)
        missing = os.path.join(self.f.root, 'missing-clock-policy.json')
        self.assertEqual(runtime.apply_experimental_clock_index(
            accepted, 'individual', identity_paths=(missing,), scripta_conf=missing), 'omitted')
        self.assertNotIn('blakestream-clock-scope', accepted)
        broken = [
            {'key': 'baikal-clock-mode', 'value': 'individual'},
            {'key': 'baikal-experimental-clock-index', 'value': True},
            {'key': 'baikal-board-clock-index', 'value': '22:30:30'},
            {'key': 'blakestream-clock-scope', 'value': 'global'},
        ]
        with self.assertRaises(runtime.ExperimentalClockError):
            runtime.apply_experimental_clock_index(runtime.load_saved_options(broken), 'individual')

        current = T.apply_experimental(items, [22, 30, 30])
        current.append({'key': 'user-added', 'value': 'keep'})
        plan = T.plan_restore(current, items)
        self.assertEqual(plan['action'], 'merge')
        restored = T.value_map(plan['items'])
        self.assertEqual(restored['blakestream-clock-scope'], 'global')
        self.assertNotIn('baikal-experimental-clock-index', restored)
        self.assertNotIn('baikal-board-clock-index', restored)
        self.assertEqual(restored['user-added'], 'keep')
        self.assertEqual(restored['fixture-unrelated'], 'preserved')
        self.assertEqual(restored['baikal-options'], '400:40:55')

        absent = [
            {'key': 'baikal-options', 'value': '400:40:55'},
            {'key': 'baikal-clock-mode', 'value': 'global'},
            {'key': 'fixture-unrelated', 'value': 'preserved'},
        ]
        added = T.apply_experimental(absent, [8, 30, 32])
        added.append({'key': 'user-added', 'value': 'keep'})
        removed = T.value_map(T.plan_restore(added, absent)['items'])
        self.assertNotIn('blakestream-clock-scope', removed)
        self.assertNotIn('baikal-board-clock-index', removed)
        self.assertEqual(removed['baikal-clock-mode'], 'global')
        self.assertEqual(removed['user-added'], 'keep')
        self.assertEqual(removed['fixture-unrelated'], 'preserved')

    def test_run_restores_original_scope(self):
        items = self.f.read_options()
        items.append({'key': 'blakestream-clock-scope', 'value': 'global',
                      'description': 'Clock control scope (presentation only)'})
        self.f.save_options(items)
        with open(self.f.settings.options_path, 'rb') as handle:
            self.f.original = handle.read()

        def intervene(fixture):
            mapped = T.value_map(fixture.read_options())
            fixture.candidate_scope = mapped.get('blakestream-clock-scope')
            fixture.candidate_clock_index = mapped.get('baikal-board-clock-index')
            edited = fixture.read_options()
            edited.append({'key': 'user-added', 'value': 'keep'})
            fixture.save_options(edited)

        self.f.hook = intervene
        self.assertNotEqual(self.f.runner.run(), 0, self.f.deps.stderr.getvalue())
        self.assertIn('config-changed', self.f.runner.failure_reasons)
        self.assertEqual(self.f.candidate_scope, 'individual')
        self.assertEqual(self.f.candidate_clock_index, '22:30:30')
        self.assert_restored(exact=False)
        mapped = T.value_map(self.f.read_options())
        self.assertEqual(mapped['blakestream-clock-scope'], 'global')
        self.assertEqual(mapped['user-added'], 'keep')
        self.assertEqual(mapped['fixture-unrelated'], 'preserved')
        self.assertNotIn('baikal-experimental-clock-index', mapped)
        self.assertNotIn('baikal-board-clock-index', mapped)
        self.assertEqual(mapped['baikal-options'], '400:40:55')

    def logged_budget(self):
        data = schedule()
        data['hw_policy'] = 'logged-budget-10-v1'
        self.f.runner.schedule = T.validate_schedule(data)

    def test_logged_budget_nine_errors_include_startup_and_are_labeled(self):
        self.logged_budget()
        self.f.stat_override['DiagHwErrors'] = 4
        self.f.hook = lambda f: f.stat_override.update({'DiagHwErrors': 9})
        self.assertEqual(self.f.runner.run(), 0)
        self.assert_restored()
        report = self.f.runner.legs_report[0]
        board = report['boards'][1]
        self.assertEqual(board['startup_hw_errors'], 4)
        self.assertEqual(board['hw_errors_delta'], 5)
        self.assertEqual(board['leg_hw_errors'], 9)
        self.assertEqual(board['hw_status'], 'errors-observed')
        self.assertEqual(report['leg_hw_errors_total'], 9)
        self.assertEqual(report['quality_flags'], ['hardware-errors-observed'])
        with open(os.path.join(self.f.settings.output_dir, 'samples.jsonl')) as handle:
            samples = [json.loads(line) for line in handle]
        self.assertTrue(any(s['phase'] == 'warmup' and s['boards'][1]['hw'] == 4 for s in samples))
        self.assertTrue(any(s['phase'] == 'measure' and s['boards'][1]['hw'] == 9 for s in samples))

    def test_logged_budget_tenth_error_aborts_without_resetting_startup_budget(self):
        self.logged_budget()
        self.f.stat_override['DiagHwErrors'] = 9
        self.f.hook = lambda f: f.stat_override.update({'DiagHwErrors': 10})
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assertIn('hw-budget-total', self.f.runner.failure_reasons)
        self.assert_restored()

    def test_logged_budget_total_limit_all_phases(self):
        self.logged_budget()
        parsed = T.parse_boards(self.f.api('devs'), self.f.api('stats'))
        for phase in ('precheck', 'settle', 'warmup', 'measure', 'verify'):
            expected = self.f.runner._expected([30] * 3, False, 'individual', phase)
            expected['temp_limit'] = 50
            for board in parsed['boards'].values():
                board['hw'] = 3
            self.assertEqual(T.assess_boards(parsed, expected, {}, 1000), [], phase)
            parsed['boards'][0]['hw'] = 4
            self.assertIn('hw-budget-total', T.assess_boards(parsed, expected, {}, 1000), phase)

    def test_logged_budget_preserves_usb_reject_and_reset_guards(self):
        self.logged_budget()
        parsed = T.parse_boards(self.f.api('devs'), self.f.api('stats'))
        expected = self.f.runner._expected([30] * 3, False, 'individual', 'measure')
        expected['temp_limit'] = 50
        parsed['boards'][0]['hw'] = 1
        for field, value, reason in (('usb_rx', 1, 'usb-error'), ('rejected', 1, 'nonzero-rejects'),
                                     ('temp', 50, 'temperature'), ('chips', 63, 'startup-count'),
                                     ('hw', None, 'hw-error'), ('hw', -1, 'negative-counter')):
            changed = copy.deepcopy(parsed)
            changed['boards'][0][field] = value
            self.assertIn(reason, T.assess_boards(changed, expected, {}, 1000), field)
        previous = copy.deepcopy(parsed['boards'])
        previous[0]['hw'] = 2
        self.assertIn('counter-reset', T.assess_boards(parsed, expected,
                                                      {'locked': True, 'identity': parsed['boards'],
                                                       'previous': previous}, 1000))

    def test_logged_observe_is_opt_in_and_never_mutates(self):
        self.f.runner.schedule = None
        self.f.settings.output_dir = None
        original_api = self.f.deps.api

        def with_error(command):
            response = original_api(command)
            if command == 'stats':
                response['STATS'][0]['DiagHwErrors'] = 1
            return response

        self.f.deps.api = with_error
        self.assertEqual(self.f.runner.observe(), T.EXIT_FAILED)
        self.f.settings.hw_policy = 'logged-budget-10-v1'
        self.f.deps.stdout = StringIO()
        self.assertEqual(self.f.runner.observe(), T.EXIT_OK)
        report = json.loads(self.f.deps.stdout.getvalue())
        self.assertEqual(report['quality_flags'], ['hardware-errors-observed'])
        self.assertEqual(report['board_hw_errors'], [1, 0, 0])
        self.assertEqual(self.f.restarts, 0)
        self.assertEqual(self.f.read_options(), self.f.options)

    def test_logged_policy_cli_cannot_override_a_schedule(self):
        parser = T.build_parser()
        for command in ('observe', 'restore'):
            settings = T.settings_from_namespace(parser.parse_args([command, '--hw-policy', 'logged-budget-10-v1']))
            self.assertEqual(settings.hw_policy, 'logged-budget-10-v1')
        for command in ('run', 'validate'):
            with self.assertRaises(T.TrialError):
                T.settings_from_namespace(parser.parse_args([command, '--hw-policy', 'logged-budget-10-v1']))

    def test_logged_independent_restore_accepts_logged_errors_without_extra_restart(self):
        self.logged_budget()
        self.assertEqual(self.f.runner.run(), 0)
        original_api = self.f.deps.api

        def with_error(command):
            response = original_api(command)
            if command == 'stats':
                response['STATS'][0]['DiagHwErrors'] = 1
            return response

        self.f.deps.api = with_error
        self.f.settings.hw_policy = 'logged-budget-10-v1'
        restarts = self.f.restarts
        recovery = T.Runner(self.f.deps, self.f.settings)
        self.assertEqual(recovery.restore_command(), 0)
        self.assertTrue(recovery.restore_verified)
        self.assertEqual(self.f.restarts, restarts)
        with open(os.path.join(self.f.settings.output_dir, 'summary.json')) as handle:
            summary = json.load(handle)
        self.assertEqual(summary['hw_policy'], 'logged-budget-10-v1')

    def test_unknown_budget_rejected(self):
        data = schedule()
        data['hw_policy'] = 'ignore-errors'
        with self.assertRaises(T.TrialError):
            T.validate_schedule(data)

    def test_api_diagnostics_preserved_and_timeout_still_restores(self):
        original = self.f.deps.api
        fired = [False]
        details = {'command': 'stats', 'stage': 'read', 'detail': 'socket deadline exceeded',
                   'bytes': 0, 'elapsed_seconds': 4.0, 'limit_seconds': 4.0}

        def timeout_once(command):
            if self.f.runner.phase == 'measure' and command == 'stats' and not fired[0]:
                fired[0] = True
                exc = T.TrialAborted('api-timeout', 'fixture timeout')
                exc.api_details = details
                raise exc
            return original(command)

        self.f.deps.api = timeout_once
        self.assertEqual(self.f.runner.run(), T.EXIT_FAILED)
        self.assert_restored()
        self.assertEqual(self.f.runner.failure_reasons, ['api-timeout'])
        with open(os.path.join(self.f.settings.output_dir, 'events.jsonl')) as handle:
            events = [json.loads(line) for line in handle]
        errors = [row for row in events if row['kind'] == 'api-error']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['details'], details)
        self.assertIn('"stage":"read"', self.f.deps.stderr.getvalue())


class MonotonicFallbackTests(unittest.TestCase):
    def test_python2_clock_fallback_never_spawns_library_discovery(self):
        old_time, old_popen = T.time, T.subprocess.Popen
        self.addCleanup(setattr, T, 'time', old_time)
        self.addCleanup(setattr, T.subprocess, 'Popen', old_popen)
        spawned = []

        def deny_spawn(*args, **kwargs):
            spawned.append(args)
            raise AssertionError('clock read spawned a process')

        T.time = object()  # Exercise the controller's path without time.monotonic.
        T.subprocess.Popen = deny_spawn
        first = T.monotonic_seconds()
        second = T.monotonic_seconds()
        self.assertIsNotNone(first)
        self.assertGreaterEqual(second, first)
        self.assertEqual(spawned, [])


class ApiSocket(object):
    def __init__(self, owner):
        self.owner = owner
        self.closed = False
        self.timeout = None
        self.connect_delay = 0
        self.send_delay = 0
        self.reads = [(0, b'{"STATS":[]}\x00')]
        self.recv_count = 0
        self.sent = None

    def settimeout(self, value):
        self.timeout = value

    def delay(self, seconds):
        if seconds >= self.timeout:
            self.owner.now += self.timeout
            raise T.socket.timeout('fixture deadline')
        self.owner.now += seconds

    def connect(self, address):
        self.delay(self.connect_delay)

    def sendall(self, data):
        self.delay(self.send_delay)
        self.sent = data

    def shutdown(self, mode):
        pass

    def recv(self, size):
        self.recv_count += 1
        if not self.reads:
            raise AssertionError('read after complete frame')
        delay, data = self.reads.pop(0)
        self.delay(delay)
        if isinstance(data, Exception):
            raise data
        return data

    def close(self):
        self.closed = True


class ApiTransportTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.sock = ApiSocket(self)
        original = T.socket.socket
        T.socket.socket = lambda: self.sock
        self.addCleanup(setattr, T.socket, 'socket', original)

    def call(self, **kwargs):
        return T.call_api('stats', '127.0.0.1', 4028, clock=lambda: self.now, **kwargs)

    def failure(self, reason, stage, **kwargs):
        with self.assertRaises(T.TrialAborted) as caught:
            self.call(**kwargs)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(caught.exception.api_details['command'], 'stats')
        self.assertEqual(caught.exception.api_details['stage'], stage)
        self.assertTrue(self.sock.closed)
        return caught.exception.api_details

    def test_nul_frame_does_not_wait_for_close(self):
        self.assertEqual(self.call(), {'STATS': []})
        self.assertEqual(self.sock.recv_count, 1)
        self.assertTrue(self.sock.closed)
        self.assertEqual(self.sock.sent, b'{"command":"stats"}')

    def test_fragmented_body_and_separate_terminator(self):
        self.sock.reads = [(0, b'{"ST'), (0.1, b'ATS":[]}'), (0.1, b'\x00')]
        self.assertEqual(self.call(), {'STATS': []})

    def test_eof_compatibility(self):
        self.sock.reads = [(0, b'{"STATS":[]}'), (0, b'')]
        self.assertEqual(self.call(), {'STATS': []})

    def test_delayed_reply_within_existing_four_second_budget(self):
        self.sock.reads = [(2.5, b'{"STATS":[]}\x00')]
        self.assertEqual(self.call(), {'STATS': []})
        self.assertAlmostEqual(self.now, 1002.5)

    def test_trickles_do_not_extend_total_deadline(self):
        self.sock.reads = [(1.5, b'{'), (1.5, b'"STATS"'), (1.5, b':[]}\x00')]
        details = self.failure('api-timeout', 'read')
        self.assertEqual(details['elapsed_seconds'], 4.0)
        self.assertEqual(details['bytes'], 8)

    def test_connect_and_send_consume_the_same_budget(self):
        self.sock.connect_delay = 1.5
        self.sock.send_delay = 1.5
        self.sock.reads = [(1.5, b'{"STATS":[]}\x00')]
        details = self.failure('api-timeout', 'read')
        self.assertEqual(details['elapsed_seconds'], 4.0)
        self.assertEqual(details['bytes'], 0)

    def test_connect_timeout_identifies_stage(self):
        self.sock.connect_delay = 5
        self.failure('api-timeout', 'connect')

    def test_send_timeout_identifies_stage(self):
        self.sock.send_delay = 5
        self.failure('api-timeout', 'send')

    def test_socket_error_identifies_stage(self):
        self.sock.reads = [(0, T.socket.error('fixture reset'))]
        details = self.failure('api-timeout', 'read')
        self.assertEqual(details['detail'], 'socket operation failed')

    def test_size_cap_is_not_bypassed_by_terminator(self):
        details = self.failure('api-timeout', 'read', max_bytes=8)
        self.assertEqual(details['detail'], 'response exceeded size cap')

    def test_terminator_does_not_accept_invalid_json(self):
        self.sock.reads = [(0, b'{"STATS":\x00')]
        self.failure('api-invalid', 'parse')

    def test_extra_payload_after_terminator_is_rejected(self):
        self.sock.reads = [(0, b'{"STATS":[]}\x00{"extra":1}')]
        self.failure('api-invalid', 'read')

    def test_non_object_json_is_rejected(self):
        self.sock.reads = [(0, b'[]\x00')]
        self.failure('api-invalid', 'parse')

    def test_unavailable_clock_closes_socket(self):
        with self.assertRaises(T.TrialAborted):
            T.call_api('stats', '127.0.0.1', 4028, clock=lambda: None)
        self.assertTrue(self.sock.closed)

    def test_non_read_command_refused(self):
        with self.assertRaises(T.TrialAborted) as caught:
            T.call_api('restart', '127.0.0.1', 4028)
        self.assertEqual(caught.exception.reason, 'api-command')
        self.assertIsNone(self.sock.sent)


def fault_test(field, value):
    def run(self):
        self.f.hook = lambda f: f.stat_override.update({field: value})
        self.assertNotEqual(self.f.runner.run(), 0, field)
        self.assert_restored()
        self.assertEqual(self.f.runner.legs_report[0]['status'], 'failed')
    return run


for _field, _value in (
    ('Temperature', 50), ('Temperature', None), ('StartupSuccessCount', 56),
    ('CommandVerified', False), ('SentClockIndex', 8), ('RequestedClockIndex', 8),
    ('ExperimentalClock', False), ('DiagPid', 999), ('DiagStartUnix', 0),
    ('ClockGeneration', 99), ('DiagStatEpoch', 99), ('DiagRouteEpoch', 99),
    ('DiagEnableEpoch', 99), ('DiagHostSha256', 'b' * 64), ('Effective Pool', 9),
    ('Failover Active', True), ('ClockRetryPending', True), ('DiagAccepted', -1),
    ('DiagDeviceElapsed', 0), ('DiagShareSnapValid', 0), ('DiagHwErrors', 1),
    ('DiagUsbTxErrors', 1), ('DiagUsbRxErrors', 1), ('DiagInvalidId', 1),
    ('DiagRejected', 100), ('DiagDiffAccepted', -1),
):
    setattr(TrialTests, 'test_fault_%s_%s' % (_field.replace(' ', '_'), str(_value).replace('.', '_')),
            fault_test(_field, _value))


def cancel_test(reason):
    def run(self):
        def stop(f):
            if reason == 'signal':
                f.runner.request_stop(signal.SIGTERM)
            elif reason == 'file':
                with open(f.settings.stop_file, 'w') as handle:
                    handle.write('stop')
            elif reason == 'cap':
                f.runner.max_samples = 1
            else:
                f.runner.started_mono -= 100000
        self.f.hook = stop
        self.assertNotEqual(self.f.runner.run(), 0)
        self.assert_restored()
    return run


for _reason in ('signal', 'file', 'cap', 'deadline'):
    setattr(TrialTests, 'test_cancel_' + _reason, cancel_test(_reason))


if __name__ == '__main__':
    unittest.main(verbosity=2)
