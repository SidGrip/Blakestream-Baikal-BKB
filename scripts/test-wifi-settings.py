#!/usr/bin/env python3
"""Offline Wi-Fi helper tests. Real dbus-python types; fake transport, filesystem, and clocks."""
from __future__ import print_function

import importlib.util
import io
import json
import os
import tempfile
import unittest

try:
    import dbus
except ImportError:
    raise SystemExit('missing dbus-python: run with an interpreter providing python3-dbus')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    'wifi_helper', os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin', 'wifi-settings.py'))
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)

def _refuse_real_bus(*_args, **_kwargs):
    raise AssertionError('refusing a real bus')


dbus.SystemBus = _refuse_real_bus
dbus.SessionBus = _refuse_real_bus

ETH_DEVICE = '/org/freedesktop/NetworkManager/Devices/1'
WIFI_DEVICE = '/org/freedesktop/NetworkManager/Devices/2'
ETH_ACTIVE = '/org/freedesktop/NetworkManager/ActiveConnection/1'
WIFI_ACTIVE = '/org/freedesktop/NetworkManager/ActiveConnection/2'
ETH_CONN = '/org/freedesktop/NetworkManager/Settings/1'
WIFI_CONN = '/org/freedesktop/NetworkManager/Settings/2'
IP4 = '/org/freedesktop/NetworkManager/IP4Config/1'
TIMER = '/org/freedesktop/systemd1/unit/rollback'
APPLY_PATH = '/org/freedesktop/systemd1/unit/apply'
UNIT_IFACE = 'org.freedesktop.systemd1.Unit'
ETH_UUID = '12345678-1234-1234-1234-123456789abc'
WIFI_UUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
OTHER_UUID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
OTHER_CONN = '/org/freedesktop/NetworkManager/Settings/8'
PSK = 'secretpass'
SSID = 'LabNet'
DERIVED = HELPER.derived_psk(SSID.encode('utf-8'), PSK)


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def ethernet_profile():
    return {
        'connection': {
            'id': {'sig': 's', 'v': 'QA Ethernet'},
            'uuid': {'sig': 's', 'v': ETH_UUID},
            'type': {'sig': 's', 'v': '802-3-ethernet'},
            'interface-name': {'sig': 's', 'v': 'eth0'},
            'autoconnect': {'sig': 'b', 'v': True},
            'autoconnect-priority': {'sig': 'i', 'v': 0},
        },
        '802-3-ethernet': {},
        'ipv4': {'method': {'sig': 's', 'v': 'auto'}},
        'ipv6': {'method': {'sig': 's', 'v': 'auto'}},
    }


def wifi_public(uuid, ssid, security, ident='QA Wi-Fi'):
    settings = {
        'connection': {
            'id': {'sig': 's', 'v': ident},
            'uuid': {'sig': 's', 'v': uuid},
            'type': {'sig': 's', 'v': '802-11-wireless'},
            'interface-name': {'sig': 's', 'v': 'wlan0'},
            'autoconnect': {'sig': 'b', 'v': True},
            'autoconnect-priority': {'sig': 'i', 'v': -10},
        },
        '802-11-wireless': {
            'ssid': {'sig': 'ay', 'v': list(ssid.encode('utf-8'))},
            'mode': {'sig': 's', 'v': 'infrastructure'},
            'hidden': {'sig': 'b', 'v': False},
        },
        'ipv4': {'method': {'sig': 's', 'v': 'auto'}},
        'ipv6': {'method': {'sig': 's', 'v': 'auto'}},
    }
    if security == 'wpa-psk':
        settings['802-11-wireless']['security'] = {'sig': 's', 'v': '802-11-wireless-security'}
        settings['802-11-wireless-security'] = {'key-mgmt': {'sig': 's', 'v': 'wpa-psk'}}
    return HELPER.net.encode_settings(settings, dbus)


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
        self.named = {}
        self.boot_id = 'boot-aaa-1\n'
        self.held = False
        self.saved = set([ETH_UUID, WIFI_UUID])
        self.wifi_profiles = []
        self.profiles_dir = '/etc/NetworkManager/system-connections'

    def ensure_state_dir(self):
        return None

    def profile_saved(self, uuid):
        return uuid in self.saved

    def note_saved(self, uuid):
        self.saved.add(uuid)

    def note_removed(self, uuid):
        self.saved.discard(uuid)

    def wifi_keyfiles(self):
        return list(self.wifi_profiles)

    def try_lock(self):
        if self.held:
            raise HELPER.net.NetError('network settings are busy')
        self.held = True
        return _Lock(self)

    def read_txn(self):
        return self.text

    def write_txn(self, text):
        if PSK in text:
            raise AssertionError('password written to the journal')
        self.text = text

    def read_named(self, name):
        if name == 'transaction.json':
            return self.text
        return self.named.get(name)

    def write_named(self, name, text):
        if name == 'secret':
            if len(text) != 64 or any(c not in '0123456789abcdef' for c in text):
                raise AssertionError('raw passphrase written to the handoff')
            self.named[name] = text
            return
        if PSK in text:
            raise AssertionError('password written to ' + name)
        if name == 'transaction.json':
            self.text = text
            return
        self.named[name] = text

    def unlink_named(self, name):
        self.named.pop(name, None)

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
        block = bytes([self.count]) * size
        self.count += 1
        return block


