#!/usr/bin/env python3
"""Offline Service transactions. Real dbus-python values; fake transport, filesystem, and clocks."""
import importlib.util
import json
import os
import time
import unittest

try:
    import dbus
except ImportError:
    raise SystemExit('missing dbus-python: run with an interpreter providing python3-dbus')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location('network_helper', os.path.join(
    ROOT, 'overlay', 'opt', 'scripta', 'bin', 'network-settings.py'))
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def _refuse_real_bus(*_args, **_kwargs):
    raise AssertionError('refusing a real bus')


dbus.SystemBus = _refuse_real_bus
dbus.SessionBus = _refuse_real_bus

DEVICE = '/org/freedesktop/NetworkManager/Devices/1'
ACTIVE = '/org/freedesktop/NetworkManager/ActiveConnection/1'
IP4 = '/org/freedesktop/NetworkManager/IP4Config/1'
DHCP4 = '/org/freedesktop/NetworkManager/DHCP4Config/1'
ORIG = '/org/freedesktop/NetworkManager/Settings/1'
TIMER = '/org/freedesktop/systemd1/unit/rollback'
APPLY_PATH = '/org/freedesktop/systemd1/unit/apply'
UNIT_IFACE = 'org.freedesktop.systemd1.Unit'
MAC = '02:00:00:00:00:01'
ORIG_UUID = '12345678-1234-1234-1234-123456789abc'
IFACE_NAME = 'eth0'
ORIG_LEASE = ('192.0.2.10', 24, '192.0.2.1', ['192.0.2.53'])
DHCP_LEASE = ('203.0.113.10', 24, '203.0.113.1', ['203.0.113.53'])
STATIC_ADDRESS = '198.51.100.24'
STATIC_GATEWAY = '198.51.100.1'
STATIC_DNS = ['198.51.100.53']

DHCP_BODY = {
    'interface': IFACE_NAME, 'mode': 'dhcp', 'address': '', 'prefix': 24,
    'gateway': '', 'dns': [],
}
STATIC_BODY = {
    'interface': IFACE_NAME, 'mode': 'static', 'address': STATIC_ADDRESS,
    'prefix': 24, 'gateway': STATIC_GATEWAY, 'dns': list(STATIC_DNS),
}


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def original_profile():
    return {
        'connection': {
            'id': {'sig': 's', 'v': 'QA Ethernet'},
            'uuid': {'sig': 's', 'v': ORIG_UUID},
            'type': {'sig': 's', 'v': '802-3-ethernet'},
            'interface-name': {'sig': 's', 'v': IFACE_NAME},
            'autoconnect': {'sig': 'b', 'v': True},
            'autoconnect-priority': {'sig': 'i', 'v': 0},
        },
        '802-3-ethernet': {
            'mac-address': {'sig': 'ay', 'v': [2, 0, 0, 0, 0, 1]},
        },
        'ipv4': {
            'method': {'sig': 's', 'v': 'auto'},
            'addresses': {'sig': 'aau', 'v': []},
            'address-data': {'sig': 'aa{sv}', 'v': []},
            'gateway': {'sig': 's', 'v': ''},
            'dns': {'sig': 'au', 'v': []},
        },
        'ipv6': {'method': {'sig': 's', 'v': 'auto'}},
    }


ORIGINAL_ENCODED = HELPER.encode_settings(original_profile(), dbus)


class Call(object):
    def __init__(self, owner, path, iface, method, args):
        self.owner = owner
        self.path = path
        self.iface = iface
        self.method = method
        self.args = args


class Proxy(object):
    def __init__(self, world, owner, path):
        self.world = world
        self.bus_name = owner
        self.requested_bus_name = owner
        self.object_path = path

    def get_dbus_method(self, member, dbus_interface):
        def caller(*args, **kwargs):
            timeout = kwargs.get('timeout')
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                raise AssertionError('dbus timeout was not applied')
            return self.world.dispatch(self.bus_name, self.object_path, dbus_interface, member, args)
        return caller


class Bus(object):
    def __init__(self, world):
        self.world = world
        self.owners = []

    def get_name_owner(self, name):
        if name == HELPER.NM_SERVICE:
            return ':1.50'
        if name == HELPER.SD_SERVICE:
            return ':1.51'
        raise dbus.DBusException('name has no owner')

    def get_object(self, bus_name, object_path, introspect=True):
        if introspect is not False:
            raise AssertionError('introspection is forbidden')
        if not str(bus_name).startswith(':'):
            raise AssertionError('well-known bus name was used')
        self.owners.append(str(bus_name))
        return Proxy(self.world, str(bus_name), str(object_path))


class MemoryFS(object):
    def __init__(self):
        self.text = None
        self.boot_id = 'boot-aaa-1\n'
        self.held = False
        self.saved = set([ORIG_UUID])

    def ensure_state_dir(self):
        return None

    def profile_saved(self, uuid):
        return uuid in self.saved

    def note_saved(self, uuid):
        self.saved.add(uuid)

    def note_removed(self, uuid):
        self.saved.discard(uuid)

    def try_lock(self):
        if self.held:
            raise HELPER.NetError('network settings are busy')
        self.held = True
        return _Lock(self)

    def read_txn(self):
        return self.text

    def write_txn(self, text):
        self.text = text

    def read_boot_id(self):
        return self.boot_id


class _Lock(object):
    def __init__(self, fs):
        self.fs = fs

    def release(self):
        if self.fs is not None:
            self.fs.held = False
            self.fs = None


class Clock(object):
    def __init__(self):
        self.wall = 1700000000
        self.mono = 5000

    def __call__(self):
        return self.wall

    def monotonic(self):
        return self.mono


class URandom(object):
    def __init__(self):
        self.count = 1

    def __call__(self, size):
        if size != 16:
            raise AssertionError(size)
        block = bytes([self.count]) * size
        self.count += 1
        return block


