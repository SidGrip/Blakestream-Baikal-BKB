#!/usr/bin/env python
from __future__ import division
import errno
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin'))
import measured_rate_store as storage


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bkb-measured-recovery-')
        self.ram, self.sd = os.path.join(self.root, 'ram'), os.path.join(self.root, 'sd')
        self.store = storage.Store(self.ram, self.sd)
        self.sync = storage._sync_dir

    def tearDown(self):
        storage._fault = None
        storage._sync_dir = self.sync
        shutil.rmtree(self.root)

    def add(self, now=1000, force=False):
        def record(meta, runtime):
            return [{'context_id': str(board) * 64,
                     'context': {'board': board, 'installation': meta['board_ids'][str(board)]},
                     'end': now, 'seconds': 60, 'accepted': 30, 'difficulty': 960,
                     'rejected': 0, 'hw': 0, 'temp_sum': 2400, 'temp_min': 40,
                     'temp_max': 40, 'fan_sum': 4200, 'fixed32': True}
                    for board in range(3)]
        return self.store.tick(record, now, force=force)

    def test_migration_post_rename_sync_failure_preserves_data(self):
        self.store.set_target('SD', now=999)
        old = self.add(force=True)
        calls = [0]
        def fail(directory):
            if directory == self.sd:
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError(errno.EIO, 'injected directory sync failure')
            return self.sync(directory)
        storage._sync_dir = fail
        with self.assertRaises(storage.StoreError):
            self.store.set_target('RAM', now=1001)
        self.assertEqual(calls[0], 2)
        storage._sync_dir = self.sync
        recovered = self.store.status(now=1002)
        self.assertEqual(sum(c['difficulty'] for c in recovered['contexts']),
                         sum(c['difficulty'] for c in old['contexts']))

    def test_clear_post_rename_failure_has_valid_generation(self):
        self.store.set_target('SD', now=999)
        self.add(force=True)
        calls = [0]
        def fail(directory):
            if directory == self.sd:
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError(errno.EIO, 'injected directory sync failure')
            return self.sync(directory)
        storage._sync_dir = fail
        with self.assertRaises(storage.StoreError):
            self.store.clear(now=1001)
        self.assertEqual(calls[0], 2)
        storage._sync_dir = self.sync
        self.assertTrue(self.store.status(now=1002)['ok'])

    def test_reset_after_publish_drops_only_replaced_board(self):
        self.add(force=True)
        self.add(now=1060)
        def fail(step):
            if step == 'reset-after-publish':
                raise OSError(errno.EIO, 'injected crash')
        storage._fault = fail
        with self.assertRaises(storage.StoreError):
            self.store.reset_board(1, now=1061)
        storage._fault = None
        result = self.store.status(now=1062)
        self.assertEqual([c['context']['board'] for c in result['contexts']], [0, 2])
        self.assertEqual(sum(c['difficulty'] for c in result['contexts']), 3840)

    def test_committed_pending_replay_is_idempotent(self):
        self.store.status(now=999)
        count = [0]
        def fail(step):
            if step == 'state-write':
                count[0] += 1
                if count[0] > 1:
                    raise OSError(errno.EIO, 'injected snapshot failure after commit')
        storage._fault = fail
        self.add(force=True)
        storage._fault = None
        result = self.store.tick(lambda meta, runtime: [], 1060, force=True)
        self.assertEqual(sum(c['difficulty'] for c in result['contexts']), 2880)
        self.assertFalse(result['unsaved'])


if __name__ == '__main__':
    unittest.main()