class World(object):
    def __init__(self, fs, wifi=True):
        self.fs = fs
        self.wifi = wifi
        self.calls = []
        self.next_id = 10
        self.wireless_enabled = False
        self.wireless_hardware = True
        self.fail_activate = False
        self.unrelated_on_fail = False
        self.fail_nm = False
        self.scan_error = None
        self.activate_device_state = 100
        self.activate_ac_state = 2
        self.wifi_ac_state = 2
        self.last_settings = None
        self.psk_on_wire = []
        self.secrets = {}
        self.units = {TIMER: 'inactive', APPLY_PATH: 'inactive'}
        self.aps = {
            '/ap/open': {'ssid': b'OpenNet', 'flags': 0, 'wpa': 0, 'rsn': 0, 'strength': 70},
            '/ap/psk': {'ssid': b'LabNet', 'flags': 1, 'wpa': 0, 'rsn': 0x188, 'strength': 80},
            '/ap/ent': {'ssid': b'Corp', 'flags': 1, 'wpa': 0, 'rsn': 0x200, 'strength': 60},
            '/ap/wep': {'ssid': b'OldWEP', 'flags': 1, 'wpa': 0, 'rsn': 0, 'strength': 40},
            '/ap/sae': {'ssid': b'WPA3Net', 'flags': 1, 'wpa': 0, 'rsn': 0x400, 'strength': 50},
        }
        self.connections = {
            ETH_CONN: {
                'encoded': HELPER.net.encode_settings(ethernet_profile(), dbus),
                'uuid': ETH_UUID,
                'kind': '802-3-ethernet',
                'unsaved': False,
            },
            WIFI_CONN: {
                'encoded': wifi_public(WIFI_UUID, 'OldNet', 'wpa-psk'),
                'uuid': WIFI_UUID,
                'kind': '802-11-wireless',
                'ssid': 'OldNet',
                'unsaved': False,
            },
            OTHER_CONN: {
                'encoded': wifi_public(OTHER_UUID, 'OtherNet', 'wpa-psk'),
                'uuid': OTHER_UUID,
                'kind': '802-11-wireless',
                'ssid': 'OtherNet',
                'unsaved': False,
            },
        }
        self.order = [ETH_CONN, WIFI_CONN, OTHER_CONN]
        self.eth_state = 100
        self.eth_active = ETH_ACTIVE
        self.eth_uuid = ETH_UUID
        self.eth_connection = ETH_CONN
        self.wifi_state = 30
        self.wifi_active = '/'
        self.wifi_uuid = None
        self.wifi_connection = None
        self.wifi_ap = '/'
        self.managed = True
        self.firmware_missing = False

    def dispatch(self, owner, path, iface, method, args):
        self.calls.append(Call(owner, str(path), iface, method, args))
        return self._handle(str(path), iface, method, args)

    def _handle(self, path, iface, method, args):
        if self.fail_nm and path.startswith('/org/freedesktop/NetworkManager'):
            raise dbus.DBusException('org.freedesktop.DBus.Error.NoReply')
        if method == 'GetDevices' and path == HELPER.NM_PATH:
            devices = [dbus.ObjectPath(ETH_DEVICE)]
            if self.wifi:
                devices.append(dbus.ObjectPath(WIFI_DEVICE))
            return dbus.Array(devices, signature='o')
        if method == 'Get' and iface == HELPER.DBUS_PROPS:
            return self._property(path, args[0], args[1])
        if method == 'Set' and iface == HELPER.DBUS_PROPS:
            return self._set(path, args[0], args[1], args[2])
        if method == 'GetSettings':
            return dbus.Dictionary({group: dbus.Dictionary(dict(values), signature='sv')
                                    for group, values in self.connections[path]['encoded'].items()},
                                   signature='sa{sv}')
        if method == 'GetSecrets':
            return dbus.Dictionary({group: dbus.Dictionary(dict(values), signature='sv')
                                    for group, values in self.secrets.get(path, {}).items()},
                                   signature='sa{sv}')
        if method == 'ListConnections' and path == HELPER.NM_SETTINGS_PATH:
            return dbus.Array([dbus.ObjectPath(item) for item in self.order], signature='o')
        if method == 'AddConnectionUnsaved':
            return self._add(args[0], True)
        if method == 'AddConnection':
            return self._add(args[0], False)
        if method == 'Update':
            return self._replace(path, args[0], False)
        if method == 'Delete':
            return self._delete(path)
        if method == 'ActivateConnection':
            return self._activate(args[0], args[1], args[2])
        if method == 'Disconnect':
            return self._disconnect(path)
        if method == 'RequestScan':
            if self.scan_error:
                raise dbus.DBusException(self.scan_error)
            return None
        if method == 'GetAccessPoints':
            return dbus.Array([dbus.ObjectPath(item) for item in self.aps], signature='o')
        if method == 'StartUnit':
            path_unit = self._unit_path(args[0])
            if args[0] == HELPER.ROLLBACK_UNIT:
                self.units[path_unit] = 'active'
            return dbus.ObjectPath('/org/freedesktop/systemd1/job/1')
        if method in ('GetUnit', 'LoadUnit'):
            return dbus.ObjectPath(self._unit_path(args[0]))
        raise dbus.DBusException('unhandled {0} {1}'.format(method, path))

    def _set(self, path, iface, name, value):
        if name == 'NetworkingEnabled':
            raise AssertionError('must not set NetworkingEnabled')
        if path == HELPER.NM_PATH and name == 'WirelessEnabled':
            self.wireless_enabled = bool(value)
            return None
        raise AssertionError('unexpected Set {0}'.format(name))

    def _property(self, path, iface, name):
        if path == HELPER.NM_PATH and iface == HELPER.NM_IFACE:
            if name == 'WirelessEnabled':
                return dbus.Boolean(self.wireless_enabled)
            if name == 'WirelessHardwareEnabled':
                return dbus.Boolean(self.wireless_hardware)
        if path == ETH_DEVICE and iface == HELPER.NM_DEVICE_IFACE:
            return self._eth_device(name)
        if path == WIFI_DEVICE and iface == HELPER.NM_DEVICE_IFACE:
            return self._wifi_device_prop(name)
        if path == WIFI_DEVICE and iface == HELPER.NM_WIFI_IFACE:
            if name == 'ActiveAccessPoint':
                return dbus.ObjectPath(self.wifi_ap)
        if path == ETH_ACTIVE and iface == HELPER.NM_ACTIVE_IFACE:
            return self._active(name, self.eth_uuid, self.eth_connection)
        if path == WIFI_ACTIVE and iface == HELPER.NM_ACTIVE_IFACE:
            return self._active(name, self.wifi_uuid, self.wifi_connection, self.wifi_ac_state)
        if path == IP4 and iface == HELPER.NM_IP4_IFACE:
            if name == 'AddressData':
                return dbus.Array([dbus.Dictionary({
                    'address': dbus.String('192.0.2.10', variant_level=1),
                    'prefix': dbus.UInt32(24, variant_level=1),
                }, signature='sv')], signature='a{sv}')
            if name == 'Gateway':
                return dbus.String('192.0.2.1')
            if name == 'Nameservers':
                return dbus.Array([], signature='u')
        if path in self.connections and iface == HELPER.NM_CONNECTION_IFACE and name == 'Unsaved':
            return dbus.Boolean(self.connections[path]['unsaved'])
        if iface == UNIT_IFACE and name == 'ActiveState' and path in self.units:
            return dbus.String(self.units[path])
        if path in self.aps and iface == HELPER.NM_AP_IFACE:
            ap = self.aps[path]
            if name == 'Ssid':
                return dbus.ByteArray(ap['ssid'])
            if name == 'Flags':
                return dbus.UInt32(ap['flags'])
            if name == 'WpaFlags':
                return dbus.UInt32(ap['wpa'])
            if name == 'RsnFlags':
                return dbus.UInt32(ap['rsn'])
            if name == 'Strength':
                return dbus.Byte(ap['strength'])
            if name == 'LastSeen':
                return dbus.Int32(ap.get('last_seen', int(self.clock.mono)))
        raise dbus.DBusException('unknown property {0} {1}'.format(iface, name))

    def _eth_device(self, name):
        if name == 'DeviceType':
            return dbus.UInt32(HELPER.DEVICE_TYPE_ETHERNET)
        if name == 'Interface':
            return dbus.String('eth0')
        if name == 'Managed':
            return dbus.Boolean(True)
        if name == 'State':
            return dbus.UInt32(self.eth_state)
        if name == 'ActiveConnection':
            return dbus.ObjectPath(self.eth_active)
        if name == 'Ip4Config':
            return dbus.ObjectPath(IP4)
        if name == 'AvailableConnections':
            return dbus.Array([dbus.ObjectPath(ETH_CONN)], signature='o')
        raise dbus.DBusException(name)

    def _wifi_device_prop(self, name):
        if name == 'DeviceType':
            return dbus.UInt32(HELPER.DEVICE_TYPE_WIFI)
        if name == 'Interface':
            return dbus.String('wlan0')
        if name == 'Managed':
            return dbus.Boolean(self.managed)
        if name == 'FirmwareMissing':
            return dbus.Boolean(self.firmware_missing)
        if name == 'Driver':
            return dbus.String('brcmfmac')
        if name == 'State':
            return dbus.UInt32(self.wifi_state)
        if name == 'ActiveConnection':
            return dbus.ObjectPath(self.wifi_active)
        if name == 'Ip4Config':
            return dbus.ObjectPath('/')
        raise dbus.DBusException(name)

    def _active(self, name, uuid, connection, state=2):
        if name == 'State':
            return dbus.UInt32(state)
        if name == 'Uuid':
            return dbus.String(uuid or '')
        if name == 'Connection':
            return dbus.ObjectPath(connection or '/')
        if name == 'Ip4Config':
            return dbus.ObjectPath('/')
        if name == 'Dhcp4Config':
            return dbus.ObjectPath('/')
        raise dbus.DBusException(name)

    def _add(self, settings, unsaved):
        kind = str(settings['connection']['type'])
        if kind == '802-3-ethernet':
            raise AssertionError('must not add an Ethernet profile')
        self.last_settings = settings
        uuid = str(settings['connection']['uuid'])
        ssid = bytes(settings['802-11-wireless']['ssid']).decode('utf-8')
        sec = settings.get('802-11-wireless-security')
        if sec is not None and 'psk' in sec:
            psk = str(sec['psk'])
            if psk == PSK:
                self.psk_on_wire.append(psk)
            else:
                self.psk_on_wire.append(psk)
        path = '/org/freedesktop/NetworkManager/Settings/{0}'.format(self.next_id)
        self.next_id += 1
        security = 'wpa-psk' if sec is not None else 'open'
        self.connections[path] = {
            'encoded': wifi_public(uuid, ssid, security, ident=str(settings['connection']['id'])),
            'uuid': uuid,
            'kind': '802-11-wireless',
            'ssid': ssid,
            'unsaved': unsaved,
        }
        self.order.append(path)
        if sec is not None and 'psk' in sec:
            self.secrets[path] = {'802-11-wireless-security': {'psk':sec['psk']}}
        if not unsaved:
            self.fs.note_saved(uuid)
        return dbus.ObjectPath(path)

    def _replace(self, path, settings, unsaved):
        if path == ETH_CONN:
            raise AssertionError('must not update Ethernet')
        self.last_settings = settings
        uuid = str(settings['connection']['uuid'])
        ssid = bytes(settings['802-11-wireless']['ssid']).decode('utf-8')
        sec = settings.get('802-11-wireless-security')
        if sec is not None and 'psk' in sec:
            self.psk_on_wire.append(str(sec['psk']))
            self.secrets[path] = {'802-11-wireless-security': {'psk':sec['psk']}}
        security = 'wpa-psk' if sec is not None else 'open'
        self.connections[path]['encoded'] = wifi_public(uuid, ssid, security, ident=str(settings['connection']['id']))
        self.connections[path]['unsaved'] = unsaved
        self.connections[path]['ssid'] = ssid
        if not unsaved:
            self.fs.note_saved(uuid)
        return None

    def _delete(self, path):
        if path == ETH_CONN:
            raise AssertionError('must not delete Ethernet')
        uuid = self.connections[path]['uuid']
        self.order.remove(path)
        del self.connections[path]
        self.fs.note_removed(uuid)
        return None

    def _activate(self, connection, device, specific):
        if str(device) == ETH_DEVICE:
            raise AssertionError('must not activate Ethernet')
        if str(device) != WIFI_DEVICE or str(specific) != '/':
            raise dbus.DBusException('unexpected activation target')
        if self.fail_activate:
            self.fail_activate = False
            if self.unrelated_on_fail:
                self.wifi_connection = OTHER_CONN
                self.wifi_uuid = OTHER_UUID
                self.wifi_active = WIFI_ACTIVE
                self.wifi_state = 100
                self.wifi_ac_state = 2
                self.aps['/ap/other'] = {'ssid': b'OtherNet', 'flags': 1, 'wpa': 0, 'rsn': 0x188, 'strength': 40}
                self.wifi_ap = '/ap/other'
            else:
                self.wifi_active = '/'
                self.wifi_state = 30
                self.wifi_uuid = None
                self.wifi_connection = None
                self.wifi_ap = '/'
                self.wifi_ac_state = 2
            return dbus.ObjectPath('/org/freedesktop/NetworkManager/ActiveConnection/99')
        connection = str(connection)
        uuid = self.connections[connection]['uuid']
        ssid = self.connections[connection].get('ssid') or 'LabNet'
        security = 'open' if ssid == 'OpenNet' else 'wpa-psk'
        self.wifi_connection = connection
        self.wifi_uuid = uuid
        self.wifi_active = WIFI_ACTIVE
        self.wifi_state = self.activate_device_state
        self.wifi_ac_state = self.activate_ac_state
        ap_path = '/ap/active'
        flags = 1 if security == 'wpa-psk' else 0
        rsn = 0x188 if security == 'wpa-psk' else 0
        self.aps[ap_path] = {'ssid': ssid.encode('utf-8'), 'flags': flags, 'wpa': 0, 'rsn': rsn, 'strength': 50}
        self.wifi_ap = ap_path
        return dbus.ObjectPath(WIFI_ACTIVE)

    def _disconnect(self, path):
        if path == ETH_DEVICE:
            raise AssertionError('must not disconnect Ethernet')
        if path != WIFI_DEVICE:
            raise dbus.DBusException('unexpected disconnect')
        self.wifi_active = '/'
        self.wifi_state = 30
        self.wifi_uuid = None
        self.wifi_connection = None
        self.wifi_ap = '/'
        return None

    def _unit_path(self, name):
        if name == HELPER.ROLLBACK_UNIT:
            return TIMER
        if name == HELPER.APPLY_UNIT:
            return APPLY_PATH
        raise dbus.DBusException(name)


