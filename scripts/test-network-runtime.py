#!/usr/bin/env python3
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from unittest import mock
import dbus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('network', os.path.join(ROOT, 'overlay/opt/scripta/bin/network-settings.py'))
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'state')
        self.fs = n.PosixFS(self.path, require_root=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_roundtrip_and_lock(self):
        self.assertIsNone(self.fs.read_txn())
        self.fs.ensure_state_dir()
        first = self.fs.try_lock()
        with self.assertRaisesRegex(n.NetError, 'busy'):
            self.fs.try_lock()
        self.fs.write_txn('{"value":1}')
        self.fs.write_txn('{"value":2}')
        self.assertEqual(json.loads(self.fs.read_txn().decode()), {'value':2})
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.path, 'transaction.json')).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o700)
        first.release()
        self.fs.try_lock().release()

    def test_symlinked_directory_rejected(self):
        os.symlink(self.temp.name, self.path)
        with self.assertRaises(n.NetError):
            self.fs.ensure_state_dir()

    def test_symlinked_state_rejected(self):
        self.fs.ensure_state_dir()
        target = os.path.join(self.temp.name, 'outside')
        with open(target, 'w') as output:
            output.write('unchanged')
        os.symlink(target, os.path.join(self.path, 'transaction.json'))
        with self.assertRaises(n.NetError):
            self.fs.read_txn()
        with self.assertRaises(n.NetError):
            self.fs.write_txn('{}')
        with open(target) as value:
            self.assertEqual(value.read(), 'unchanged')

    def test_writable_state_and_oversize_rejected(self):
        self.fs.ensure_state_dir()
        self.fs.write_txn('{}')
        os.chmod(os.path.join(self.path, 'transaction.json'), 0o666)
        with self.assertRaises(n.NetError):
            self.fs.read_txn()
        with self.assertRaises(n.NetError):
            self.fs.write_txn('{}')
        with self.assertRaises(n.NetError):
            self.fs.write_txn('x' * (n.MAX_TXN_BYTES + 1))

    def test_real_boot_identity(self):
        self.assertRegex(n.PosixFS().read_boot_id().strip(), n.BOOT_ID_TEXT)

    def test_keyfile_presence_is_independent_of_runtime_dirty_flag(self):
        self.fs.profiles_dir = self.temp.name
        uuid = '12345678-1234-1234-1234-123456789abc'
        self.assertFalse(self.fs.profile_saved(uuid))
        with open(os.path.join(self.temp.name, 'profile'), 'w') as output:
            output.write('[connection]\nuuid=' + uuid + '\n[ipv4]\nmethod=manual\n')
        self.assertTrue(self.fs.profile_saved(uuid))
        self.assertFalse(self.fs.profile_saved('other'))


class TransportTests(unittest.TestCase):
    def test_login_pending_check_does_not_open_dbus(self):
        with mock.patch.object(dbus, 'SystemBus', side_effect=AssertionError('no bus at login')):
            service = n.real_service(journal_only=True)
        service.fs = mock.Mock()
        service.fs.read_txn.return_value = None
        service.geteuid = lambda:0
        self.assertEqual(service.dispatch('pending', '')['pending'], None)
        service.fs.ensure_state_dir.assert_not_called()
        service.fs.try_lock.assert_not_called()

    def test_timeout_no_introspection_and_pinned_owner(self):
        bus = mock.Mock()
        bus.get_name_owner.return_value = ':1.42'
        method = mock.Mock(return_value=[])
        service = n.Service(bus, dbus, None, lambda:100, lambda delay:None,
                            os.urandom, lambda:0, monotonic=lambda:100)
        service.call_deadline = 101
        with mock.patch.object(dbus, 'Interface', return_value=mock.Mock(ListConnections=method)):
            for unused in range(2):
                service._dbus(n.NM_SERVICE, n.NM_SETTINGS_PATH, n.NM_SETTINGS_IFACE, 'ListConnections', [])
        bus.get_name_owner.assert_called_once_with(n.NM_SERVICE)
        bus.get_object.assert_called_with(':1.42', n.NM_SETTINGS_PATH, introspect=False)
        method.assert_called_with(timeout=1)
        service.call_deadline = 99
        with self.assertRaisesRegex(n.NetError, 'timed out'):
            service._dbus(n.NM_SERVICE, n.NM_SETTINGS_PATH, n.NM_SETTINGS_IFACE, 'ListConnections', [])

    def test_idle_timer_does_not_contact_bus_or_create_state(self):
        fs = mock.Mock()
        fs.read_txn.return_value = None
        bus = mock.Mock()
        service = n.Service(bus, dbus, fs, lambda:100, lambda delay:None, os.urandom, lambda:0)
        self.assertTrue(service.dispatch('tick', '')['ok'])
        self.assertEqual(bus.mock_calls, [])
        fs.ensure_state_dir.assert_not_called()
        fs.try_lock.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
