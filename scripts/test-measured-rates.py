#!/usr/bin/env python
from __future__ import division
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'overlay', 'opt', 'scripta', 'bin'))
import measured_rates as m

META = {'generation': 'g1', 'salt': 'private-local-salt', 'board_ids': {'0': 'b0', '1': 'b1', '2': 'b2'}}


def api(at, accepted=None):
    if accepted is None:
        accepted = at // 60 * 30
    stats, devs = [], []
    for bid in range(3):
        stats.append({'ID': 'BKLU%d' % bid, 'DiagPid': 123, 'DiagStartUnix': 1000,
            'DiagHostSha256': 'a' * 64, 'DiagStatEpoch': 1, 'DiagRouteEpoch': 2,
            'DiagEnableEpoch': 3, 'ClockGeneration': 4, 'SentClockIndex': 30,
            'ExperimentalClock': False, 'RequestedClockIndex': 0, 'RequestedClock': 400, 'StartupSuccessCount': 64,
            'Chip Count': 64, 'ClockMode': 'individual', 'Algo': 'blake256r8',
            'DiagShareSnapValid': 1, 'DiagDeviceElapsedValid': 1, 'CommandVerified': True,
            'DiagDeviceElapsed': at, 'DiagSnapMonoUs': at * 1000000,
            'DiagAccepted': accepted, 'DiagDiffAccepted': accepted * 32,
            'DiagRejected': 0, 'DiagHwErrors': 0, 'FanPercent': 70})
        devs.append({'ASC': bid, 'Enabled': 'Y', 'Status': 'Alive', 'Effective Pool': 0, 'Temperature': 40})
    return {'STATS': stats}, {'DEVS': devs}, {'POOLS': [
        {'POOL': 0, 'URL': 'stratum+tcp://pool.example:3334', 'User': 'private-worker'}]}


def build(at, accepted=None, mutate=None, firmware=None, route_epoch=2,
          confirmed=True, indices=(30, 30, 30)):
    stats, devs, pools = api(at, accepted)
    for bid, row in enumerate(stats['STATS']):
        row['DiagRouteEpoch'] = route_epoch
        row['CommandVerified'] = confirmed
        row['SentClockIndex'] = indices[bid]
    before = copy.deepcopy(stats)
    if mutate:
        mutate(stats, devs, pools)
    return m.samples_from_api(before, stats, devs, pools, {}, [],
        {'chip_md5': 'b' * 32, 'at': 500} if firmware is None else firmware,
        {}, META, 1700000000 + at, 'boot-a')