class ClassifyTests(unittest.TestCase):
    def test_open_psk_and_unsupported(self):
        self.assertEqual(HELPER.classify_ap(0, 0, 0), 'open')
        self.assertEqual(HELPER.classify_ap(1, 0, 0x188), 'wpa-psk')
        self.assertEqual(HELPER.classify_ap(1, 0, 0x200), 'unsupported')
        self.assertEqual(HELPER.classify_ap(1, 0, 0), 'unsupported')
        self.assertEqual(HELPER.classify_ap(1, 0, 0x400), 'unsupported')


class EncodeTests(unittest.TestCase):
    def test_wpa_standard_vector_and_already_derived_key(self):
        expected = 'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e'
        self.assertEqual(HELPER.derived_psk(b'IEEE', 'password'), expected)
        self.assertEqual(HELPER.derived_psk(b'IEEE', expected.upper()), expected)
        self.assertNotEqual(HELPER.derived_psk(b' IEEE ', 'password'), expected)
        for value in ('x' * 7, 'x' * 65, '\ud800' * 8, '\u00e9' * 32):
            with self.assertRaises(HELPER.WifiError):
                HELPER.derived_psk(b'IEEE', value)

    def test_route_metric_is_dbus_int64_on_ipv4_and_ipv6(self):
        spec = {
            'id': 'blakestream-wifi-test',
            'uuid': WIFI_UUID,
            'interface': 'wlan0',
            'ssid': b'LabNet',
            'hidden': False,
            'security': 'wpa-psk',
        }
        encoded = HELPER.encode_wifi(spec, dbus, PSK)
        self.assertEqual(str(encoded['802-11-wireless-security']['psk']), DERIVED)
        for family in ('ipv4', 'ipv6'):
            metric = encoded[family]['route-metric']
            self.assertIsInstance(metric, dbus.Int64, family)
            self.assertNotIsInstance(metric, dbus.UInt32, family)
            self.assertEqual(type(metric), dbus.Int64, family)
            self.assertEqual(int(metric), HELPER.WIFI_METRIC, family)
            self.assertEqual(metric.variant_level, 1, family)


