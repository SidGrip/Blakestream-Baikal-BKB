#!/usr/bin/env python
from __future__ import division
import errno
import hashlib
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin'))
import measured_rate_store as m


def record(meta, now, board=0, profile=0, seconds=60, accepted=30, hw=0):
    context = {'board': board, 'installation': meta['board_ids'][str(board)], 'profile': profile}
    ident = hashlib.sha256(json.dumps(context, sort_keys=True).encode('ascii')).hexdigest()
    return {'context_id': ident, 'context': context, 'end': now, 'seconds': seconds,
            'difficulty': accepted * 32, 'accepted': accepted, 'rejected': 0, 'hw': hw,
            'temp_sum': 40 * seconds, 'temp_min': 39, 'temp_max': 41,
            'fan_sum': 80 * seconds, 'fixed32': True}


def writer(root, board):
    store = m.Store(root + '/ram', root + '/sd')
    for at in range(1000, 1010):
        store.tick(lambda meta, runtime: [record(meta, at, board=board)], at, force=True)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bkb-rate-store-')
        self.ram, self.sd = self.root + '/ram', self.root + '/sd'
        self.store = m.Store(self.ram, self.sd)
        self.cap = m.MAX_DB_BYTES

    def tearDown(self):
        m._fault = None
        m.MAX_DB_BYTES = self.cap
        shutil.rmtree(self.root)

    def add(self, at=1000, force=False, **kwargs):
        return self.store.tick(lambda meta, runtime: [record(meta, at, **kwargs)], at, force=force)

    def test_default_ram_commits_without_sd_database(self):
        self.assertEqual(m.MAX_DB_BYTES, 2 * 1024 * 1024)
        status = self.add()
        self.assertEqual(status['target'], 'RAM')
        self.assertFalse(status['unsaved'])
        self.assertEqual(status['last_saved'], 1000)
        self.assertEqual(status['contexts'][0]['difficulty'], 960)
        name = 'measured-rates-' + status['generation'] + '.sqlite'
        self.assertTrue(os.path.isfile(self.ram + '/' + name))
        self.assertFalse(os.path.exists(self.sd + '/' + name))
        reopened = m.Store(self.ram, self.sd).status(now=1001)
        self.assertEqual(reopened['target'], 'RAM')
        self.assertEqual(reopened['contexts'], status['contexts'])
        shutil.rmtree(self.ram)
        rebooted = self.store.status(now=1002)
        self.assertEqual(rebooted['target'], 'RAM')
        self.assertFalse(rebooted['contexts'])

    def test_clear_before_first_sample_defaults_to_ram(self):
        status = self.store.clear(now=999)
        self.assertEqual(status['target'], 'RAM')
        self.assertFalse(status['contexts'])
        self.assertFalse(any(name.endswith('.sqlite') for name in os.listdir(self.sd)))
        self.assertEqual(self.add()['target'], 'RAM')

    def test_saved_sd_selection_survives_reopen_and_clear(self):
        self.store.set_target('SD', now=999)
        before = self.add(force=True)
        reopened = m.Store(self.ram, self.sd).status(now=1001)
        self.assertEqual(reopened['target'], 'SD')
        self.assertEqual(reopened['contexts'], before['contexts'])
        self.assertEqual(self.store.clear(now=1002)['target'], 'SD')

    def test_sd_buffer_and_timed_flush(self):
        self.store.set_target('SD', now=999)
        status = self.add()
        self.assertEqual(status['target'], 'SD')
        self.assertTrue(status['unsaved'])
        self.assertEqual(status['last_saved'], 999)
        self.assertNotIn('salt', status)
        status = self.store.tick(lambda meta, runtime: [], 1300)
        self.assertFalse(status['unsaved'])
        self.assertEqual(status['last_saved'], 1300)
        self.assertEqual(status['contexts'][0]['difficulty'], 960)

    def test_weighting_zero_work_and_errors(self):
        self.add(seconds=30, accepted=20)
        result = self.add(at=1090, seconds=90, accepted=0, hw=2, force=True)
        c = result['contexts'][0]
        self.assertEqual((c['seconds'], c['difficulty'], c['accepted'], c['hw']), (120, 640, 20, 2))
        self.assertEqual(c['temp_sum'] / c['seconds'], 40)

    def test_backward_clock_does_not_stall_pending_flush(self):
        self.store.set_target('SD', now=999)
        self.add()
        result = self.store.tick(lambda meta, runtime: [], 900)
        self.assertFalse(result['unsaved'])
        self.assertEqual(result['contexts'][0]['difficulty'], 960)

    def test_migrations_preserve_pending_and_retire_copy(self):
        self.store.set_target('SD', now=999)
        start = self.add()
        for target, directory, other in (('RAM', self.ram, self.sd), ('SD', self.sd, self.ram)):
            result = self.store.set_target(target, now=1001)
            self.assertEqual(result['target'], target)
            self.assertEqual(result['contexts'], start['contexts'])
            self.assertFalse(result['unsaved'])
            self.assertTrue(os.path.isfile(directory + '/measured-rates-' + result['generation'] + '.sqlite'))
            self.assertFalse(os.path.exists(other + '/measured-rates-' + result['generation'] + '.sqlite'))

    def test_failed_migration_keeps_original(self):
        self.store.set_target('SD', now=999)
        self.add(force=True)
        for step in ('db-copy', 'db-verify', 'control-publish'):
            def fail(got):
                if got == step:
                    raise OSError(errno.ENOSPC, 'injected full disk')
            m._fault = fail
            with self.assertRaises(m.StoreError):
                self.store.set_target('RAM', now=1001)
            m._fault = None
            status = self.store.status(now=1002)
            self.assertEqual(status['target'], 'SD')
            self.assertEqual(status['contexts'][0]['difficulty'], 960)

    def test_disk_full_retains_pending_and_reports_unsaved(self):
        self.store.set_target('SD', now=999)
        self.add(force=True)
        def fail(step):
            if step == 'db-commit':
                raise OSError(errno.ENOSPC, 'injected full disk')
        m._fault = fail
        failed = self.add(at=1060, force=True)
        self.assertFalse(failed['ok'])
        self.assertTrue(failed['unsaved'])
        self.assertEqual(failed['last_saved'], 1000)
        self.assertIn('full', failed['error'])
        m._fault = None
        recovered = self.store.tick(lambda meta, runtime: [], 1120, force=True)
        self.assertTrue(recovered['ok'])
        self.assertEqual(recovered['contexts'][0]['difficulty'], 1920)
        self.assertFalse(recovered['unsaved'])

    def test_reboot_sd_preserves_durable_only_ram_stays_volatile(self):
        self.store.set_target('SD', now=999)
        self.add(force=True)
        self.add(at=1060)
        shutil.rmtree(self.ram)
        status = self.store.status(now=1120)
        self.assertEqual(status['contexts'][0]['difficulty'], 960)
        self.assertFalse(status['runtime'])
        self.store.set_target('RAM', now=1120)
        shutil.rmtree(self.ram)
        status = self.store.status(now=1180)
        self.assertEqual(status['target'], 'RAM')
        self.assertFalse(status['contexts'])

    def test_clear_rejects_old_generation(self):
        old = self.add()
        old_path = self.ram + '/measured-rates-state-' + old['generation'] + '.json'
        with open(old_path, 'rb') as handle:
            snapshot = handle.read()
        cleared = self.store.clear(now=1001)
        self.assertNotEqual(old['generation'], cleared['generation'])
        self.assertNotEqual(old['board_ids'], cleared['board_ids'])
        with open(old_path, 'wb') as handle:
            handle.write(snapshot)
        result = self.store.tick(lambda meta, runtime: [], 1002, force=True)
        self.assertFalse(result['contexts'])
        self.assertFalse(os.path.exists(old_path))

    def test_retention_and_context_limits(self):
        self.add(force=True)
        future = 1000 + 8 * 86400
        result = self.add(at=future, profile=1, force=True)
        self.assertEqual(len(result['contexts']), 1)
        self.assertEqual(result['contexts'][0]['context']['profile'], 1)
        for profile in range(2, 82):
            result = self.add(at=future + profile * 60, profile=profile, force=True)
        self.assertEqual(len(result['contexts']), 64)
        self.assertTrue(all(c['seconds'] == 60 and c['difficulty'] == 960 for c in result['contexts']))

    def test_byte_cap_prunes_work_and_time_together(self):
        m.MAX_DB_BYTES = 65536
        for profile in range(100):
            result = self.add(at=1000 + 60 * profile, profile=profile, force=True)
        self.assertLessEqual(result['bytes'], m.MAX_DB_BYTES)
        self.assertLessEqual(len(result['contexts']), 64)
        self.assertTrue(all(c['difficulty'] == 16 * c['seconds'] for c in result['contexts']))

    def test_pending_and_runtime_caps(self):
        self.store.set_target('SD', now=999)
        result = self.store.tick(lambda meta, runtime: [record(meta, 1000) for _ in range(200)], 1000)
        self.assertLessEqual(result['pending_intervals'], 128)
        self.assertGreater(result['dropped'], 0)
        def too_big(meta, runtime):
            runtime['oversized'] = 'x' * (m.MAX_RUNTIME_BYTES + 1)
            return []
        with self.assertRaises(m.StoreError):
            self.store.tick(too_big, 1001)
        self.assertNotIn('oversized', self.store.status(now=1001)['runtime'])

    def test_corrupt_database_is_not_silently_replaced(self):
        self.store.set_target('SD', now=999)
        result = self.add(force=True)
        path = self.sd + '/measured-rates-' + result['generation'] + '.sqlite'
        with open(path, 'wb') as handle:
            handle.write(b'broken database')
        with self.assertRaises(m.StoreError):
            self.store.status(now=1001)
        with open(path, 'rb') as handle:
            self.assertEqual(handle.read(), b'broken database')
        cleared = self.store.clear(now=1002)
        self.assertTrue(cleared['ok'])
        self.assertFalse(cleared['contexts'])

    def test_read_status_and_clear_leave_other_files_unchanged(self):
        self.add(force=True)
        path = self.sd + '/other-history.json'
        with open(path, 'w') as handle:
            handle.write('keep')
        control = self.sd + '/measured-rates-control.json'
        before = os.stat(control).st_mtime
        self.store.status(now=1001)
        self.assertEqual(before, os.stat(control).st_mtime)
        self.store.clear(now=1002)
        with open(path) as handle:
            self.assertEqual(handle.read(), 'keep')

    def test_shared_writer_lock(self):
        self.store.status(now=999)
        jobs = [multiprocessing.Process(target=writer, args=(self.root, board)) for board in (0, 1)]
        for job in jobs:
            job.start()
        for job in jobs:
            job.join(20)
            if job.is_alive():
                job.terminate()
                job.join()
            self.assertEqual(job.exitcode, 0)
        result = self.store.status(now=1010)
        self.assertEqual(sum(c['difficulty'] for c in result['contexts']), 19200)


if __name__ == '__main__':
    unittest.main()
