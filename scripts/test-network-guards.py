#!/usr/bin/env python3
import importlib.util
import json
import os
import unittest
from unittest import mock
import dbus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('network', os.path.join(ROOT, 'overlay/opt/scripta/bin/network-settings.py'))
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


class MemoryFS:
    def __init__(self):
        self.raw = None
        self.boot = 'boot-a'
        self.locked = False

    def read_txn(self): return self.raw
    def write_txn(self, value): self.raw = value
    def read_boot_id(self): return self.boot
    def ensure_state_dir(self): pass

    def try_lock(self):
        if self.locked:
            raise n.NetError('network settings are busy')
        self.locked = True
        return self

    def release(self): self.locked = False


class Guards(unittest.TestCase):
    def setUp(self):
        self.fs = MemoryFS()
        self.mono = 100
        self.wall = 1000
        self.s = n.Service(None, dbus, self.fs, lambda:self.wall, lambda delay:None,
                           lambda size:b'a' * size, lambda:0, grace=0, monotonic=lambda:self.mono)
        self.s.interfaces = mock.Mock(return_value=[])
        self.s._select_profile = mock.Mock(return_value=None)
        self.calls = []
        self.timer_ok = True
        self.worker_lock = False
        def transport(service, path, iface, method, args, what='NetworkManager'):
            self.calls.append((service, path, method, args))
            if service != n.SD_SERVICE:
                raise AssertionError('unexpected NM transport')
            if method in ('GetUnit','LoadUnit'):
                return '/timer' if args[0] == n.ROLLBACK_UNIT else '/apply'
            if method == 'Get':
                return 'active' if path == '/timer' and self.timer_ok else 'inactive'
            if method == 'StartUnit':
                if args[0] == n.APPLY_UNIT and self.worker_lock:
                    self.fs.locked = True
                return '/job'
            raise AssertionError(method)
        self.s._dbus = transport

    def apply(self):
        return self.s.dispatch('apply', json.dumps({'interface':'eth0', 'mode':'dhcp'}))

    def txn(self): return json.loads(self.fs.raw)

    def test_timer_is_armed_before_worker(self):
        self.assertTrue(self.apply()['ok'])
        starts = [item[3][0] for item in self.calls if item[2] == 'StartUnit']
        self.assertEqual(starts, [n.ROLLBACK_UNIT, n.APPLY_UNIT])

    def test_login_pending_check_reads_journal_without_mutation(self):
        self.apply()
        before = self.fs.raw
        self.calls = []
        self.s.interfaces.reset_mock()
        self.fs.locked = True
        result = self.s.dispatch('pending', '')
        self.assertTrue(result['ok'])
        self.assertEqual(result['pending']['id'], self.txn()['id'])
        self.assertEqual(self.fs.raw, before)
        self.assertEqual(self.calls, [])
        self.s.interfaces.assert_not_called()

    def test_inactive_timer_prevents_apply(self):
        self.timer_ok = False
        self.assertFalse(self.apply()['ok'])
        self.assertIsNone(self.fs.raw)
        self.assertFalse(any(item[2] == 'StartUnit' and item[3][0] == n.APPLY_UNIT for item in self.calls))

    def test_worker_winning_lock_does_not_break_queued_response(self):
        self.worker_lock = True
        result = self.apply()
        self.assertTrue(result['ok'])
        self.assertEqual(result['pending']['id'], self.txn()['id'])

    def test_pending_and_lock_contention_prevent_another_change(self):
        self.apply()
        before = self.fs.raw
        self.assertFalse(self.apply()['ok'])
        self.assertEqual(self.fs.raw, before)
        self.fs.locked = True
        self.assertFalse(self.apply()['ok'])
        self.assertEqual(self.fs.raw, before)

    def test_deadline_ignores_wall_clock_and_fails_closed_without_boot(self):
        self.apply()
        txn = self.txn()
        self.wall = -100000
        self.assertFalse(self.s._expired(txn))
        self.mono += n.ROLLBACK_SECONDS
        self.assertTrue(self.s._expired(txn))
        self.mono = 100
        self.fs.boot = None
        self.assertTrue(self.s._expired(txn))
        self.fs.boot = 'boot-b'
        self.assertTrue(self.s._expired(txn))

    def test_uncertain_absence_does_not_finish_rollback(self):
        self.apply()
        txn = self.txn()
        self.s._candidate_still_active = mock.Mock(return_value=False)
        self.s._lookup_uuid = mock.Mock(side_effect=n.NetError('lookup timed out'))
        with self.assertRaises(n.NetError): self.s._delete_candidate(txn)

    def test_candidate_update_resolves_uuid_and_rejects_mismatch(self):
        self.apply()
        txn = self.txn()
        txn['candidate']['path'] = '/old_path_now_unrelated'
        settings = {'connection':{'uuid':n._tagged('s', txn['candidate']['uuid'])}}
        self.s._lookup_uuid = mock.Mock(return_value='/current_candidate')
        self.s._connection_settings = mock.Mock(return_value=settings)
        self.s._dbus = mock.Mock()
        self.s._update_candidate(txn, settings, True)
        self.assertEqual(self.s._dbus.call_args[0][1], '/current_candidate')
        self.s._dbus.reset_mock()
        self.s._connection_settings.return_value = {'connection':{'uuid':n._tagged('s', 'unrelated')}}
        with self.assertRaises(n.NetError): self.s._update_candidate(txn, settings, True)
        self.s._dbus.assert_not_called()

    def test_disconnected_original_does_not_get_activated(self):
        self.apply()
        txn = self.txn()
        txn['original'] = {'active':False, 'uuid':'original'}
        self.s._device = mock.Mock(return_value='/device')
        self.s._prop = lambda path, iface, name: '/active' if name == 'ActiveConnection' else txn['candidate']['uuid']
        self.s._restore_original = mock.Mock(return_value='/original')
        self.s._candidate_still_active = mock.Mock(return_value=False)
        self.s._delete_candidate = mock.Mock()
        self.s._dbus = mock.Mock()
        self.s._restore(txn)
        self.assertEqual([call[0][3] for call in self.s._dbus.call_args_list], ['Disconnect'])

    def test_failed_rollback_remains_pending_and_can_retry(self):
        self.apply()
        txn = self.txn()
        self.s._restore = mock.Mock(side_effect=n.NetError('temporary failure'))
        self.assertFalse(self.s._rollback(txn, 'tick'))
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.s._restore.side_effect = None
        self.assertTrue(self.s._rollback(txn, 'tick'))
        self.assertEqual(self.txn()['state'], 'rolled_back')

    def test_confirmation_rejects_wrong_address_and_expiry(self):
        self.apply()
        txn = self.txn()
        txn['state'] = 'awaiting-confirmation'
        self.s._save(txn)
        self.s._confirm_ready = mock.Mock(return_value=False)
        self.s._update_candidate = mock.Mock()
        body = json.dumps({'id':txn['id'], 'server_address':'192.0.2.1'})
        self.assertFalse(self.s.dispatch('confirm', body)['ok'])
        self.s._update_candidate.assert_not_called()
        self.s._restore = mock.Mock()
        self.mono += n.ROLLBACK_SECONDS
        self.assertFalse(self.s.dispatch('confirm', body)['ok'])
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.s._update_candidate.assert_not_called()

    def test_transient_observation_is_retried_during_restore(self):
        self.s._original_matches = mock.Mock(side_effect=[n.NetError('IP object replaced'), False, True])
        self.s._wait_original({'uuid':'original'})
        self.assertEqual(self.s._original_matches.call_count, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
