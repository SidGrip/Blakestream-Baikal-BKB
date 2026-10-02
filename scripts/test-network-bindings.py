#!/usr/bin/env python3
"""Real dbus-python types and wire marshalling, with no bus or network access."""
import importlib.util
import os
import socket
import struct
import unittest

try:
    import dbus
    import dbus.lowlevel
except ImportError:
    raise SystemExit('missing dbus-python: run with an interpreter providing python3-dbus')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location('network_helper', os.path.join(
    ROOT, 'overlay', 'opt', 'scripta', 'bin', 'network-settings.py'))
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def wire(address):
    return dbus.UInt32(struct.unpack('=I', socket.inet_aton(address))[0])


def address(value):
    return socket.inet_ntoa(struct.pack('=I', int(value)))


def fixture():
    return dbus.Dictionary({
        'connection': dbus.Dictionary({
            'id': dbus.String('QA Ethernet', variant_level=1),
            'uuid': dbus.String('12345678-1234-1234-1234-123456789abc', variant_level=1),
            'type': dbus.String('802-3-ethernet', variant_level=1),
            'interface-name': dbus.String('eth0', variant_level=1),
            'autoconnect': dbus.Boolean(True, variant_level=1),
            'autoconnect-priority': dbus.Int32(0, variant_level=1),
            'timestamp': dbus.UInt64(1700000000, variant_level=1),
        }, signature='sv'),
        '802-3-ethernet': dbus.Dictionary({
            'mac-address': dbus.ByteArray(b'\x02\x00\x00\x00\x00\x01', variant_level=1),
        }, signature='sv'),
        'ipv4': dbus.Dictionary({
            'method': dbus.String('manual', variant_level=1),
            'addresses': dbus.Array([dbus.Array([
                wire('192.0.2.10'), dbus.UInt32(24), wire('192.0.2.1')
            ], signature='u')], signature='au', variant_level=1),
            'address-data': dbus.Array([dbus.Dictionary({
                'address': dbus.String('192.0.2.10', variant_level=1),
                'prefix': dbus.UInt32(24, variant_level=1)
            }, signature='sv')], signature='a{sv}', variant_level=1),
            'gateway': dbus.String('192.0.2.1', variant_level=1),
            'dns': dbus.Array([wire('192.0.2.53')], signature='u', variant_level=1),
            'dns-search': dbus.Array(['example.test'], signature='s', variant_level=1),
            'routes': dbus.Array([], signature='au', variant_level=1),
        }, signature='sv'),
        'ipv6': dbus.Dictionary({
            'method': dbus.String('auto', variant_level=1),
            'addresses': dbus.Array([], signature='(ayuay)', variant_level=1),
            'routes': dbus.Array([], signature='(ayuayu)', variant_level=1),
        }, signature='sv'),
    }, signature='sa{sv}')


def marshalled(settings):
    message = dbus.lowlevel.MethodCallMessage(
        'org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager/Settings',
        'org.freedesktop.NetworkManager.Settings', 'AddConnectionUnsaved')
    message.append(settings, signature='a{sa{sv}}')
    return message.get_args_list(byte_arrays=True)[0]


class NetworkBindings(unittest.TestCase):
    def test_fixture_uses_valid_real_wire_types(self):
        self.assertEqual(marshalled(fixture()), fixture())

    def test_real_variant_and_nested_container_roundtrip(self):
        original = fixture()
        decoded = HELPER.decode_settings(original, dbus)
        encoded = HELPER.encode_settings(decoded, dbus)
        result = marshalled(encoded)
        self.assertEqual(result, original)
        self.assertEqual(result['ipv4']['addresses'].signature, 'au')
        self.assertEqual(result['ipv4']['addresses'][0].signature, 'u')
        self.assertEqual(result['ipv4']['method'].variant_level, 1)
        self.assertEqual(HELPER.decode_settings(result, dbus), decoded)

    def test_real_manual_profile_is_supported(self):
        decoded = HELPER.decode_settings(fixture(), dbus)
        HELPER._validate_profile(decoded, 'eth0', '02:00:00:00:00:01')
        endpoint = HELPER._manual_endpoint(decoded)
        self.assertEqual(endpoint['address'], '192.0.2.10')
        self.assertEqual(endpoint['gateway'], '192.0.2.1')
        self.assertEqual(endpoint['dns'], ['192.0.2.53'])

    def test_static_candidate_uses_network_order_and_aau(self):
        decoded = HELPER.decode_settings(fixture(), dbus)
        requested = {'mode': 'static', 'address': '198.51.100.24', 'prefix': 24,
                     'gateway': '198.51.100.1', 'dns': ['198.51.100.53']}
        candidate = HELPER._candidate_settings(decoded, 'a' * 32,
            '22345678-1234-1234-1234-123456789abc', 'eth0', requested)
        value = marshalled(HELPER.encode_settings(candidate, dbus))['ipv4']
        self.assertEqual(value['addresses'].signature, 'au')
        row = value['addresses'][0]
        self.assertEqual(address(row[0]), requested['address'])
        self.assertEqual(int(row[1]), 24)
        self.assertEqual(address(row[2]), requested['gateway'])
        self.assertEqual(address(value['dns'][0]), requested['dns'][0])
        self.assertEqual(value['address-data'][0]['address'], requested['address'])

    def test_dhcp_has_no_fixed_address_gateway_or_dns(self):
        decoded = HELPER.decode_settings(fixture(), dbus)
        candidate = HELPER._candidate_settings(decoded, 'a' * 32,
            '22345678-1234-1234-1234-123456789abc', 'eth0', {'mode': 'dhcp'})
        value = marshalled(HELPER.encode_settings(candidate, dbus))['ipv4']
        self.assertEqual(value['method'], 'auto')
        self.assertFalse(value['addresses'])
        self.assertFalse(value['address-data'])
        self.assertFalse(value['gateway'])
        self.assertFalse(value['dns'])
        self.assertFalse(value['ignore-auto-dns'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