class RatesTests(unittest.TestCase):
    def warm(self):
        runtime = {}
        for at in range(1000, 5261, 60):
            m.reduce_samples(META, runtime, build(at))
        return runtime

    def restarted(self, at=5380, firmware=None, confirmed=True, indices=(30, 30, 30)):
        samples = build(at, accepted=10 + (at - 5380) // 2, firmware=firmware,
                        confirmed=confirmed, indices=indices)
        for sample in samples.values():
            sample['de'] = at - 5360
            sample['live_context'].update(pid=456, start=1700005360, ds=0, cg=1, re=0, ee=0)
            sample['segment'] = m.digest([sample['live_context'], sample['boot_id']])
        return samples

    def test_firmware_restart_retains_live_samples_not_counter_delta_or_history(self):
        for confirmed, indices in ((True, (30, 30, 30)), (False, (0, 0, 0))):
            runtime = self.warm()
            old = copy.deepcopy(runtime['boards']['0']['window'])
            current = self.restarted(firmware={'chip_md5': 'c' * 32, 'at': 1700005300},
                                     confirmed=confirmed, indices=indices)
            self.assertFalse(m.reduce_samples(META, runtime, current))
            state = runtime['boards']['0']
            self.assertEqual(state['window'], [v for v in old if 5380 - v['end'] < 3600])
            self.assertEqual(state['anchor']['a'], 10)
            summary = m.public_summary({'runtime': runtime}, 1700005380)
            for board in summary['boards'].values():
                self.assertTrue(board['15m']['valid'])
                self.assertTrue(board['1h']['valid'])
                self.assertFalse(board['1h']['full_window'], 'restart gaps are not full coverage')
                self.assertEqual(board['context']['pid'], 456)
                self.assertEqual(board['context']['cv'], int(confirmed))
                self.assertIsNone(board['personal'])
            for at in (5440, 5500, 5560):
                samples = self.restarted(at, firmware={'chip_md5': 'c' * 32, 'at': 1700005300},
                                         confirmed=confirmed, indices=indices)
                records = m.reduce_samples(META, runtime, samples)
                if at < 5560 or not confirmed:
                    self.assertFalse(records)
                else:
                    self.assertEqual(len(records), 3)
                    self.assertTrue(all(r['seconds'] == 60 and r['accepted'] == 30 for r in records))
            self.assertEqual(runtime['boards']['0']['window'][-1]['accepted'], 30)
            self.assertEqual(runtime['boards']['0']['window'][-1]['end'], 5560)

    def test_restart_outage_retained_but_not_published_until_valid_snapshot(self):
        runtime = self.warm()
        for at in (5320, 5380, 5440, 5500):
            missing = dict((str(bid), {'t': 1700000000 + at, 'error': 'telemetry-unavailable'})
                           for bid in range(3))
            self.assertFalse(m.reduce_samples(META, runtime, missing))
            self.assertTrue(runtime['boards']['0']['window'])
            summary = m.public_summary({'runtime': runtime}, 1700000000 + at)
            self.assertFalse(summary['boards']['0']['15m']['valid'])
        self.assertFalse(m.reduce_samples(META, runtime, self.restarted(5560)))
        self.assertTrue(m.public_summary({'runtime': runtime}, 1700005560)['boards']['0']['15m']['valid'])

    def test_restart_guards_clock_algorithm_board_pool_host_boot_and_time(self):
        for key in ('target_index', 'algorithm', 'installation', 'startup', 'total', 'route', 'host', 'mapping'):
            runtime = self.warm()
            samples = self.restarted()
            value = samples['0']['live_profile'][key]
            samples['0']['live_profile'][key] = value + 1 if isinstance(value, int) else value + '-changed'
            m.reduce_samples(META, runtime, samples)
            self.assertFalse(runtime['boards']['0']['window'], key)
            self.assertTrue(runtime['boards']['1']['window'], 'other boards remain independent')
        for key, value in (('boot_id', 'new-boot'), ('t', 1700005200), ('mono', 6000)):
            runtime = self.warm()
            samples = self.restarted()
            samples['0'][key] = value
            m.reduce_samples(META, runtime, samples)
            self.assertFalse(runtime['boards']['0']['window'], key)
        for field, value in (('scc', 8), ('start', 999)):
            runtime = self.warm()
            samples = self.restarted()
            samples['0']['live_context'][field] = value
            m.reduce_samples(META, runtime, samples)
            self.assertFalse(runtime['boards']['0']['window'], field)
        runtime = self.warm()
        m.reduce_samples(META, runtime, self.restarted(6220))
        self.assertFalse(runtime['boards']['0']['window'], 'long downtime ages out continuity')

    def test_same_process_counter_or_epoch_reset_still_resets_live_window(self):
        for key in ('ds', 'cg', 'ee'):
            runtime = self.warm()
            samples = build(5320, firmware={'chip_md5': 'c' * 32, 'at': 500})
            samples['0']['live_context'][key] += 1
            samples['0']['segment'] = m.digest([samples['0']['live_context'], 'boot-a'])
            m.reduce_samples(META, runtime, samples)
            self.assertFalse(runtime['boards']['0']['window'], key)
        runtime = self.warm()
        m.reduce_samples(META, runtime, build(5320, accepted=1, firmware={}))
        self.assertFalse(runtime['boards']['0']['window'])

    def test_normal_400_and_explicit_index_30_share_live_profile_only(self):
        stats, devs, pools = api(1000)
        normal = m.samples_from_api(stats, stats, devs, pools, {}, [], {}, {}, META, 1700001000, 'boot-a')
        for row in stats['STATS']:
            row.update(ExperimentalClock=True, RequestedClockIndex=30)
        custom = m.samples_from_api(stats, stats, devs, pools, {'userAutoFan': True}, [], {}, {}, META, 1700001000, 'boot-a')
        self.assertEqual(normal['0']['live_profile'], custom['0']['live_profile'])
        self.assertNotEqual(normal['0']['context_id'], custom['0']['context_id'])
        for row in stats['STATS']:
            row['RequestedClockIndex'] = 8
        lower = m.samples_from_api(stats, stats, devs, pools, {}, [], {}, {}, META, 1700001000, 'boot-a')
        self.assertNotEqual(normal['0']['live_profile'], lower['0']['live_profile'])
        runtime = self.warm()
        changed = build(5320)
        changed['0']['live_profile']['target_index'] = 8
        m.reduce_samples(META, runtime, changed)
        self.assertFalse(runtime['boards']['0']['window'])

    def test_old_runtime_upgrade_does_not_reset_and_ages_by_monotonic_clock(self):
        runtime = self.warm()
        state = runtime['boards']['0']
        for item in state['window']:
            item['de'] = item.pop('end')
        del state['anchor']['live_profile']
        expected = m.rate(state['window'], 900)['ghs']
        m.reduce_samples(META, runtime, build(5320))
        summary = m.public_summary({'runtime': runtime}, 1700005320)
        self.assertAlmostEqual(summary['boards']['0']['15m']['ghs'], expected)
        self.assertTrue(all('end' in item and 'de' not in item for item in runtime['boards']['0']['window']))

    def test_retained_intervals_age_out_by_actual_time_not_new_miner_uptime(self):
        runtime = self.warm()
        m.reduce_samples(META, runtime, self.restarted())
        window = runtime['boards']['0']['window']
        self.assertTrue(m.rate(window, 900, now=5380)['valid'])
        self.assertFalse(m.rate(window, 900, now=6220)['valid'])
        self.assertTrue(m.rate(window, 3600, now=6220)['valid'])
        self.assertFalse(m.rate(window, 3600, now=8860)['valid'])

    def test_failed_clock_ack_allows_live_rates_but_never_verified_history(self):
        for firmware in ({}, {'chip_md5': 'b' * 32, 'at': 500}):
            for indices in ((0, 30, 30), (30, 30, 30)):
                runtime, records = {}, []
                for at in range(1000, 2021, 60):
                    samples = build(at, firmware=firmware, confirmed=False, indices=indices)
                    for sample in samples.values():
                        self.assertNotIn('error', sample)
                        self.assertFalse(sample['history_verified'])
                        self.assertEqual(sample['live_context']['cv'], 0)
                    records.extend(m.reduce_samples(META, runtime, samples))
                self.assertEqual(records, [])
                summary = m.public_summary({'runtime': runtime, 'contexts': []}, 1700002020)
                for board in summary['boards'].values():
                    self.assertTrue(board['15m']['valid'])
                    self.assertTrue(board['1h']['valid'])
                    self.assertIsNone(board['personal'])
                    self.assertAlmostEqual(board['15m']['ghs'], 30 * 32 * 4.294967296 / 60)
                if indices[0] == 0:
                    self.assertIsNone(samples['0']['context']['index'])

    def test_unknown_clock_index_never_qualifies_for_history(self):
        samples = build(1000, indices=(0, 30, 30))
        self.assertTrue(all(not s['history_verified'] for s in samples.values()))
        for invalid in (1, 7, 33, -1, None, True):
            samples = build(1000, indices=(invalid, 30, 30))
            self.assertTrue(all('error' in s for s in samples.values()))

    def test_clock_verification_transition_does_not_backfill_history(self):
        runtime = {}
        for at in range(1000, 2021, 60):
            self.assertFalse(m.reduce_samples(META, runtime, build(at, confirmed=False)))
        for at in (2080, 2140, 2200):
            self.assertFalse(m.reduce_samples(META, runtime, build(at)))
        records = m.reduce_samples(META, runtime, build(2260))
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r['seconds'] == 60 for r in records))
        self.assertFalse(m.reduce_samples(META, runtime, build(2320, confirmed=False)))
        self.assertTrue(m.rate(runtime['boards']['0']['window'], 900)['valid'])

    def test_unconfirmed_clock_still_rejects_pending_and_bad_snapshots(self):
        for field in m.PENDING:
            def pending(stats, devs, pools):
                stats['STATS'][0][field] = True
            self.assertEqual(build(1000, confirmed=False, mutate=pending)['0']['error'], 'command-pending')
        for field in ('DiagShareSnapValid', 'DiagDeviceElapsedValid'):
            def invalid(stats, devs, pools):
                stats['STATS'][0][field] = 0
            self.assertIn('error', build(1000, confirmed=False, mutate=invalid)['0'])
        for confirmed in (None, 'unknown', 2):
            self.assertIn('error', build(1000, confirmed=confirmed)['0'])

    def test_same_pool_reconnect_preserves_live_hour_but_restarts_verified_settle(self):
        runtime = {}
        for at in range(1000, 5261, 60):
            m.reduce_samples(META, runtime, build(at))
        self.assertTrue(m.rate(runtime['boards']['0']['window'], 3600)['full_window'])
        records = m.reduce_samples(META, runtime, build(5320, route_epoch=4))
        self.assertEqual(records, [])
        summary = m.public_summary({'runtime': runtime}, 1700005320)
        for board in summary['boards'].values():
            self.assertTrue(board['15m']['valid'])
            self.assertTrue(board['1h']['full_window'])
            self.assertEqual(board['context']['re'], 4)
        for at in (5380, 5440):
            self.assertEqual(m.reduce_samples(META, runtime, build(at, route_epoch=4)), [])
        records = m.reduce_samples(META, runtime, build(5500, route_epoch=4))
        self.assertEqual(len(records), 3)
        self.assertTrue(all(record['seconds'] == 60 for record in records))

    def test_short_outage_retains_window_without_publishing_unavailable_samples(self):
        runtime = {}
        for at in range(1000, 5261, 60):
            m.reduce_samples(META, runtime, build(at))
        missing = dict((str(bid), {'t': 1700005320, 'error': 'telemetry-unavailable'})
                       for bid in range(3))
        self.assertEqual(m.reduce_samples(META, runtime, missing), [])
        self.assertTrue(runtime['boards']['0']['window'])
        summary = m.public_summary({'runtime': runtime}, 1700005320)
        self.assertFalse(summary['boards']['0']['15m']['valid'])
        self.assertFalse(summary['boards']['0']['context'])
        records = m.reduce_samples(META, runtime, build(5380.5, route_epoch=4))
        self.assertEqual(records, [], 'an unobserved interval must not enter verified history')
        summary = m.public_summary({'runtime': runtime}, 1700005380.5)
        self.assertTrue(summary['boards']['0']['15m']['valid'])
        self.assertTrue(summary['boards']['0']['1h']['full_window'])
        expected = build(5380.5)['0']['da'] - build(5260)['0']['da']
        self.assertEqual(runtime['boards']['0']['window'][-1]['difficulty'], expected)
        m.reduce_samples(META, runtime, build(5560, route_epoch=4))
        self.assertFalse(m.rate(runtime['boards']['0']['window'], 900)['valid'])

    def test_reconnect_keeps_live_identity_time_and_counter_guards(self):
        previous = build(1000)['0']
        current = build(1060, route_epoch=4)['0']
        self.assertIsNone(m.interval(previous, current))
        self.assertIsNotNone(m.interval(previous, current, live=True))
        for field in previous['live_context']:
            changed = copy.deepcopy(current)
            value = changed['live_context'][field]
            changed['live_context'][field] = value + 1 if isinstance(value, (int, float)) else value + '-changed'
            if field == 're':
                changed['live_context'][field] = 1
            changed['segment'] = m.digest([changed['live_context'], changed['boot_id']])
            self.assertIsNone(m.interval(previous, changed, live=True), field)
        changed = copy.deepcopy(current)
        changed['boot_id'] = 'other-boot'
        self.assertIsNone(m.interval(previous, changed, live=True))
        for field, value in (('context_id', 'other-pool-or-worker'), ('a', 0), ('da', 0),
                             ('de', 999), ('mono', 999), ('t', 1700000999)):
            changed = dict(current)
            changed[field] = value
            self.assertIsNone(m.interval(previous, changed, live=True), field)

    def test_full_window_accepts_jitter_without_promoting_early(self):
        runtime = {}
        at = 1000.0
        for index in range(75):
            m.reduce_samples(META, runtime, build(at, accepted=index * 30))
            window = runtime['boards']['0']['window']
            hour = m.rate(window, 3600)
            observed = sum(row['seconds'] for row in window)
            self.assertEqual(hour['full_window'], observed >= 3600)
            at += 60.1
        self.assertTrue(hour['valid'])
        self.assertTrue(hour['full_window'])
        self.assertLess(hour['seconds'], 3600)
        stale = m.public_summary({'runtime': runtime}, 1700000000 + at + m.MAX_GAP)
        self.assertFalse(stale['boards']['0']['1h']['full_window'])
        m.reduce_samples(META, runtime, build(at + m.MAX_GAP + 1, accepted=2500))
        self.assertFalse(m.rate(runtime['boards']['0']['window'], 3600)['full_window'])

    def test_full_window_zero_work_is_not_an_estimate(self):
        interval = {'seconds': 60, 'difficulty': 0, 'accepted': 0}
        partial = m.rate([interval] * 5, 3600)
        self.assertTrue(partial['valid'])
        self.assertFalse(partial['full_window'])
        full = m.rate([interval] * 60, 3600)
        self.assertTrue(full['full_window'])
        self.assertEqual(full['ghs'], 0)

    def test_identity_and_privacy(self):
        s = build(1000)['0']
        self.assertNotIn('error', s)
        self.assertNotIn('private-worker', m.canonical(s))
        self.assertNotIn('private-local-salt', m.canonical(s))
        self.assertEqual(s['a'], 480)

    def test_missing_bridge_identity_allows_live_rates_but_never_history(self):
        for firmware in ({}, {'chip_md5': 'bad', 'at': 500}, {'chip_md5': 'b' * 32}):
            runtime, records = {}, []
            for at in range(1000, 2021, 60):
                samples = build(at, firmware=firmware)
                self.assertFalse(samples['0']['history_verified'])
                records.extend(m.reduce_samples(META, runtime, samples))
            self.assertEqual(records, [])
            summary = m.public_summary({'runtime': runtime, 'contexts': []}, 1700002020)
            for board in summary['boards'].values():
                self.assertTrue(board['15m']['valid'])
                self.assertTrue(board['1h']['valid'])
                self.assertEqual(board['15m']['seconds'], 900)
                self.assertIsNone(board['personal'])
            expected = m.rate(runtime['boards']['0']['window'], 900)['ghs']
            verified = {}
            for at in range(1000, 2021, 60):
                m.reduce_samples(META, verified, build(at))
            self.assertEqual(expected, m.rate(verified['boards']['0']['window'], 900)['ghs'])

    def test_unverified_live_rates_keep_snapshot_and_window_guards(self):
        for field in ('DiagShareSnapValid', 'CommandVerified', 'DiagHostSha256'):
            def remove(stats, devs, pools):
                del stats['STATS'][0][field]
            self.assertIn('error', build(1000, mutate=remove, firmware={})['0'])
        runtime = {}
        for at in range(1000, 1421, 60):
            m.reduce_samples(META, runtime, build(at, firmware={}))
        self.assertTrue(m.rate(runtime['boards']['0']['window'], 900)['valid'])
        m.reduce_samples(META, runtime, build(1600, firmware={}))
        self.assertFalse(m.rate(runtime['boards']['0']['window'], 900)['valid'])

    def test_verification_starts_new_history_without_backfilling_live_window(self):
        runtime = {}
        for at in range(1000, 2021, 60):
            self.assertFalse(m.reduce_samples(META, runtime, build(at, firmware={})))
        for at in (2080, 2140, 2200):
            self.assertFalse(m.reduce_samples(META, runtime, build(at)))
        records = m.reduce_samples(META, runtime, build(2260))
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r['seconds'] == 60 for r in records))
        self.assertTrue(all(r['context']['bridge'] == 'b' * 32 for r in records))
        self.assertFalse(m.reduce_samples(META, runtime, build(2320, firmware={})))

    def test_summary_retains_missing_telemetry_reason(self):
        runtime = {'boards': {'0': {'reason': 'unconfirmed-snapshot', 'window': []}}}
        summary = m.public_summary({'runtime': runtime}, 1700001000)
        self.assertEqual(summary['boards']['0']['15m']['reason'], 'unconfirmed-snapshot')

    def test_missing_snapshot_is_not_zero(self):
        for field in ('DiagShareSnapValid', 'DiagDeviceElapsedValid', 'DiagAccepted', 'DiagDiffAccepted'):
            def remove(stats, devs, pools):
                del stats['STATS'][0][field]
            self.assertIn('error', build(1000, mutate=remove)['0'])

    def test_malformed_api_is_unavailable(self):
        for value in ([], 'bad', {'STATS': None}, {'STATS': [None, []]}):
            result = m.samples_from_api(value, value, value, value, {}, [], {}, {}, META, 1000, 'boot')
            self.assertTrue(all('error' in item for item in result.values()))

    def test_snapshot_race(self):
        for field in m.IDENTITY:
            def change(stats, devs, pools):
                stats['STATS'][0][field] = 'changed'
            self.assertIn('error', build(1000, mutate=change)['0'])

    def test_context_boundaries_and_worker(self):
        a = build(1000)['0']
        def worker(stats, devs, pools):
            pools['POOLS'][0]['User'] = 'other-worker'
        b = build(1060, mutate=worker)['0']
        self.assertNotEqual(a['context_id'], b['context_id'])
        self.assertIsNone(m.interval(a, b))
        b = build(1060)['0']
        for key in ('segment', 'context_id'):
            c = copy.deepcopy(b)
            c[key] = 'changed'
            self.assertIsNone(m.interval(a, c))

    def test_cooling_policy_boundary_not_actual_fan_motion(self):
        stats, devs, pools = api(1000)
        def sample(cfg):
            return m.samples_from_api(stats, stats, devs, pools, cfg, [],
                {'chip_md5': 'b' * 32, 'at': 500}, {}, META, 1700001000, 'boot')['0']
        first = sample({})
        for field in ('userAutoFanEmergency', 'userAutoFanStep', 'userAutoFanMaxDownStep'):
            self.assertNotEqual(first['context_id'], sample({field: 10})['context_id'])
        stats['STATS'][0]['FanPercent'] = 85
        self.assertEqual(first['context_id'], sample({})['context_id'])

    def test_time_and_counter_boundaries(self):
        a, b = build(1000)['0'], build(1060)['0']
        self.assertAlmostEqual(m.interval(a, b)['seconds'], 60)
        for key, value in (('t', a['t'] - 1), ('mono', a['mono'] - 1),
                           ('de', a['de'] + 500), ('a', a['a'] - 1),
                           ('da', a['da'] - 1), ('hw', -1), ('r', -1)):
            c = dict(b)
            c[key] = value
            self.assertIsNone(m.interval(a, c), key)
        self.assertIsNone(m.interval(a, build(1181)['0']))

    def test_settle_disjoint_zero_work_errors(self):
        runtime, records = {}, []
        for at in range(1000, 2021, 60):
            records.extend(m.reduce_samples(META, runtime, build(at, accepted=100)))
        self.assertEqual(len(records), 15 * 3)
        r = m.rate(runtime['boards']['0']['window'], 900)
        self.assertTrue(r['valid'])
        self.assertEqual(r['seconds'], 900)
        self.assertEqual(r['ghs'], 0)
        a, b = build(1000)['0'], build(1060)['0']
        b['hw'], b['r'] = 3, 2
        result = m.interval(a, b)
        self.assertEqual((result['hw'], result['rejected']), (3, 2))

    def test_gap_resets_without_backfill(self):
        runtime = {}
        for at in range(1000, 2021, 60):
            m.reduce_samples(META, runtime, build(at))
        m.reduce_samples(META, runtime, {'0': {'error': 'missing'}})
        self.assertEqual(runtime['boards']['0']['window'], [])
        self.assertEqual(m.reduce_samples(META, runtime, build(2080)), [])

    def test_weighted_rate_and_partial_coverage(self):
        w = [{'seconds': 60, 'difficulty': 600, 'accepted': 20},
             {'seconds': 120, 'difficulty': 2400, 'accepted': 30},
             {'seconds': 120, 'difficulty': 0, 'accepted': 0}]
        r = m.rate(w, 3600)
        self.assertTrue(r['valid'])
        self.assertEqual(r['seconds'], 300)
        self.assertAlmostEqual(r['coverage'], 1 / 12)
        self.assertAlmostEqual(r['ghs'], 3000 * 4294967296 / 300 / 1e9)

    def test_learning_threshold_and_no_runtime_export(self):
        runtime = {}
        s = build(1000)
        m.reduce_samples(META, runtime, s)
        c = {'context_id': s['0']['context_id'], 'seconds': 21600,
             'accepted': 10000, 'difficulty': 320000, 'fixed32': True}
        status = {'runtime': runtime, 'contexts': [c]}
        summary = m.public_summary(status, 1700001000)
        self.assertNotIn('runtime', summary)
        self.assertTrue(summary['boards']['0']['personal']['qualified'])
        c['fixed32'] = False
        self.assertFalse(m.public_summary(status, 1700001000)['boards']['0']['personal']['qualified'])
        c['fixed32'] = True
        status['error'] = 'SD write failed'
        self.assertFalse(m.public_summary(status, 1700001000)['boards']['0']['personal']['qualified'])


if __name__ == '__main__':
    unittest.main()