class World(object):
    def __init__(self, disconnected, fs):
        self.fs = fs
        decoded = HELPER.decode_settings(ORIGINAL_ENCODED, dbus)
        self.connections = {ORIG: {'encoded': ORIGINAL_ENCODED, 'decoded': decoded, 'unsaved': False}}
        self.order = [ORIG]
        self.next_id = 2
        self.calls = []
        self.fail = set()
        self.fail_after = {}
        self.lie_gateway = None
        self.suppress_dhcp4 = False
        self.units = {TIMER: 'inactive', APPLY_PATH: 'inactive'}
        self.address = None
        self.prefix = None
        self.gateway = None
        self.dns = []
        self.dhcp4_path = '/'
        self.ip4_path = '/'
        self.active_path = '/'
        self.ac_state = 0
        self.ac_uuid = None
        self.ac_connection = None
        self.state = 30
        if not disconnected:
            self.ac_connection = ORIG
            self.ac_uuid = ORIG_UUID
            self._set_lease(ORIG_LEASE[0], ORIG_LEASE[1], ORIG_LEASE[2], ORIG_LEASE[3], DHCP4)

    def dispatch(self, owner, path, iface, method, args):
        self.calls.append(Call(owner, str(path), iface, method, args))
        if self._blocked(method):
            raise dbus.DBusException('forced failure')
        return self._handle(str(path), iface, method, args)

    def _blocked(self, method):
        if method in self.fail:
            return True
        if method not in self.fail_after:
            return False
        if self.fail_after[method] <= 0:
            return True
        self.fail_after[method] -= 1
        return False

    def _handle(self, path, iface, method, args):
        if method == 'GetDevices' and path == HELPER.NM_PATH:
            return dbus.Array([dbus.ObjectPath(DEVICE)], signature='o')
        if method == 'Get' and iface == HELPER.DBUS_PROPS:
            return self._property(path, args[0], args[1])
        if method == 'GetSettings':
            return self.connections[path]['encoded']
        if method == 'ListConnections' and path == HELPER.NM_SETTINGS_PATH:
            return dbus.Array([dbus.ObjectPath(item) for item in self.order], signature='o')
        if method == 'AddConnectionUnsaved':
            return self._add(args[0], True)
        if method == 'AddConnection':
            return self._add(args[0], False)
        if method == 'Update':
            return self._replace(path, args[0], False)
        if method == 'UpdateUnsaved':
            return self._replace(path, args[0], True)
        if method == 'Delete':
            uuid = self.connections[path]['decoded']['connection']['uuid']['v']
            self.order.remove(path)
            del self.connections[path]
            self.fs.note_removed(uuid)
            return None
        if method == 'ActivateConnection':
            return self._activate(args[0], args[1], args[2])
        if method == 'Disconnect':
            return self._disconnect(path)
        if method == 'StartUnit':
            return self._start_unit(args[0], args[1])
        if method in ('GetUnit', 'LoadUnit'):
            return dbus.ObjectPath(self._unit_path(args[0]))
        raise dbus.DBusException('unhandled {0} {1}'.format(method, path))

    def _property(self, path, iface, name):
        if path == DEVICE and iface == HELPER.NM_DEVICE_IFACE:
            return self._device_property(name)
        if path == DEVICE and iface == HELPER.NM_DEVICE_WIRED_IFACE and name == 'HwAddress':
            return dbus.String(MAC)
        if path == ACTIVE and iface == HELPER.NM_ACTIVE_IFACE:
            return self._active_property(name)
        if path == IP4 and iface == HELPER.NM_IP4_IFACE:
            return self._ip4_property(name)
        if path in self.connections and iface == HELPER.NM_CONNECTION_IFACE and name == 'Unsaved':
            return dbus.Boolean(self.connections[path]['unsaved'])
        if iface == UNIT_IFACE and name == 'ActiveState' and path in self.units:
            return dbus.String(self.units[path])
        raise dbus.DBusException('unknown property {0} {1}'.format(iface, name))

    def _device_property(self, name):
        if name == 'DeviceType':
            return dbus.UInt32(HELPER.DEVICE_TYPE_ETHERNET)
        if name == 'Interface':
            return dbus.String(IFACE_NAME)
        if name == 'Managed':
            return dbus.Boolean(True)
        if name == 'State':
            return dbus.UInt32(self.state)
        if name == 'ActiveConnection':
            return dbus.ObjectPath(self.active_path)
        if name == 'Ip4Config':
            return dbus.ObjectPath(self.ip4_path)
        if name == 'AvailableConnections':
            return dbus.Array([dbus.ObjectPath(path) for path in self.order], signature='o')
        raise dbus.DBusException(name)

    def _active_property(self, name):
        if name == 'State':
            return dbus.UInt32(self.ac_state)
        if name == 'Uuid':
            return dbus.String(self.ac_uuid or '')
        if name == 'Connection':
            return dbus.ObjectPath(self.ac_connection or '/')
        if name == 'Ip4Config':
            return dbus.ObjectPath(self.ip4_path)
        if name == 'Dhcp4Config':
            return dbus.ObjectPath(self.dhcp4_path)
        raise dbus.DBusException(name)

    def _ip4_property(self, name):
        if name == 'AddressData':
            if not self.address:
                return dbus.Array([], signature='a{sv}')
            return dbus.Array([dbus.Dictionary({
                'address': dbus.String(self.address, variant_level=1),
                'prefix': dbus.UInt32(self.prefix, variant_level=1),
            }, signature='sv')], signature='a{sv}')
        if name == 'Gateway':
            return dbus.String(self.gateway or '')
        if name == 'Nameservers':
            return dbus.Array([dbus.UInt32(HELPER._wire_ip(item)) for item in self.dns], signature='u')
        raise dbus.DBusException(name)

    def _set_lease(self, address, prefix, gateway, dns, dhcp4):
        self.address = address
        self.prefix = prefix
        self.gateway = gateway
        self.dns = list(dns)
        self.dhcp4_path = dhcp4
        self.ip4_path = IP4
        self.state = HELPER.DEVICE_STATE_ACTIVATED
        self.active_path = ACTIVE
        self.ac_state = HELPER.AC_STATE_ACTIVATED

    def _add(self, settings, unsaved):
        path = '/org/freedesktop/NetworkManager/Settings/{0}'.format(self.next_id)
        self.next_id += 1
        decoded = HELPER.decode_settings(settings, dbus)
        self.connections[path] = {'encoded': settings, 'decoded': decoded, 'unsaved': unsaved}
        self.order.append(path)
        if not unsaved:
            self.fs.note_saved(decoded['connection']['uuid']['v'])
        return dbus.ObjectPath(path)

    def _replace(self, path, settings, unsaved):
        if path not in self.connections:
            raise dbus.DBusException('missing connection')
        decoded = HELPER.decode_settings(settings, dbus)
        self.connections[path]['encoded'] = settings
        self.connections[path]['decoded'] = decoded
        self.connections[path]['unsaved'] = unsaved
        if not unsaved:
            self.fs.note_saved(decoded['connection']['uuid']['v'])
        return None

    def _activate(self, connection, device, specific):
        connection = str(connection)
        if str(device) != DEVICE or str(specific) != '/':
            raise dbus.DBusException('unexpected activation target')
        decoded = self.connections[connection]['decoded']
        uuid = decoded['connection']['uuid']['v']
        self.ac_connection = connection
        self.ac_uuid = uuid
        if uuid == ORIG_UUID:
            self._set_lease(ORIG_LEASE[0], ORIG_LEASE[1], ORIG_LEASE[2], ORIG_LEASE[3], DHCP4)
        elif decoded['ipv4']['method']['v'] == 'auto':
            dhcp4 = '/' if self.suppress_dhcp4 else DHCP4
            self._set_lease(DHCP_LEASE[0], DHCP_LEASE[1], DHCP_LEASE[2], DHCP_LEASE[3], dhcp4)
        else:
            endpoint = HELPER._manual_endpoint(decoded)
            gateway = self.lie_gateway or endpoint['gateway']
            self._set_lease(endpoint['address'], endpoint['prefix'], gateway, endpoint['dns'], '/')
        return dbus.ObjectPath(ACTIVE)

    def _disconnect(self, path):
        if path != DEVICE:
            raise dbus.DBusException('unexpected disconnect')
        self.active_path = '/'
        self.state = 30
        self.ip4_path = '/'
        self.address = None
        self.prefix = None
        self.gateway = None
        self.dns = []
        self.dhcp4_path = '/'
        self.ac_uuid = None
        self.ac_connection = None
        return None

    def _unit_path(self, name):
        if name == HELPER.ROLLBACK_UNIT:
            return TIMER
        if name == HELPER.APPLY_UNIT:
            return APPLY_PATH
        raise dbus.DBusException(name)

    def _start_unit(self, name, mode):
        if mode != 'replace':
            raise dbus.DBusException(mode)
        path = self._unit_path(name)
        if name == HELPER.ROLLBACK_UNIT:
            self.units[path] = 'active'
        return dbus.ObjectPath('/org/freedesktop/systemd1/job/1')