class ParseTests(unittest.TestCase):
    def test_unsafe_and_unsupported_inputs(self):
        rejects = (
            (dump({'ssid': 'Net', 'security': 'wpa-psk', 'psk': PSK, 'command': 'rm'}), 'unknown setting'),
            (dump({'ssid': 'Net', 'security': 'wep', 'psk': PSK}), 'unsupported security'),
            (dump({'ssid': 'Net', 'security': 'sae', 'psk': PSK}), 'unsupported security'),
            (dump({'ssid': 'Net', 'security': 'wpa-eap', 'psk': PSK}), 'unsupported security'),
            (dump({'ssid': 'Net', 'security': 'open', 'psk': PSK}), 'open networks do not accept a password'),
            (dump({'ssid': 'Net', 'security': 'wpa-psk'}), 'password required'),
            (dump({'ssid': '', 'security': 'open'}), 'invalid SSID'),
            (dump({'ssid': 'n' * 33, 'security': 'open'}), 'invalid SSID'),
            (dump({'ssid': '\u6d4b' * 11, 'security': 'open'}), 'invalid SSID'),
            (dump({'ssid': 'Net\x00x', 'security': 'open'}), 'invalid SSID'),
            (dump({'ssid': 'Net', 'security': 'wpa-psk', 'psk': 'short'}), 'invalid password'),
        )
        for raw, error in rejects:
            with self.assertRaises(HELPER.WifiError) as caught:
                HELPER._parse_connect(raw)
            self.assertEqual(str(caught.exception), error)
            self.assertNotIn(PSK, str(caught.exception))

    def test_ssid_keeps_spaces_and_uses_utf8_byte_length(self):
        spaced = HELPER._parse_connect(dump({'ssid': ' LabNet ', 'security': 'open'}))
        self.assertEqual(spaced['ssid_text'], ' LabNet ')
        self.assertEqual(spaced['ssid'], b' LabNet ')
        ok_utf8 = HELPER._parse_connect(dump({'ssid': '\u6d4b' * 10, 'security': 'open'}))
        self.assertEqual(len(ok_utf8['ssid']), 30)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.temp.name, 'wifi')
        self.nm_dir = os.path.join(self.temp.name, 'nm')
        os.mkdir(self.nm_dir)
        self.nm_state = os.path.join(self.nm_dir, 'NetworkManager.state')
        self.profiles = os.path.join(self.temp.name, 'connections')
        os.mkdir(self.profiles)
        self.fs = HELPER.WifiFS(self.state, require_root=False, profiles_dir=self.profiles)
        self.service = HELPER.Service(
            None, None, self.fs, lambda: 1000, lambda delay: None, os.urandom, lambda: 0,
            nm_state_path=self.nm_state)

    def test_handoff_refuses_plaintext(self):
        self.fs.ensure_state_dir()
        with self.assertRaises(HELPER.net.NetError):
            self.fs.write_named('secret', PSK)
        self.fs.write_named('secret', DERIVED)
        self.assertEqual(self.fs.read_named('secret'), DERIVED.encode('ascii'))

    def tearDown(self):
        self.temp.cleanup()

    def radio(self):
        raw = self.fs.read_named('radio.json')
        return json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)

    def nm_wireless(self):
        parsed, _text = HELPER.read_nm_state_wireless(self.nm_state)
        return parsed

    def test_fresh_image_defaults_radio_off_and_keeps_ethernet_networking(self):
        with open(self.nm_state, 'w') as handle:
            handle.write('[main]\nNetworkingEnabled=true\nWirelessEnabled=true\nWWANEnabled=true\n')
        result = self.service.dispatch('provision', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.radio()['enabled'], False)
        self.assertEqual(self.radio()['source'], 'default')
        parsed = self.nm_wireless()
        self.assertIs(parsed['wireless'], False)
        self.assertIs(parsed['networking'], True)

    def test_existing_radio_choice_is_not_replaced_on_upgrade(self):
        self.fs.ensure_state_dir()
        self.fs.write_named('radio.json', dump({'v': 1, 'enabled': True, 'source': 'user'}))
        with open(self.nm_state, 'w') as handle:
            handle.write('[main]\nNetworkingEnabled=true\nWirelessEnabled=false\n')
        result = self.service.dispatch('provision', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.radio()['enabled'], True)
        self.assertEqual(self.radio()['source'], 'user')
        self.assertFalse(result.get('created'))
        self.assertIs(self.nm_wireless()['wireless'], True)
        self.assertIs(self.nm_wireless()['networking'], True)

    def test_preexisting_wifi_profile_is_preserved_when_no_radio_json(self):
        with open(os.path.join(self.profiles, 'old'), 'w') as handle:
            handle.write('[connection]\ntype=802-11-wireless\nuuid=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n')
        with open(self.nm_state, 'w') as handle:
            handle.write('[main]\nNetworkingEnabled=true\nWirelessEnabled=true\n')
        result = self.service.dispatch('provision', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.radio()['enabled'], True)
        self.assertEqual(self.radio()['source'], 'user')

    def test_ethernet_keyfile_does_not_count_as_wifi_use(self):
        with open(os.path.join(self.profiles, 'wired'), 'w') as handle:
            handle.write('[connection]\ntype=802-3-ethernet\nuuid=12345678-1234-1234-1234-123456789abc\n')
        result = self.service.dispatch('provision', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.radio()['enabled'], False)

    def test_provision_never_fails_closed_on_missing_nm_dir(self):
        missing = os.path.join(self.temp.name, 'missing', 'NetworkManager.state')
        self.service.nm_state_path = missing
        result = self.service.dispatch('provision', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.radio()['enabled'], False)


class WifiTransactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.nm_dir = os.path.join(self.temp.name, 'nm')
        os.mkdir(self.nm_dir)
        self.nm_state = os.path.join(self.nm_dir, 'NetworkManager.state')
        with open(self.nm_state, 'w') as handle:
            handle.write('[main]\nNetworkingEnabled=true\nWirelessEnabled=false\n')
        self._build(True)

    def tearDown(self):
        self.temp.cleanup()

    def _build(self, wifi):
        self.clock = Clock()
        self.fs = MemoryFS()
        self.world = World(self.fs, wifi=wifi)
        self.world.clock = self.clock
        self.bus = Bus(self.world)
        self.service = HELPER.Service(
            self.bus, dbus, self.fs, self.clock, lambda delay: None, URandom(), lambda: 0,
            grace=0, monotonic=self.clock.monotonic, nm_state_path=self.nm_state,
            boottime=self.clock.monotonic)

    def txn(self):
        return json.loads(self.fs.text)

    def methods(self, start=0):
        return [call.method for call in self.world.calls[start:]]

    def sets(self):
        return [call.args[1] for call in self.world.calls if call.method == 'Set']

    def radio_on(self, wait=True):
        result = self.service.dispatch('radio', dump({'enabled': True}))
        self.assertTrue(result['ok'], result)
        self.assertTrue(self.world.wireless_enabled)
        if wait:
            self.clock.mono += HELPER.SCAN_COOLDOWN_SECONDS
        return result

    def test_no_adapter(self):
        self._build(False)
        result = self.service.dispatch('status', '')
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['adapter']['present'])
        self.assertFalse(self.service.dispatch('scan', '{}')['ok'])
        self.assertIn('no supported Wi-Fi adapter', self.service.dispatch('scan', '{}')['error'])
        self.assertEqual(self.world.eth_uuid, ETH_UUID)

    def test_fresh_status_is_radio_off_until_user_enables(self):
        self.service.dispatch('provision', '')
        status = self.service.dispatch('status', '')
        self.assertFalse(status['radio']['enabled'])
        self.assertFalse(self.world.wireless_enabled)
        scan = self.service.dispatch('scan', '{}')
        self.assertFalse(scan['ok'])
        self.assertEqual(scan['error'], 'Wi-Fi radio is off')
        connect = self.service.dispatch('connect', dump({'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK}))
        self.assertFalse(connect['ok'])
        self.assertEqual(connect['error'], 'Wi-Fi radio is off')
        self.assertNotIn('Set', self.methods())
        self.assertNotIn('RequestScan', self.methods())
        self.assertNotIn('AddConnectionUnsaved', self.methods())
        self.assertEqual(self.world.eth_uuid, ETH_UUID)

    def test_scan_and_connect_do_not_silently_enable_radio(self):
        self.fs.write_named('radio.json', dump({'v': 1, 'enabled': False, 'source': 'user'}))
        self.world.wireless_enabled = False
        self.service.dispatch('scan', '{}')
        self.service.dispatch('connect', dump({'ssid': SSID, 'security': 'open'}))
        self.assertEqual(self.sets(), [])
        self.assertFalse(self.world.wireless_enabled)

    def test_hardware_blocked_radio_rejects_mutations_and_leaves_ethernet(self):
        self.radio_on()
        self.world.wireless_hardware = False
        scan = self.service.dispatch('scan', '{}')
        self.assertFalse(scan['ok'])
        self.assertIn('blocked by hardware', scan['error'])
        connect = self.service.dispatch('connect', dump({'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK}))
        self.assertFalse(connect['ok'])
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertEqual(self.world.eth_state, 100)
        self.assertNotIn('NetworkingEnabled', self.sets())

    def test_radio_off_on_persists_and_does_not_touch_ethernet(self):
        off = self.service.dispatch('provision', '')
        self.assertFalse(off['radio']['enabled'])
        on = self.radio_on()
        self.assertTrue(on['radio']['enabled'])
        self.assertEqual(json.loads(self.fs.named['radio.json'])['source'], 'user')
        parsed, text = HELPER.read_nm_state_wireless(self.nm_state)
        self.assertIs(parsed['wireless'], True)
        self.assertIs(parsed['networking'], True)
        self.assertIn('NetworkingEnabled=true', text.replace(' ', ''))
        off2 = self.service.dispatch('radio', dump({'enabled': False}))
        self.assertTrue(off2['ok'], off2)
        self.assertFalse(self.world.wireless_enabled)
        self.assertFalse(json.loads(self.fs.named['radio.json'])['enabled'])
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertNotIn('NetworkingEnabled', self.sets())
        self.assertNotIn('Disconnect', [call.method for call in self.world.calls if call.path == ETH_DEVICE])

    def test_unsupported_visible_security_is_rejected(self):
        self.radio_on()
        for ssid, security in (('Corp', 'wpa-psk'), ('OldWEP', 'wpa-psk'), ('WPA3Net', 'wpa-psk')):
            result = self.service.dispatch('connect', dump({'ssid': ssid, 'security': security, 'psk': PSK}))
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['error'], 'unsupported security')
        scan = self.service.dispatch('scan', '{}')
        self.assertTrue(scan['ok'], scan)
        names = {item['ssid']: item for item in scan['networks']}
        self.assertTrue(names['LabNet']['supported'])
        self.assertFalse(names['Corp']['supported'])
        self.assertFalse(names['OldWEP']['supported'])
        self.assertFalse(names['WPA3Net']['supported'])
        self.assertNotIn(PSK, dump(scan))

    def test_scan_cooldown_is_shared_with_status_and_expires(self):
        self.radio_on()
        scan = self.service.dispatch('scan', '{}')
        self.assertTrue(scan['ok'], scan)
        self.assertEqual(scan['scan_retry_after'], 15)
        self.assertFalse(scan['scan_deferred'])
        self.clock.wall += 3600
        self.clock.mono += 5
        status = self.service.dispatch('status', '')
        self.assertEqual(status['scan_retry_after'], 10)
        deferred = self.service.dispatch('scan', '{}')
        self.assertTrue(deferred['ok'], deferred)
        self.assertTrue(deferred['scan_deferred'])
        self.assertEqual(deferred['networks'], scan['networks'])
        self.assertEqual(deferred['scan_retry_after'], 10)
        self.assertEqual(self.methods().count('RequestScan'), 1)
        self.clock.mono += 10
        self.assertEqual(self.service.dispatch('status', '')['scan_retry_after'], 0)
        self.assertFalse(self.service.dispatch('scan', '{}')['scan_deferred'])
        self.assertEqual(self.methods().count('RequestScan'), 2)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)

    def test_radio_enable_defers_scan_and_publishes_automatic_results(self):
        self.world.wifi_state = 20
        enabled = self.radio_on(wait=False)
        self.assertEqual(enabled['scan_retry_after'], 15)
        scan = self.service.dispatch('scan', '{}')
        self.assertTrue(scan['ok'], scan)
        self.assertTrue(scan['scan_deferred'])
        self.assertNotIn('RequestScan', self.methods())
        self.clock.mono += 5
        self.world.wifi_state = 30
        status = self.service.dispatch('status', '')
        self.assertTrue(status['networks'])
        self.assertEqual(status['scan_retry_after'], 10)
        self.assertNotIn('RequestScan', self.methods(), 'status reads automatic scan results only')
        self.clock.mono += 10
        scan = self.service.dispatch('scan', '{}')
        self.assertFalse(scan['scan_deferred'])
        self.assertEqual(self.methods().count('RequestScan'), 1)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)

    def test_scan_drops_stale_aps_and_status_does_not_restore_them(self):
        self.radio_on()
        self.world.aps['/ap/open']['last_seen'] = int(self.clock.mono) - 30
        self.world.aps['/ap/ent']['last_seen'] = -1
        result = self.service.dispatch('scan', '{}')
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['scan_deferred'])
        self.assertNotIn('OpenNet', [r['ssid'] for r in result['networks']])
        self.assertNotIn('Corp', [r['ssid'] for r in result['networks']])
        self.assertEqual(result['networks'], self.service.dispatch('status', '')['networks'])
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertNotIn('Disconnect', self.methods())

    def test_scan_waits_for_fresh_aps_instead_of_nonempty_cache(self):
        self.radio_on()
        self.world.aps = {'/ap/psk': self.world.aps['/ap/psk']}
        self.world.aps['/ap/psk']['last_seen'] = int(self.clock.mono) - 20
        waits = []
        def finish_scan(delay):
            waits.append(delay)
            if len(waits) == 2:
                self.world.aps['/ap/psk']['last_seen'] = int(self.clock.mono)
        self.service.sleep = finish_scan
        result = self.service.dispatch('scan', '{}')
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['scan_deferred'])
        self.assertEqual(len(waits), 2)
        self.assertEqual(result['networks'][0]['ssid'], 'LabNet')

    def test_empty_scan_replaces_previous_list_without_erasing_saved_profiles(self):
        self.radio_on()
        self.assertTrue(self.service.dispatch('scan', '{}')['networks'])
        self.clock.mono += 15
        self.world.aps = {}
        before = list(self.world.order)
        result = self.service.dispatch('scan', '{}')
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['scan_deferred'], 'an accepted scan can return no networks')
        self.assertEqual(result['networks'], [])
        self.assertEqual(self.service.dispatch('status', '')['networks'], [])
        self.assertEqual(self.world.order, before)

    def test_late_scan_updates_status_without_requesting_another_scan(self):
        self.radio_on()
        self.world.aps = {'/ap/psk': self.world.aps['/ap/psk']}
        self.world.aps['/ap/psk']['last_seen'] = int(self.clock.mono) - 20
        result = self.service.dispatch('scan', '{}')
        self.assertEqual(result['networks'], [])
        self.assertFalse(result['scan_deferred'])
        self.world.aps['/ap/psk']['last_seen'] = int(self.clock.mono)
        status = self.service.dispatch('status', '')
        self.assertEqual(status['networks'][0]['ssid'], 'LabNet')
        self.assertEqual(self.methods().count('RequestScan'), 1)

    def test_stale_stronger_duplicate_cannot_hide_fresh_ap(self):
        self.radio_on()
        self.world.aps['/ap/stale'] = dict(self.world.aps['/ap/psk'],
            strength=100, last_seen=int(self.clock.mono) - 1)
        result = self.service.dispatch('scan', '{}')
        lab = next(r for r in result['networks'] if r['ssid'] == 'LabNet')
        self.assertEqual(lab['strength'], 80)

    def test_scan_freshness_is_boot_scoped_and_independent_of_ntp(self):
        self.radio_on()
        self.world.aps['/ap/open']['last_seen'] = int(self.clock.mono) - 30
        self.service.dispatch('scan', '{}')
        self.clock.wall -= 1000000
        self.assertNotIn('OpenNet', [r['ssid'] for r in self.service.dispatch('status', '')['networks']])
        data = json.loads(self.fs.named['scan.json'])
        data['boot_id'] = '00000000-0000-0000-0000-000000000000'
        self.fs.write_named('scan.json', dump(data))
        self.assertIsNone(self.service._scan_seen_since())
        self.assertEqual(self.service._read_scan(), [])

    def test_unavailable_or_activating_adapter_does_not_request_scan(self):
        self.radio_on()
        for state in (0, 20, 40, 50, 60, 70, 80, 90, 110):
            self.world.wifi_state = state
            scan = self.service.dispatch('scan', '{}')
            self.assertTrue(scan['ok'], scan)
            self.assertTrue(scan['scan_deferred'])
            self.assertEqual(scan['adapter']['state'], state)
            self.assertEqual(scan['scan_retry_after'], 0, 'unavailable hardware does not reset the timer')
        self.assertNotIn('RequestScan', self.methods())
        for state in HELPER.SCAN_READY_STATES:
            self.world.wifi_state = state
            self.assertFalse(self.service.dispatch('scan', '{}')['scan_deferred'])
            self.clock.mono += 15

    def test_scan_cooldown_ignores_old_boot_and_legacy_cache(self):
        self.radio_on()
        self.service.dispatch('scan', '{}')
        data = json.loads(self.fs.named['scan.json'])
        data['boot_id'] = '00000000-0000-0000-0000-000000000000'
        self.fs.write_named('scan.json', dump(data))
        self.assertEqual(self.service.dispatch('status', '')['scan_retry_after'], 0)
        del data['scanned_mono']
        self.fs.write_named('scan.json', dump(data))
        self.assertEqual(self.service.dispatch('status', '')['scan_retry_after'], 0)

    def test_busy_scan_returns_results_and_retry_without_raw_error(self):
        self.radio_on()
        for reason in ('immediately following previous scan', 'while already scanning',
                       'while unavailable', 'while unavailable or activating', 'at this time'):
            self.world.scan_error = ('org.freedesktop.NetworkManager.Device.NotAllowed: '
                                     'Scanning not allowed ' + reason)
            result = self.service.dispatch('scan', '{}')
            self.assertTrue(result['ok'], result)
            self.assertIsNone(result['error'])
            self.assertTrue(result['scan_deferred'])
            self.assertEqual(result['scan_retry_after'], 15)
            self.assertTrue(result['networks'])
            self.clock.mono += 15
        self.world.aps = {}
        cached = self.service.dispatch('scan', '{}')
        self.assertEqual(cached['networks'], [], 'empty live list does not resurrect cached networks')
        self.assertEqual(self.world.eth_uuid, ETH_UUID)

    def test_scan_does_not_hide_unrelated_networkmanager_errors(self):
        self.radio_on()
        self.world.scan_error = 'org.freedesktop.DBus.Error.AccessDenied: denied'
        result = self.service.dispatch('scan', '{}')
        self.assertFalse(result['ok'])
        self.assertIn('AccessDenied', result['error'])
        self.assertEqual(self.service.dispatch('status', '')['scan_retry_after'], 0)

    def test_failed_connection_restores_previous_wifi_and_ethernet(self):
        self.radio_on()
        self.world.wifi_active = WIFI_ACTIVE
        self.world.wifi_state = 100
        self.world.wifi_uuid = WIFI_UUID
        self.world.wifi_connection = WIFI_CONN
        self.world.wifi_ap = '/ap/old'
        self.world.aps['/ap/old'] = {'ssid': b'OldNet', 'flags': 1, 'wpa': 0, 'rsn': 0x188, 'strength': 40}
        self.world.fail_activate = True
        queued = self.service.dispatch('connect', dump({'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK}))
        self.assertTrue(queued['ok'], queued)
        self.assertNotIn(PSK, dump(queued))
        self.assertNotIn(PSK, self.fs.text)
        failed = self.service.dispatch('activate', '')
        self.assertFalse(failed['ok'], failed)
        self.assertNotIn(PSK, dump(failed))
        self.assertNotIn('secret', self.fs.named)
        self.assertEqual(self.world.wifi_uuid, WIFI_UUID)
        self.assertEqual(self.world.wifi_state, 100)
        self.assertEqual(self.world.wifi_ac_state, 2)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertEqual(self.world.eth_state, 100)
        self.assertTrue(any(item.method == 'Delete' for item in self.world.calls))
        self.assertNotIn(ETH_CONN, [item.path for item in self.world.calls if item.method == 'Delete'])
        self.assertIsNotNone(failed.get('last_result'))
        self.assertFalse(failed['last_result']['ok'])
        self.assertIn('Wi-Fi connection failed', failed['last_result']['error'])
        status = self.service.dispatch('status', '')
        self.assertTrue(status['ok'], status)
        self.assertFalse(status['last_result']['ok'])
        self.assertIn('Wi-Fi connection failed', status['last_result']['error'])
        self.assertIsInstance(self.world.last_settings['ipv4']['route-metric'], dbus.Int64)
        self.assertIsInstance(self.world.last_settings['ipv6']['route-metric'], dbus.Int64)
        self.assertEqual(type(self.world.last_settings['ipv4']['route-metric']), dbus.Int64)
        self.assertEqual(type(self.world.last_settings['ipv6']['route-metric']), dbus.Int64)

    def test_success_saves_profile_redacts_password_and_preserves_ethernet(self):
        self.radio_on()
        queued = self.service.dispatch('connect', dump({
            'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK, 'hidden': False
        }))
        self.assertTrue(queued['ok'], queued)
        self.assertEqual(queued['pending']['ssid'], SSID)
        self.assertIn('secret', self.fs.named)
        self.assertEqual(self.fs.named['secret'], DERIVED)
        self.assertNotIn(PSK, self.fs.text)
        activated = self.service.dispatch('activate', '')
        self.assertTrue(activated['ok'], activated)
        self.assertIsNone(activated['pending'])
        self.assertEqual(activated['connection']['ssid'], SSID)
        self.assertTrue(activated['ethernet_connected'])
        self.assertNotIn(PSK, dump(activated))
        self.assertNotIn('secret', self.fs.named)
        self.assertTrue(self.world.psk_on_wire)
        self.assertTrue(all(key == DERIVED for key in self.world.psk_on_wire))
        self.assertNotIn(DERIVED, dump(activated))
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertEqual(self.world.eth_state, 100)
        self.assertNotIn('NetworkingEnabled', self.sets())
        self.assertIn('Update', self.methods())
        self.assertNotIn(ETH_DEVICE, [item.path for item in self.world.calls if item.method in ('Disconnect', 'Delete')])
        saved = json.loads(self.fs.text)
        self.assertEqual(saved['state'], 'connected')
        self.assertNotIn(PSK, dump(saved))
        metric4 = self.world.last_settings['ipv4']['route-metric']
        metric6 = self.world.last_settings['ipv6']['route-metric']
        self.assertIsInstance(metric4, dbus.Int64)
        self.assertIsInstance(metric6, dbus.Int64)
        self.assertEqual(type(metric4), dbus.Int64)
        self.assertEqual(type(metric6), dbus.Int64)
        self.assertNotIsInstance(metric4, dbus.UInt32)
        self.assertEqual(int(metric4), 600)
        self.assertEqual(int(metric6), 600)
        self.assertEqual(metric4.variant_level, 1)
        self.assertEqual(metric6.variant_level, 1)

    def test_reconnect_uses_saved_profile_without_reading_or_rewriting_secrets(self):
        self.radio_on()
        before = dict(self.world.connections[WIFI_CONN]['encoded'])
        queued = self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))
        self.assertTrue(queued['ok'], queued)
        self.assertEqual(queued['pending']['ssid'], 'OldNet')
        self.assertNotIn('secret', self.fs.named)
        self.assertTrue(self.txn()['candidate']['saved'])
        repeated = self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))
        self.assertFalse(repeated['ok'], repeated)
        activated = self.service.dispatch('activate', '')
        self.assertTrue(activated['ok'], activated)
        self.assertEqual(activated['connection']['uuid'], WIFI_UUID)
        self.assertEqual(self.world.connections[WIFI_CONN]['encoded'], before)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        for method in ('GetSecrets', 'AddConnectionUnsaved', 'Update', 'Delete'):
            self.assertNotIn(method, self.methods())
        self.assertTrue(self.service.dispatch('disconnect', '{}')['ok'])
        self.assertTrue(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        self.assertTrue(self.service.dispatch('activate', '')['ok'])
        self.assertEqual(self.world.wifi_uuid, WIFI_UUID)

    def test_reconnect_open_hidden_profile_preserves_settings(self):
        self.radio_on()
        profile = self.world.connections[WIFI_CONN]
        profile['encoded'] = wifi_public(WIFI_UUID, 'OpenNet', 'open')
        profile['encoded']['802-11-wireless']['hidden'] = dbus.Boolean(True, variant_level=1)
        profile['ssid'] = 'OpenNet'
        queued = self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))
        self.assertTrue(queued['ok'], queued)
        self.assertTrue(self.txn()['hidden'])
        self.assertTrue(self.service.dispatch('activate', '')['ok'])
        self.assertNotIn('Update', self.methods())
        self.assertNotIn('GetSecrets', self.methods())

    def test_reconnect_rejects_invalid_unknown_ethernet_and_mixed_requests(self):
        self.radio_on()
        for body in ({'uuid': 'bad'}, {'uuid': ETH_UUID},
                     {'uuid': 'cccccccc-cccc-cccc-cccc-cccccccccccc'},
                     {'uuid': WIFI_UUID, 'psk': PSK}, {'uuid': WIFI_UUID, 'ssid': SSID}):
            result = self.service.dispatch('connect', dump(body))
            self.assertFalse(result['ok'], result)
        self.assertNotIn('ActivateConnection', self.methods())
        self.assertIsNone(self.fs.text)

    def test_reconnect_requires_saved_supported_infrastructure_profile(self):
        self.radio_on()
        self.fs.saved.discard(WIFI_UUID)
        self.assertFalse(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        self.fs.saved.add(WIFI_UUID)
        profile = self.world.connections[WIFI_CONN]['encoded']
        for key in ('none', 'wpa-eap', 'sae'):
            profile['802-11-wireless-security']['key-mgmt'] = dbus.String(key, variant_level=1)
            self.assertFalse(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        profile['802-11-wireless-security']['key-mgmt'] = dbus.String('wpa-psk', variant_level=1)
        profile['802-11-wireless']['mode'] = dbus.String('ap', variant_level=1)
        self.assertFalse(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        self.assertNotIn('ActivateConnection', self.methods())

    def test_failed_reconnect_preserves_saved_profile_and_restores_original(self):
        self.radio_on()
        self.world._activate(WIFI_CONN, WIFI_DEVICE, '/')
        self.fs.saved.add(OTHER_UUID)
        self.world.fail_activate = True
        self.assertTrue(self.service.dispatch('connect', dump({'uuid': OTHER_UUID}))['ok'])
        failed = self.service.dispatch('activate', '')
        self.assertFalse(failed['ok'], failed)
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertEqual(self.world.wifi_uuid, WIFI_UUID)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertIn(OTHER_CONN, self.world.connections)
        self.assertIn(OTHER_UUID, self.fs.saved)
        self.assertNotIn('Delete', self.methods())
        self.assertNotIn('Update', self.methods())

    def test_reconnect_failure_while_disconnected_keeps_saved_network(self):
        self.radio_on()
        self.world.fail_activate = True
        self.assertTrue(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        self.assertFalse(self.service.dispatch('activate', '')['ok'])
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertIsNone(self.world.wifi_uuid)
        self.assertIn(WIFI_CONN, self.world.connections)
        self.assertNotIn('Delete', self.methods())

    def test_reconnect_revalidates_changed_profile_before_activation(self):
        self.radio_on()
        self.assertTrue(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        self.world.connections[WIFI_CONN]['encoded'] = wifi_public(WIFI_UUID, 'ChangedNet', 'wpa-psk')
        failed = self.service.dispatch('activate', '')
        self.assertFalse(failed['ok'], failed)
        self.assertIn('changed', failed['error'])
        self.assertNotIn('ActivateConnection', self.methods())
        self.assertNotIn('Delete', self.methods())

    def test_expired_reconnect_does_not_delete_saved_network(self):
        self.radio_on()
        self.assertTrue(self.service.dispatch('connect', dump({'uuid': WIFI_UUID}))['ok'])
        self.clock.mono += HELPER.ROLLBACK_SECONDS + 1
        result = self.service.dispatch('tick', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.txn()['state'], 'rolled_back')
        self.assertIn(WIFI_CONN, self.world.connections)
        self.assertNotIn('Delete', self.methods())

    def test_legacy_handoff_is_converted_before_networkmanager(self):
        self.radio_on()
        self.service.dispatch('connect', dump({'ssid':SSID, 'security':'wpa-psk', 'psk':PSK}))
        self.fs.named['secret'] = PSK
        activated = self.service.dispatch('activate', '')
        self.assertTrue(activated['ok'], activated)
        self.assertNotIn('secret', self.fs.named)
        self.assertTrue(all(key == DERIVED for key in self.world.psk_on_wire))

    def test_password_can_match_public_ssid_or_schema_words(self):
        for password in ('interface', 'candidate', 'HomeNet123'):
            self.tearDown()
            self.setUp()
            self.radio_on()
            queued = self.service.dispatch('connect', dump({
                'ssid': 'HomeNet123', 'security': 'wpa-psk', 'psk': password, 'hidden': True
            }))
            self.assertTrue(queued['ok'], queued)
            saved = json.loads(self.fs.text)
            self.assertEqual(saved['ssid'], 'HomeNet123')
            self.assertEqual(self.fs.named['secret'], HELPER.derived_psk(b'HomeNet123', password))
            self.assertNotIn('psk', saved)

    def test_migrate_saved_psk_preserves_profile_and_connection(self):
        self.world.secrets[WIFI_CONN] = {'802-11-wireless-security':{'psk':dbus.String(PSK, variant_level=1)}}
        self.world.connections[WIFI_CONN]['encoded']['802-11-wireless']['mtu'] = dbus.UInt32(1400, variant_level=1)
        self.world.wifi_active = WIFI_ACTIVE
        self.world.wifi_state = 100
        self.world.wifi_uuid = WIFI_UUID
        self.world.wifi_connection = WIFI_CONN
        original_radio = self.world.wireless_enabled
        result = self.service.dispatch('migrate-secrets', '')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['migrated_profiles'], 1)
        expected = HELPER.derived_psk(b'OldNet', PSK)
        self.assertEqual(str(self.world.secrets[WIFI_CONN]['802-11-wireless-security']['psk']), expected)
        self.assertEqual(int(self.world.last_settings['802-11-wireless']['mtu']), 1400)
        self.assertNotIn('ActivateConnection', self.methods())
        self.assertNotIn('Disconnect', self.methods())
        self.assertEqual(self.world.eth_uuid, ETH_UUID)
        self.assertEqual(self.world.wifi_uuid, WIFI_UUID)
        self.assertEqual(self.world.wireless_enabled, original_radio)
        again = self.service.dispatch('migrate-secrets', '')
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['migrated_profiles'], 0)

    def test_key_is_redacted_when_networkmanager_echoes_it_on_failure(self):
        self.radio_on()
        self.service.dispatch('connect', dump({'ssid':SSID, 'security':'wpa-psk', 'psk':PSK}))
        original = self.world._add
        def fail(settings, unsaved):
            raise dbus.DBusException('invalid credential ' + str(settings['802-11-wireless-security']['psk']))
        self.world._add = fail
        failed = self.service.dispatch('activate', '')
        self.assertFalse(failed['ok'])
        self.assertNotIn(PSK, dump(failed))
        self.assertNotIn(DERIVED, dump(failed))
        self.assertNotIn(DERIVED, self.fs.text)
        self.world._add = original

    def test_password_stays_off_argv_and_status(self):
        stdin = io.StringIO(dump({'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK}))
        stdout = io.StringIO()
        self.fs.write_named('radio.json', dump({'v': 1, 'enabled': False, 'source': 'default'}))
        code = HELPER.main(['wifi-settings.py', 'connect'], stdin, stdout, service=self.service)
        self.assertEqual(code, 1)
        self.assertNotIn(PSK, stdout.getvalue())
        self.assertNotIn(PSK, ' '.join(['wifi-settings.py', 'connect']))

    def test_forget_rejects_ethernet_uuid(self):
        self.radio_on()
        result = self.service.dispatch('forget', dump({'uuid': ETH_UUID}))
        self.assertFalse(result['ok'])
        self.assertIn('unknown Wi-Fi network', result['error'])
        self.assertIn(ETH_CONN, self.world.connections)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)

    def test_status_uses_observed_radio_not_persisted_off(self):
        self.fs.write_named('radio.json', dump({'v': 1, 'enabled': False, 'source': 'user'}))
        self.world.wireless_enabled = True
        status = self.service.dispatch('status', '')
        self.assertTrue(status['ok'], status)
        self.assertTrue(status['radio']['enabled'])
        self.assertTrue(status['adapter']['present'])
        self.assertFalse(json.loads(self.fs.named['radio.json'])['enabled'])

    def test_nm_error_fails_closed_not_absent_success(self):
        self.radio_on()
        self.world.fail_nm = True
        status = self.service.dispatch('status', '')
        self.assertFalse(status['ok'], status)
        self.assertIn('NetworkManager', status['error'])
        self.assertFalse(status['adapter']['present'])
        self.assertFalse(status['ok'] and not status['adapter']['present'])

    def test_restore_requires_device_and_connection_activated(self):
        self.radio_on()
        self.world.wifi_active = WIFI_ACTIVE
        self.world.wifi_state = 100
        self.world.wifi_uuid = WIFI_UUID
        self.world.wifi_connection = WIFI_CONN
        self.world.wifi_ac_state = 2
        self.world.fail_activate = True
        self.world.activate_device_state = 40
        self.world.activate_ac_state = 1
        queued = self.service.dispatch('connect', dump({'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK}))
        self.assertTrue(queued['ok'], queued)
        failed = self.service.dispatch('activate', '')
        self.assertFalse(failed['ok'], failed)
        text = (failed.get('error') or '') + dump(failed.get('last_result') or {})
        self.assertIn('previous Wi-Fi network did not return', text)
        self.assertEqual(self.world.wifi_uuid, WIFI_UUID)
        self.assertNotEqual(self.world.wifi_state, 100)

    def test_restore_preserves_unrelated_active_wifi(self):
        self.radio_on()
        self.world.wifi_active = WIFI_ACTIVE
        self.world.wifi_state = 100
        self.world.wifi_uuid = WIFI_UUID
        self.world.wifi_connection = WIFI_CONN
        self.world.wifi_ac_state = 2
        self.world.fail_activate = True
        self.world.unrelated_on_fail = True
        queued = self.service.dispatch('connect', dump({'ssid': SSID, 'security': 'wpa-psk', 'psk': PSK}))
        self.assertTrue(queued['ok'], queued)
        failed = self.service.dispatch('activate', '')
        self.assertFalse(failed['ok'], failed)
        self.assertEqual(self.world.wifi_uuid, OTHER_UUID)
        original_activates = [
            call for call in self.world.calls
            if call.method == 'ActivateConnection' and str(call.args[0]) == WIFI_CONN
        ]
        self.assertEqual(original_activates, [])
        self.assertIn(OTHER_CONN, self.world.connections)
        self.assertEqual(self.world.eth_uuid, ETH_UUID)


class PackagingTests(unittest.TestCase):
    def test_enable_order_and_narrow_sudoers(self):
        overlay = os.path.join(ROOT, 'overlay')
        with open(os.path.join(overlay, 'etc/systemd/system/blakestream-wifi-default.service')) as handle:
            default = handle.read()
        self.assertIn('Before=NetworkManager.service', default)
        self.assertIn('After=local-fs.target', default)
        self.assertIn('WantedBy=NetworkManager.service', default)
        self.assertIn('wifi-settings.py provision', default)
        self.assertNotIn('Enable/order', default)
        self.assertNotIn('Packaging', default)
        with open(os.path.join(overlay, 'etc/systemd/system/blakestream-wifi-radio.service')) as handle:
            radio = handle.read()
        self.assertIn('After=NetworkManager.service', radio)
        self.assertIn('wifi-settings.py sync-radio', radio)
        with open(os.path.join(overlay, 'etc/systemd/system/NetworkManager.service.d/blakestream-wifi-recovery.conf')) as handle:
            dropin = handle.read()
        self.assertIn('blakestream-wifi-radio.service', dropin)
        self.assertNotIn('NetworkingEnabled', dropin)
        with open(os.path.join(overlay, 'etc/sudoers.d/blakestream-wifi')) as handle:
            sudoers = handle.read()
        self.assertNotIn('NOPASSWD: ALL', sudoers)
        for verb in ('activate', 'tick', 'provision', 'sync-radio'):
            self.assertNotIn('wifi-settings.py ' + verb, sudoers)
        wants = os.path.join(overlay, 'etc/systemd/system/NetworkManager.service.wants/blakestream-wifi-default.service')
        self.assertTrue(os.path.islink(wants))


if __name__ == '__main__':
    unittest.main(verbosity=2)
