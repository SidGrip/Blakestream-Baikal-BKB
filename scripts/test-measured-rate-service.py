#!/usr/bin/env python
from __future__ import division
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measured_rate_service import Service, read_json
from measured_rate_store import StoreError
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location('rate_fixtures', os.path.join(ROOT, 'scripts', 'test-measured-rates.py'))
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
except ImportError:
    import imp
    fixtures = imp.load_source('rate_fixtures', os.path.join(ROOT, 'scripts', 'test-measured-rates.py'))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bkb-measured-service-')
        self.service = Service(self.root)
        os.makedirs(self.service.etc)
        os.makedirs(os.path.dirname(self.service.boot_path))
        with open(self.service.boot_path, 'w') as f:
            f.write('boot-a')
        with open(self.service.etc + '/bkb-firmware-identity.json', 'w') as f:
            json.dump({'chip_md5': 'b' * 32, 'at': 500}, f)

    def tearDown(self):
        shutil.rmtree(self.root)

    def collect(self, at, force=False):
        stats, devs, pools = fixtures.api(at)
        return self.service.record(stats, stats, devs, pools, {}, [], 1700000000 + at, force=force)

    def test_collection_migration_clear_and_board_reset(self):
        for at in range(1000, 1901, 60):
            result = self.collect(at)
        self.assertEqual(result['target'], 'RAM')
        self.assertEqual(len(result['contexts']), 3)
        self.assertTrue(result['boards']['0']['15m']['valid'])
        self.assertLess(result['boards']['0']['1h']['seconds'], 3600)
        before = result['contexts'][0]['difficulty']
        moved = self.service.command('target', 'SD', now=1700001900)
        self.assertEqual(moved['target'], 'SD')
        self.assertEqual(moved['contexts'][0]['difficulty'], before)
        moved = self.service.command('target', 'RAM', now=1700001900)
        self.assertEqual(moved['target'], 'RAM')
        self.assertEqual(moved['contexts'][0]['difficulty'], before)
        reset = self.service.command('reset-board', '0', now=1700001900)
        self.assertEqual(len(reset['contexts']), 2)
        self.assertFalse(reset['boards'].get('0', {}).get('15m', {}).get('valid', False))
        self.collect(1960)
        cleared = self.service.command('clear', now=1700001960)
        self.assertFalse(cleared['contexts'])
        self.assertFalse(cleared['boards'])
        result = self.collect(2020)
        self.assertFalse(result['contexts'], 'pre-clear counters cannot backfill cleared history')
        self.assertFalse(result['boards']['1']['15m']['valid'])

    def test_fresh_image_live_rates_without_verification_or_stored_history(self):
        os.unlink(self.service.etc + '/bkb-firmware-identity.json')
        for at in range(1000, 1901, 60):
            result = self.collect(at, force=True)
        for board in result['boards'].values():
            self.assertTrue(board['15m']['valid'])
            self.assertTrue(board['1h']['valid'])
            self.assertIsNone(board['personal'])
        self.assertFalse(result['contexts'])
        self.assertEqual(result['pending_intervals'], 0)
        cache = read_json(self.service.rates, {})
        self.assertTrue(cache['boards']['0']['15m']['valid'])
        self.assertTrue(cache['boards']['0']['context']['pid'])
        moved = self.service.command('target', 'SD', now=1700001900)
        self.assertFalse(moved['contexts'])
        self.assertTrue(moved['boards']['0']['15m']['valid'])
        reset = self.service.command('reset-board', '0', now=1700001900)
        self.assertFalse(reset['boards'].get('0', {}).get('15m', {}).get('valid', False))
        self.assertTrue(reset['boards']['1']['15m']['valid'])
        self.assertFalse(os.path.exists(self.service.etc + '/bkb-firmware-identity.json'))

    def test_failed_global_clock_keeps_live_rates_on_ram_and_sd(self):
        for target in ('RAM', 'SD'):
            self.service.command('clear', now=1700000999)
            self.service.command('target', target, now=1700000999)
            for at in range(1000, 2021, 60):
                stats, devs, pools = fixtures.api(at)
                for bid, row in enumerate(stats['STATS']):
                    row.update(CommandVerified=False, ClockMode='global',
                               SentClockIndex=0 if bid == 0 else 30,
                               StartupSuccessCount=56 if bid == 0 else 64)
                result = Service(self.root).record(stats, stats, devs, pools,
                    {}, [], 1700000000 + at, force=True)
            refreshed = Service(self.root).command('status', now=1700002020)
            self.assertEqual(refreshed['target'], target)
            self.assertFalse(refreshed['contexts'])
            self.assertEqual(refreshed['pending_intervals'], 0)
            cache = read_json(self.service.rates, {})
            for bid in ('0', '1', '2'):
                self.assertTrue(refreshed['boards'][bid]['15m']['valid'])
                self.assertTrue(cache['boards'][bid]['15m']['valid'])
                self.assertIsNone(refreshed['boards'][bid]['personal'])
                self.assertEqual(cache['boards'][bid]['context']['cv'], 0)
            self.assertEqual(cache['boards']['0']['context']['scc'], 0)
            self.assertEqual(cache['boards']['0']['context']['startup'], 56)

    def test_ram_clear_flushes_sd_history_but_resets_windows(self):
        self.service.command('target', 'SD', now=1700000999)
        for at in range(1000, 1601, 60):
            old = self.collect(at)
        result = self.service.command('clear-side', 'RAM', now=1700001600)
        self.assertEqual(len(result['contexts']), 3)
        self.assertFalse(result['boards'])
        self.assertFalse(result['unsaved'])
        self.assertEqual(sum(c['difficulty'] for c in result['contexts']),
                         sum(c['difficulty'] for c in old['contexts']))

    def test_firmware_restart_preserves_live_rates_on_ram_and_sd(self):
        for target in ('RAM', 'SD'):
            self.service.command('clear', now=1700000999)
            self.service.command('target', target, now=1700000999)
            with open(self.service.etc + '/bkb-firmware-identity.json', 'w') as handle:
                json.dump({'chip_md5': 'b' * 32, 'at': 500}, handle)
            for at in range(1000, 2021, 60):
                before = self.collect(at, force=True)
            old_contexts = dict((c['context_id'], c['seconds']) for c in before['contexts'])
            with open(self.service.etc + '/bkb-firmware-identity.json', 'w') as handle:
                json.dump({'chip_md5': 'c' * 32, 'at': 1700002050}, handle)
            for at in (2140, 2200, 2260, 2320):
                stats, devs, pools = fixtures.api(at, accepted=10 + (at - 2140) // 2)
                for row in stats['STATS']:
                    row.update(DiagPid=456, DiagStartUnix=1700002120,
                               DiagDeviceElapsed=at - 2120, ClockGeneration=1,
                               ExperimentalClock=True, RequestedClockIndex=30)
                result = Service(self.root).record(stats, stats, devs, pools,
                    {'userAutoFan': True}, [], 1700000000 + at, force=True)
                for board in result['boards'].values():
                    self.assertTrue(board['15m']['valid'])
                    self.assertEqual(board['context']['pid'], 456)
                for context in result['contexts']:
                    if context['context_id'] in old_contexts:
                        self.assertEqual(context['seconds'], old_contexts[context['context_id']])
                    else:
                        self.assertEqual(at, 2320)
                        self.assertEqual(context['seconds'], 60)
                        self.assertEqual(context['accepted'], 30)
            refreshed = Service(self.root).command('status', now=1700002320)
            self.assertEqual(refreshed['target'], target)
            self.assertTrue(refreshed['boards']['0']['15m']['valid'])
            cache = read_json(self.service.rates, {})
            self.assertTrue(cache['boards']['0']['15m']['valid'])
            self.assertEqual(cache['boards']['0']['context']['pid'], 456)
    def test_reconnect_and_fresh_read_preserve_live_rates_on_both_storage_targets(self):
        for target in ('RAM', 'SD'):
            self.service.command('clear', now=1700000999)
            self.service.command('target', target, now=1700000999)
            for at in range(1000, 5201, 60):
                before = self.collect(at, force=True)
            old_work = sum(c['difficulty'] for c in before['contexts'])
            stats, devs, pools = fixtures.api(5260)
            for row in stats['STATS']:
                row['DiagRouteEpoch'] += 2
            self.service.record(stats, stats, devs, pools, {}, [], 1700005260, force=True)
            refreshed = Service(self.root).command('status', now=1700005260)
            self.assertEqual(refreshed['target'], target)
            self.assertEqual(sum(c['difficulty'] for c in refreshed['contexts']), old_work)
            cache = read_json(self.service.rates, {})
            for bid in ('0', '1', '2'):
                self.assertTrue(refreshed['boards'][bid]['15m']['valid'])
                self.assertTrue(refreshed['boards'][bid]['1h']['full_window'])
                self.assertEqual(cache['boards'][bid]['context']['re'], 4)
                self.assertTrue(cache['boards'][bid]['1h']['full_window'])

    def test_outage_invalidates_windows_and_preserves_history(self):
        for at in range(1000, 1601, 60):
            self.collect(at)
        result = self.service.record(None, None, None, None, {}, [], 1700001660)
        self.assertFalse(result['boards']['0']['15m']['valid'])
        self.assertTrue(result['contexts'])
        stale = self.service.command('status', now=1700002200)
        self.assertFalse(stale['boards']['0']['15m']['valid'])
        self.assertNotIn('private-worker', json.dumps(stale))
        self.assertNotIn('salt', stale)

    def test_failed_save_cannot_leave_successful_rates(self):
        for at in range(1000, 1601, 60):
            self.collect(at)
        def fail(*args, **kwargs):
            raise StoreError('Storage is read only')
        self.service.store.tick = fail
        with self.assertRaises(StoreError):
            self.collect(1660)
        status = read_json(self.service.summary, {})
        self.assertFalse(status['ok'])
        self.assertIn('read only', status['error'])
        self.assertFalse(read_json(self.service.rates, {})['boards'])
        self.assertTrue(status['contexts'])

    def test_invalid_mutations_do_not_initialize_or_clear(self):
        for operation, argument in (('target', 'disk'), ('reset-board', '-1'),
                                    ('reset-board', '1.0'), ('clear-side', 'all'), ('other', None)):
            with self.assertRaises(StoreError):
                self.service.command(operation, argument, now=1700001000)
        self.assertFalse(os.path.exists(self.service.sd))

    def test_command_lock_serializes_publication(self):
        self.collect(1000)
        other = Service(self.root)
        with self.service.locked():
            with self.assertRaises(StoreError):
                other.command('clear', now=1700001000)
        self.assertTrue(other.command('status', now=1700001000)['ok'])


if __name__ == '__main__':
    unittest.main()