class NetworkTransactions(unittest.TestCase):
    def setUp(self):
        self._build(False)

    def _build(self, disconnected):
        self.clock = Clock()
        self.fs = MemoryFS()
        self.world = World(disconnected, self.fs)
        self.bus = Bus(self.world)
        self.slept = []
        self.service = HELPER.Service(
            self.bus, dbus, self.fs, self.clock, self._sleep, URandom(), lambda: 0,
            monotonic=self.clock.monotonic)

    def _sleep(self, seconds):
        self.slept.append(seconds)

    def submit(self, body):
        return self.service.dispatch('apply', dump(body))

    def activate(self):
        return self.service.dispatch('activate', '')

    def confirm(self, ident, address):
        return self.service.dispatch('confirm', dump({'id': ident, 'server_address': address}))

    def rollback(self, ident):
        return self.service.dispatch('rollback', dump({'id': ident}))

    def tick(self):
        return self.service.dispatch('tick', '')

    def txn(self):
        self.assertIsInstance(self.fs.text, str)
        return json.loads(self.fs.text)

    def calls(self, method=None, start=0):
        found = self.world.calls[start:]
        if method is None:
            return found
        return [call for call in found if call.method == method]

    def assert_expired(self, result, start):
        self.assertFalse(result['ok'], result)
        self.assertIn('expired', result['error'])
        self.assertIsNone(result['pending'])
        saved = self.txn()
        self.assertEqual(saved['state'], 'rolled_back')
        self.assertEqual(saved['last_result']['action'], 'activate')
        self.assertTrue(saved['last_result']['ok'])
        methods = [call.method for call in self.calls(start=start)]
        self.assertNotIn('AddConnectionUnsaved', methods)
        self.assertNotIn('AddConnection', methods)

    def test_backup_uses_saved_static_profile_with_cable_disconnected(self):
        self._build(True)
        body = dict(STATIC_BODY, dns=['198.51.100.2', '198.51.100.10'])
        settings = HELPER._candidate_settings(original_profile(), 'fixture', ORIG_UUID,
                                               IFACE_NAME, body)
        self.world._replace(ORIG, HELPER.encode_settings(settings, dbus), False)
        start = len(self.world.calls)
        result = self.service.dispatch('backup', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['interfaces'], [dict(body, saved=True)])
        self.assertTrue(all(call.method in ('Get', 'GetDevices', 'GetSettings')
                            for call in self.calls(start=start)))
        self.assertIsNone(self.fs.text)

    def test_backup_dhcp_never_reads_live_lease_or_dns(self):
        self.world.dns = ['192.0.2.1'] * 8 + ['2001:db8::1']
        self.world.connections[ORIG]['unsaved'] = True
        result = self.service.dispatch('backup', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['interfaces'], [{'interface': IFACE_NAME, 'saved': True,
            'mode': 'dhcp', 'address': None, 'prefix': None, 'gateway': None, 'dns': []}])
        self.assertFalse(any(call.path == IP4 for call in self.calls()))

    def test_backup_omits_unsaved_but_keeps_original_during_pending_change(self):
        self.fs.saved.clear()
        self.assertEqual(self.service.dispatch('backup', '')['interfaces'], [])
        self._build(False)
        self.assertTrue(self.submit(STATIC_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        before = self.fs.text
        start = len(self.world.calls)
        result = self.service.dispatch('backup', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['interfaces'][0]['mode'], 'dhcp')
        self.assertIsNone(result['interfaces'][0]['address'])
        self.assertTrue(result['interfaces'][0]['saved'])
        self.assertEqual(self.fs.text, before)
        self.assertTrue(all(call.method in ('Get', 'GetDevices', 'GetSettings')
                            for call in self.calls(start=start)))

    def test_backup_disconnected_prefers_confirmed_profile_over_retained_original(self):
        self.assertTrue(self.submit(STATIC_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        self.assertTrue(self.confirm(self.txn()['id'], STATIC_ADDRESS)['ok'])
        self.assertEqual(len(self.world.order), 2)
        self.world.active_path = '/'
        before = self.fs.text
        start = len(self.world.calls)
        result = self.service.dispatch('backup', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['interfaces'], [dict(STATIC_BODY, saved=True)])
        self.assertEqual(self.fs.text, before)
        self.assertTrue(all(call.method in ('Get', 'GetDevices', 'GetSettings', 'ListConnections')
                            for call in self.calls(start=start)))

    def test_backup_disconnected_filters_unsaved_available_profile(self):
        self._build(True)
        settings = HELPER._candidate_settings(original_profile(), 'fixture',
            '87654321-1234-1234-1234-123456789abc', IFACE_NAME, STATIC_BODY)
        self.world._add(HELPER.encode_settings(settings, dbus), True)
        result = self.service.dispatch('backup', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['interfaces']), 1)
        self.assertEqual(result['interfaces'][0]['mode'], 'dhcp')

    def test_service_keeps_dbus_signature_and_optional_monotonic(self):
        init = HELPER.Service.__init__
        names = init.__code__.co_varnames[:init.__code__.co_argcount]
        self.assertEqual(names[:8], (
            'self', 'bus', 'dbus_mod', 'fs', 'clock', 'sleep', 'urandom', 'geteuid'))
        self.assertEqual(names[8:], ('grace', 'monotonic'))
        self.assertIs(init.__defaults__[0], HELPER.APPLY_GRACE_SECONDS)
        self.assertIs(init.__defaults__[1], time.monotonic)
        dbus_fn = HELPER.Service._dbus
        self.assertEqual(
            dbus_fn.__code__.co_varnames[:dbus_fn.__code__.co_argcount],
            ('self', 'service', 'path', 'iface', 'method', 'args', 'what'))
        self.assertEqual(dbus_fn.__defaults__, ('NetworkManager',))
        self.assertEqual(self.service.grace, HELPER.APPLY_GRACE_SECONDS)
        self.assertIs(self.service.monotonic.__func__, Clock.monotonic)
        self.assertRaises(AssertionError, dbus.SystemBus)
        self.assertRaises(AssertionError, dbus.SessionBus)

    def test_dhcp_and_static_validation(self):
        rejects = (
            (dict(DHCP_BODY, address='192.0.2.10'), 'DHCP does not accept a static address'),
            (dict(DHCP_BODY, gateway='192.0.2.1'), 'DHCP does not accept a static gateway'),
            (dict(DHCP_BODY, dns=['192.0.2.53']), 'DHCP does not accept static DNS'),
            (dict(DHCP_BODY, prefix='24'), 'invalid prefix'),
            (dict(DHCP_BODY, metric=1), 'unknown setting'),
            ({'interface': IFACE_NAME}, 'incomplete network settings'),
            ({'interface': IFACE_NAME, 'mode': 'static', 'address': STATIC_ADDRESS,
              'prefix': 24, 'gateway': STATIC_GATEWAY}, 'static configuration is incomplete'),
            (dict(STATIC_BODY, prefix='24'), 'invalid prefix'),
            (dict(STATIC_BODY, prefix=31), 'unsupported prefix'),
            (dict(STATIC_BODY, address='192.0.2.0', gateway='192.0.2.1'), 'invalid address'),
            (dict(STATIC_BODY, address='192.0.2.255', gateway='192.0.2.1'), 'invalid address'),
            (dict(STATIC_BODY, address='192.0.2.10', gateway='198.51.100.1'), 'invalid gateway'),
            (dict(STATIC_BODY, gateway=STATIC_ADDRESS), 'invalid gateway'),
            (dict(STATIC_BODY, dns=[]), 'invalid DNS'),
            (dict(STATIC_BODY, dns=['198.51.100.53', '198.51.100.53']), 'invalid DNS'),
            (dict(STATIC_BODY, dns=['192.0.2.53', '192.0.2.54', '198.51.100.53', '203.0.113.53']),
             'invalid DNS'),
        )
        for body, error in rejects:
            result = self.submit(body)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['error'], error)
            self.assertIsNone(self.fs.text)
        self.assertEqual(self.bus.owners, [])
        self.assertEqual(self.calls('StartUnit'), [])

        self._build(False)
        accepted = self.submit(DHCP_BODY)
        self.assertTrue(accepted['ok'], accepted)
        queued = self.txn()
        self.assertEqual(queued['mode'], 'dhcp')
        self.assertIsNone(queued['address'])
        self.assertIsNone(queued['prefix'])
        self.assertIsNone(queued['gateway'])
        self.assertEqual(queued['dns'], [])
        self.assertEqual(queued['state'], 'pending')
        self.assertEqual(queued['deadline_mono'] - queued['created_mono'], HELPER.ROLLBACK_SECONDS)

        self._build(False)
        accepted = self.submit(STATIC_BODY)
        self.assertTrue(accepted['ok'], accepted)
        queued = self.txn()
        self.assertEqual(queued['mode'], 'static')
        self.assertEqual(queued['address'], STATIC_ADDRESS)
        self.assertEqual(queued['prefix'], 24)
        self.assertEqual(queued['gateway'], STATIC_GATEWAY)
        self.assertEqual(queued['dns'], STATIC_DNS)
        self.assertIsNone(queued['candidate']['path'])

    def test_apply_arms_timer_before_unsaved_candidate(self):
        result = self.submit(DHCP_BODY)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['interfaces'], [])
        queued = self.txn()
        self.assertEqual(queued['id'], '01' * 16)
        self.assertEqual(queued['candidate']['uuid'], '02020202-0202-0202-0202-020202020202')
        self.assertIsNone(queued['candidate']['path'])
        self.assertIsNone(queued['candidate']['settings'])
        self.assertEqual(queued['created_mono'] + HELPER.ROLLBACK_SECONDS, queued['deadline_mono'])
        self.assertEqual(queued['created'] + HELPER.ROLLBACK_SECONDS, queued['deadline'])
        self.assertEqual(queued['boot_id'], 'boot-aaa-1')
        self.assertEqual(queued['nm_owner'], ':1.50')
        self.assertEqual(result['pending']['state'], 'pending')
        self.assertEqual(result['pending']['seconds_remaining'], HELPER.ROLLBACK_SECONDS)
        starts = self.calls('StartUnit')
        self.assertEqual(starts[0].args, (HELPER.ROLLBACK_UNIT, 'replace'))
        apply_starts = [call for call in starts if call.args[0] == HELPER.APPLY_UNIT]
        self.assertEqual(len(apply_starts), 1)
        self.assertEqual(apply_starts[0].args[1], 'replace')
        self.assertLess(self.world.calls.index(starts[0]), self.world.calls.index(apply_starts[0]))
        self.assertEqual(self.calls('AddConnectionUnsaved'), [])
        self.assertEqual(self.calls('AddConnection'), [])

        start = len(self.world.calls)
        activated = self.activate()
        self.assertTrue(activated['ok'], activated)
        added = self.calls('AddConnectionUnsaved', start)
        self.assertEqual(len(added), 1)
        window = self.world.calls[start:self.world.calls.index(added[0])]
        self.assertTrue(any(
            call.method == 'StartUnit' and call.args[0] == HELPER.ROLLBACK_UNIT for call in window))
        self.assertEqual(self.calls('AddConnection'), [])
        decoded = HELPER.decode_settings(added[0].args[0], dbus)
        self.assertIs(decoded['connection']['autoconnect']['v'], False)
        self.assertEqual(decoded['ipv4']['method']['v'], 'auto')
        self.assertEqual(decoded['ipv4']['addresses']['v'], [])
        self.assertEqual(decoded['ipv4']['gateway']['v'], '')
        self.assertEqual(decoded['ipv4']['dns']['v'], [])
        activation = self.calls('ActivateConnection', start)
        self.assertEqual(len(activation), 1)
        self.assertIsInstance(activation[0].args[0], dbus.ObjectPath)
        self.assertIsInstance(activation[0].args[1], dbus.ObjectPath)
        self.assertIsInstance(activation[0].args[2], dbus.ObjectPath)
        self.assertEqual(str(activation[0].args[2]), '/')
        saved = self.txn()
        self.assertEqual(saved['state'], 'awaiting-confirmation')
        self.assertEqual(saved['address'], DHCP_LEASE[0])
        self.assertIs(self.world.connections[saved['candidate']['path']]['unsaved'], True)
        self.assertTrue(self.bus.owners)
        self.assertTrue(all(owner.startswith(':') for owner in self.bus.owners))
        self.assertEqual(activated['interfaces'][0]['address'], DHCP_LEASE[0])
        self.assertEqual(activated['interfaces'][0]['mode'], 'dhcp')
        self.assertFalse(activated['interfaces'][0]['saved'])

    def test_static_activation_and_confirmation(self):
        applied = self.submit(STATIC_BODY)
        self.assertTrue(applied['ok'], applied)
        activated = self.activate()
        self.assertTrue(activated['ok'], activated)
        saved = self.txn()
        self.assertEqual(saved['state'], 'awaiting-confirmation')
        self.assertEqual(saved['address'], STATIC_ADDRESS)
        self.assertIs(self.world.connections[saved['candidate']['path']]['unsaved'], True)
        start = len(self.world.calls)
        confirmed = self.confirm(saved['id'], STATIC_ADDRESS)
        self.assertTrue(confirmed['ok'], confirmed)
        self.assertIsNone(confirmed['pending'])
        updates = self.calls('Update', start)
        self.assertEqual(len(updates), 1)
        self.assertEqual(self.calls('UpdateUnsaved', start), [])
        decoded = HELPER.decode_settings(updates[0].args[0], dbus)
        self.assertIs(decoded['connection']['autoconnect']['v'], True)
        self.assertEqual(decoded['connection']['autoconnect-priority']['v'], 1)
        self.assertEqual(decoded['connection']['uuid']['v'], saved['candidate']['uuid'])
        finished = self.txn()
        self.assertEqual(finished['state'], 'confirmed')
        self.assertIs(self.world.connections[finished['candidate']['path']]['unsaved'], False)
        interface = confirmed['interfaces'][0]
        self.assertEqual(interface['address'], STATIC_ADDRESS)
        self.assertEqual(interface['prefix'], 24)
        self.assertEqual(interface['gateway'], STATIC_GATEWAY)
        self.assertEqual(interface['dns'], STATIC_DNS)
        self.assertEqual(interface['mode'], 'static')
        self.assertTrue(interface['saved'])

    def test_dhcp_activation_and_confirmation(self):
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        activated = self.activate()
        self.assertTrue(activated['ok'], activated)
        saved = self.txn()
        self.assertEqual(saved['address'], DHCP_LEASE[0])
        self.assertNotEqual(self.world.dhcp4_path, '/')
        confirmed = self.confirm(saved['id'], DHCP_LEASE[0])
        self.assertTrue(confirmed['ok'], confirmed)
        self.assertEqual(self.txn()['state'], 'confirmed')
        self.assertIsNone(confirmed['pending'])
        self.assertEqual(confirmed['interfaces'][0]['mode'], 'dhcp')
        self.assertEqual(confirmed['interfaces'][0]['address'], DHCP_LEASE[0])
        self.assertTrue(confirmed['interfaces'][0]['saved'])

    def owned_original(self):
        profile = original_profile()
        profile['connection']['id']['v'] = 'blakestream-1234abcd'
        self.world._replace(ORIG, HELPER.encode_settings(profile, dbus), False)

    def test_confirm_retires_only_unchanged_owned_original(self):
        self.owned_original()
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        saved = self.txn()
        self.assertTrue(self.confirm(saved['id'], DHCP_LEASE[0])['ok'])
        self.assertNotIn(ORIG, self.world.connections)
        self.assertEqual(self.txn()['original']['uuid'], ORIG_UUID)
        self.assertEqual(self.world.ac_uuid, saved['candidate']['uuid'])

    def test_confirm_preserves_external_original(self):
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        self.assertTrue(self.confirm(self.txn()['id'], DHCP_LEASE[0])['ok'])
        self.assertIn(ORIG, self.world.connections)
        self.assertEqual(self.calls('Delete'), [])

    def test_owned_original_delete_failure_keeps_rollback_available(self):
        self.owned_original()
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        saved = self.txn()
        self.world.fail.add('Delete')
        self.assertFalse(self.confirm(saved['id'], DHCP_LEASE[0])['ok'])
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)
        self.world.fail.remove('Delete')
        self.assertTrue(self.rollback(saved['id'])['ok'])
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)

    def test_rollback_recreates_retired_original_after_lost_confirmation(self):
        self.owned_original()
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        saved = self.txn()
        real_ready = self.service._confirm_ready
        calls = []
        def ready(txn, address):
            calls.append(True)
            return len(calls) < 3 and real_ready(txn, address)
        self.service._confirm_ready = ready
        self.assertFalse(self.confirm(saved['id'], DHCP_LEASE[0])['ok'])
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)
        self.assertTrue(self.calls('AddConnection'))

    def test_confirm_does_not_delete_externally_changed_owned_original(self):
        self.owned_original()
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        profile = self.world.connections[ORIG]['decoded']
        profile['connection']['id']['v'] = 'operator-renamed'
        self.world._replace(ORIG, HELPER.encode_settings(profile, dbus), False)
        self.assertFalse(self.confirm(self.txn()['id'], DHCP_LEASE[0])['ok'])
        self.assertIn(ORIG, self.world.connections)
        self.assertEqual(self.calls('Delete'), [])

    def confirmed_before_restart(self):
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        self.assertTrue(self.confirm(self.txn()['id'], DHCP_LEASE[0])['ok'])
        saved = self.txn()
        saved['nm_owner'] = ':1.older'
        self.fs.write_txn(dump(saved))
        return saved

    def test_restart_reclaims_confirmed_profile_from_unsaved_connection(self):
        saved = self.confirmed_before_restart()
        self.world._activate(ORIG, DEVICE, '/')
        self.fs.note_removed(ORIG_UUID)
        result = self.service.dispatch('restore-confirmed', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.world.ac_uuid, saved['candidate']['uuid'])
        self.assertEqual(self.txn()['nm_owner'], ':1.50')
        self.assertEqual(self.txn()['state'], 'confirmed')

    def test_restart_preserves_a_different_saved_active_profile(self):
        self.confirmed_before_restart()
        self.world._activate(ORIG, DEVICE, '/')
        start = len(self.world.calls)
        self.assertTrue(self.service.dispatch('restore-confirmed', '')['ok'])
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)
        self.assertEqual(self.calls('ActivateConnection', start), [])

    def test_restart_recovery_does_not_touch_pending_transaction(self):
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        start = len(self.world.calls)
        result = self.service.dispatch('restore-confirmed', '')
        self.assertTrue(result['ok'])
        self.assertEqual(self.world.calls[start:], [])
        self.assertEqual(self.txn()['state'], 'pending')

    def test_restart_recovery_preserves_disabled_autoconnect(self):
        saved = self.confirmed_before_restart()
        path = saved['candidate']['path']
        settings = self.world.connections[path]['decoded']
        settings['connection']['autoconnect']['v'] = False
        self.world._replace(path, HELPER.encode_settings(settings, dbus), False)
        self.world._disconnect(DEVICE)
        start = len(self.world.calls)
        self.assertTrue(self.service.dispatch('restore-confirmed', '')['ok'])
        self.assertEqual(self.calls('ActivateConnection', start), [])

    def test_restart_recovery_never_creates_missing_profile(self):
        saved = self.confirmed_before_restart()
        self.world._disconnect(DEVICE)
        self.fs.note_removed(saved['candidate']['uuid'])
        start = len(self.world.calls)
        self.assertFalse(self.service.dispatch('restore-confirmed', '')['ok'])
        self.assertEqual(self.calls('ActivateConnection', start), [])
        self.assertEqual(self.calls('AddConnection', start), [])

    def test_restart_recovery_does_not_reactivate_without_new_daemon(self):
        self.confirmed_before_restart()
        saved = self.txn()
        saved['nm_owner'] = ':1.50'
        self.fs.write_txn(dump(saved))
        self.world._disconnect(DEVICE)
        start = len(self.world.calls)
        self.assertTrue(self.service.dispatch('restore-confirmed', '')['ok'])
        self.assertEqual(self.calls('ActivateConnection', start), [])

    def test_boot_change_recovers_even_when_dbus_owner_name_repeats(self):
        saved = self.confirmed_before_restart()
        saved['nm_owner'] = ':1.50'
        self.fs.write_txn(dump(saved))
        self.fs.boot_id = 'boot-bbb-2\n'
        self.world._disconnect(DEVICE)
        self.assertTrue(self.service.dispatch('restore-confirmed', '')['ok'])
        self.assertEqual(self.world.ac_uuid, saved['candidate']['uuid'])
        self.assertEqual(self.txn()['nm_owner_boot'], 'boot-bbb-2')

    def test_live_static_and_dhcp_mismatch_rolls_back(self):
        self.world.lie_gateway = '203.0.113.1'
        self.assertTrue(self.submit(STATIC_BODY)['ok'])
        result = self.activate()
        self.assertFalse(result['ok'], result)
        self.assertIn('activation failed:', result['error'])
        self.assertIn('timed out waiting for the network change', result['error'])
        self.assertEqual(self.slept.count(HELPER.POLL_INTERVAL), HELPER.POLL_ATTEMPTS)
        saved = self.txn()
        self.assertEqual(saved['state'], 'rolled_back')
        self.assertNotIn(saved['candidate']['path'], self.world.connections)
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)

        self._build(False)
        self.world.suppress_dhcp4 = True
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        result = self.activate()
        self.assertFalse(result['ok'], result)
        self.assertIn('timed out waiting for the network change', result['error'])
        self.assertEqual(self.slept.count(HELPER.POLL_INTERVAL), HELPER.POLL_ATTEMPTS)
        saved = self.txn()
        self.assertEqual(saved['state'], 'rolled_back')
        self.assertNotIn(saved['candidate']['path'], self.world.connections)
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)
        self.assertEqual(self.world.dhcp4_path, DHCP4)

    def test_explicit_rollback_restores_active_original(self):
        self.assertTrue(self.submit(STATIC_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        candidate = self.txn()['candidate']['path']
        start = len(self.world.calls)
        result = self.rollback(self.txn()['id'])
        self.assertTrue(result['ok'], result)
        self.assertIsNone(result['pending'])
        self.assertEqual(self.txn()['state'], 'rolled_back')
        later = self.calls(start=start)
        activated = [call for call in later if call.method == 'ActivateConnection']
        self.assertEqual(len(activated), 1)
        self.assertEqual(str(activated[0].args[0]), ORIG)
        self.assertFalse(any(call.method == 'Disconnect' for call in later))
        self.assertNotIn(candidate, self.world.connections)
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)
        self.assertEqual(result['interfaces'][0]['address'], ORIG_LEASE[0])
        self.assertEqual(result['interfaces'][0]['mode'], 'dhcp')

    def test_disconnected_original_stays_disconnected(self):
        self._build(True)
        applied = self.submit(DHCP_BODY)
        self.assertTrue(applied['ok'], applied)
        self.assertFalse(self.txn()['original']['active'])
        self.assertTrue(self.activate()['ok'])
        start = len(self.world.calls)
        result = self.rollback(self.txn()['id'])
        self.assertTrue(result['ok'], result)
        later = self.calls(start=start)
        self.assertFalse(any(call.method == 'ActivateConnection' for call in later))
        self.assertTrue(any(call.method == 'Disconnect' and call.path == DEVICE for call in later))
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertIn(ORIG, self.world.connections)
        self.assertEqual(self.world.active_path, '/')
        self.assertNotEqual(self.world.state, HELPER.DEVICE_STATE_ACTIVATED)
        self.assertIsNone(result['interfaces'][0]['address'])
        self.assertIsNone(result['interfaces'][0]['mode'])

    def test_failed_uuid_lookup_is_not_absence(self):
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.world.fail.add('ListConnections')
        denied = self.rollback(self.txn()['id'])
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.assertEqual(denied['pending']['state'], 'rollback-failed')
        self.assertNotIn('rollback-failed', HELPER.TERMINAL_STATES)
        self.assertFalse(self.txn()['last_result']['ok'])
        self.assertEqual(self.calls('AddConnection'), [])
        self.assertEqual(self.calls('AddConnectionUnsaved'), [])
        self.assertEqual(self.world.order, [ORIG])

        self._build(False)
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.world.fail.add('GetSettings')
        denied = self.rollback(self.txn()['id'])
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.assertIsNotNone(denied['pending'])
        self.assertEqual(self.calls('AddConnection'), [])
        self.assertEqual(self.world.order, [ORIG])

        self._build(False)
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        candidate = self.txn()['candidate']['path']
        self.world.fail_after['ListConnections'] = 1
        denied = self.rollback(self.txn()['id'])
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.assertEqual(denied['pending']['state'], 'rollback-failed')
        self.assertIn(candidate, self.world.connections)
        self.assertEqual(self.calls('AddConnection'), [])

        self._build(False)
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        del self.world.connections[ORIG]
        self.world.order.remove(ORIG)
        restored = self.rollback(self.txn()['id'])
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertIsNone(restored['pending'])
        added = self.calls('AddConnection')
        self.assertEqual(len(added), 1)
        self.assertEqual(self.calls('AddConnectionUnsaved'), [])
        self.assertEqual(HELPER.decode_settings(added[0].args[0], dbus)['connection']['uuid']['v'], ORIG_UUID)

    def test_stale_candidate_path_cannot_update_another_uuid(self):
        self.assertTrue(self.submit(STATIC_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        saved = self.txn()
        real = saved['candidate']['path']
        original_encoded = self.world.connections[ORIG]['encoded']
        saved['candidate']['path'] = ORIG
        self.fs.write_txn(dump(saved))
        start = len(self.world.calls)
        confirmed = self.confirm(saved['id'], STATIC_ADDRESS)
        self.assertTrue(confirmed['ok'], confirmed)
        updates = self.calls('Update', start)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].path, real)
        self.assertNotEqual(updates[0].path, ORIG)
        self.assertEqual(self.calls('UpdateUnsaved', start), [])
        decoded = HELPER.decode_settings(updates[0].args[0], dbus)
        self.assertEqual(decoded['connection']['uuid']['v'], saved['candidate']['uuid'])
        self.assertIs(decoded['connection']['autoconnect']['v'], True)
        self.assertIs(self.world.connections[ORIG]['encoded'], original_encoded)
        original = self.world.connections[ORIG]['decoded']
        self.assertEqual(original['connection']['uuid']['v'], ORIG_UUID)
        self.assertEqual(original['ipv4']['method']['v'], 'auto')
        self.assertEqual(self.txn()['state'], 'confirmed')

    def test_missing_or_changed_boot_id_expires(self):
        for boot_id in (None, '###', 'boot-bbb-2'):
            self._build(False)
            self.assertTrue(self.submit(DHCP_BODY)['ok'])
            self.fs.boot_id = boot_id
            start = len(self.world.calls)
            self.assert_expired(self.activate(), start)

    def test_backward_clocks_expire_safely(self):
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        saved = self.txn()
        self.clock.wall = saved['created']
        self.clock.mono = saved['created_mono'] - 1
        start = len(self.world.calls)
        self.assert_expired(self.activate(), start)

        self._build(False)
        self.assertTrue(self.submit(DHCP_BODY)['ok'])
        saved = self.txn()
        self.clock.wall = saved['created'] - 100000
        self.clock.mono = saved['created_mono'] + 30
        self.assertLess(self.clock.wall, saved['deadline'])
        ticked = self.tick()
        self.assertTrue(ticked['ok'], ticked)
        self.assertEqual(ticked['pending']['state'], 'pending')
        self.assertEqual(ticked['pending']['id'], saved['id'])
        self.assertEqual(ticked['pending']['seconds_remaining'], HELPER.ROLLBACK_SECONDS - 30)
        self.assertNotEqual(ticked['pending']['seconds_remaining'], saved['deadline'] - self.clock.wall)
        self.assertEqual(self.txn()['state'], 'pending')
        self.clock.mono = saved['deadline_mono']
        self.assertLess(self.clock.wall, self.txn()['deadline'])
        start = len(self.world.calls)
        self.assert_expired(self.activate(), start)

    def test_lock_contention_leaves_the_pending_transaction(self):
        applied = self.submit(DHCP_BODY)
        self.assertTrue(applied['ok'], applied)
        ident = applied['pending']['id']
        text = self.fs.text
        self.fs.held = True
        start = len(self.world.calls)
        ticked = self.tick()
        self.assertTrue(ticked['ok'], ticked)
        self.assertEqual(ticked['pending']['id'], ident)
        self.assertEqual(ticked['pending']['state'], 'pending')
        self.assertEqual(self.fs.text, text)
        self.assertEqual(self.calls(start=start), [])
        blocked = self.submit(STATIC_BODY)
        self.assertFalse(blocked['ok'], blocked)
        self.assertIn('busy', blocked['error'])
        self.assertNotIn('already in progress', blocked['error'])
        self.assertEqual(self.fs.text, text)
        self.assertEqual(self.calls(start=start), [])

    def test_rollback_retry_and_failure_remain_pending(self):
        self.assertTrue(self.submit(STATIC_BODY)['ok'])
        self.assertTrue(self.activate()['ok'])
        self.assertEqual(self.txn()['state'], 'awaiting-confirmation')
        candidate = self.txn()['candidate']['path']
        ident = self.txn()['id']
        self.world.fail.add('Delete')
        failed = self.rollback(ident)
        self.assertFalse(failed['ok'], failed)
        self.assertEqual(failed['pending']['id'], ident)
        self.assertEqual(failed['pending']['state'], 'rollback-failed')
        self.assertNotIn('rollback-failed', HELPER.TERMINAL_STATES)
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.assertIn(candidate, self.world.connections)
        self.assertEqual(self.world.ac_uuid, ORIG_UUID)
        retried = self.tick()
        self.assertFalse(retried['ok'], retried)
        self.assertEqual(retried['pending']['state'], 'rollback-failed')
        self.assertEqual(retried['pending']['id'], ident)
        self.assertEqual(self.txn()['state'], 'rollback-failed')
        self.assertIn(candidate, self.world.connections)
        self.world.fail.discard('Delete')
        restored = self.tick()
        self.assertTrue(restored['ok'], restored)
        self.assertIsNone(restored['pending'])
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertTrue(self.txn()['last_result']['ok'])
        self.assertEqual(self.txn()['last_result']['action'], 'tick')
        self.assertNotIn(candidate, self.world.connections)
        self.assertIn(ORIG, self.world.connections)
        self.assertEqual(restored['interfaces'][0]['address'], ORIG_LEASE[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
