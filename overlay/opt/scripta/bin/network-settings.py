#!/usr/bin/python3
"""Root-only NetworkManager helper for Settings Network.

Target runtime is Python 3.5 with dbus-python and NetworkManager 1.2.6.
NM 1.2.6 has no CheckpointCreate, so an unconfirmed change is a transaction
under /var/lib/blakestream-network plus a systemd timer. On 1.2.6, a present
ipv4.addresses value overrides address-data and gateway, so static writes keep
those forms in agreement.
"""
from __future__ import print_function

import binascii
import configparser
import errno
import fcntl
import json
import os
import signal
import socket
import stat
import struct
import sys
import time

NM_SERVICE = 'org.freedesktop.NetworkManager'
NM_PATH = '/org/freedesktop/NetworkManager'
NM_IFACE = 'org.freedesktop.NetworkManager'
NM_SETTINGS_PATH = '/org/freedesktop/NetworkManager/Settings'
NM_SETTINGS_IFACE = 'org.freedesktop.NetworkManager.Settings'
NM_CONNECTION_IFACE = 'org.freedesktop.NetworkManager.Settings.Connection'
NM_DEVICE_IFACE = 'org.freedesktop.NetworkManager.Device'
NM_DEVICE_WIRED_IFACE = 'org.freedesktop.NetworkManager.Device.Wired'
NM_ACTIVE_IFACE = 'org.freedesktop.NetworkManager.Connection.Active'
NM_IP4_IFACE = 'org.freedesktop.NetworkManager.IP4Config'
DBUS_PROPS = 'org.freedesktop.DBus.Properties'
SD_SERVICE = 'org.freedesktop.systemd1'
SD_PATH = '/org/freedesktop/systemd1'
SD_MANAGER = 'org.freedesktop.systemd1.Manager'

DEVICE_TYPE_ETHERNET = 1
DEVICE_STATE_ACTIVATED = 100
AC_STATE_ACTIVATED = 2
ROLLBACK_SECONDS = 180
APPLY_GRACE_SECONDS = 2
POLL_ATTEMPTS = 80
POLL_INTERVAL = 0.25
STATE_DIR = '/var/lib/blakestream-network'
BOOT_ID_PATH = '/proc/sys/kernel/random/boot_id'
APPLY_UNIT = 'blakestream-network-apply.service'
ROLLBACK_UNIT = 'blakestream-network-rollback.timer'
MAX_STDIN = 8192
MAX_TXN_BYTES = 262144
MAX_SETTINGS_BYTES = 65536
MAX_DEPTH = 12
NULL_PATH = '/'

VERBS = ('status', 'backup', 'pending', 'apply', 'confirm', 'rollback', 'activate', 'tick', 'restore-confirmed')
INPUT_VERBS = ('apply', 'confirm', 'rollback')
TERMINAL_STATES = ('confirmed', 'rolled_back', 'failed')
PENDING_STATES = ('pending', 'applying', 'awaiting-confirmation', 'rollback-failed')
ALL_STATES = TERMINAL_STATES + PENDING_STATES
ALLOWED_GROUPS = ('connection', '802-3-ethernet', 'ipv4', 'ipv6')
MODES = {'manual': 'static', 'auto': 'dhcp', 'link-local': 'link-local',
         'shared': 'shared', 'disabled': 'disabled'}
SECRET_EXACT = ('password', 'psk', 'secret', 'pin', 'private-key', 'wake-on-lan-password')
BASIC_SIGS = 'ynqiuxtdbsog'
BASIC_RANGES = {
    'y': (0, 255),
    'n': (-32768, 32767),
    'q': (0, 65535),
    'i': (-2147483648, 2147483647),
    'u': (0, 4294967295),
    'x': (-9223372036854775808, 9223372036854775807),
    't': (0, 18446744073709551615),
}
IFACE_RE_TEXT = '^[A-Za-z][A-Za-z0-9_.-]{0,14}$'
TXN_ID_TEXT = '^[0-9a-f]{32}$'
BOOT_ID_TEXT = '^[A-Za-z0-9-]{1,80}$'
UUID_TEXT = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

import re
IFACE_RE = re.compile(IFACE_RE_TEXT)
TXN_ID_RE = re.compile(TXN_ID_TEXT)
BOOT_ID_RE = re.compile(BOOT_ID_TEXT)
UUID_RE = re.compile(UUID_TEXT)


class NetError(Exception):
    pass


class DeadlineExceeded(BaseException):
    pass


class _Missing(Exception):
    pass


def _text(value):
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bytes):
        return value.decode('utf-8')
    raise NetError('unsupported profile format')


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_object(raw):
    if raw is None:
        raw = ''
    if isinstance(raw, bytes):
        if len(raw) > MAX_STDIN:
            raise NetError('input too large')
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise NetError('malformed JSON')
    if not isinstance(raw, str):
        raise NetError('malformed JSON')
    if len(raw) > MAX_STDIN:
        raise NetError('input too large')

    def pairs(seq):
        found = {}
        for key, value in seq:
            if not isinstance(key, str):
                raise NetError('malformed JSON')
            if key in found:
                raise NetError('duplicate key')
            found[key] = value
        return found

    try:
        data = json.loads(raw, object_pairs_hook=pairs)
    except NetError:
        raise
    except (ValueError, TypeError):
        raise NetError('malformed JSON')
    if not isinstance(data, dict):
        raise NetError('JSON object required')
    return data


def _parse_ipv4(value):
    if not isinstance(value, str):
        raise NetError('invalid IPv4 address')
    parts = value.split('.')
    if len(parts) != 4:
        raise NetError('invalid IPv4 address')
    numbers = []
    for part in parts:
        if not part.isdigit() or len(part) > 3:
            raise NetError('invalid IPv4 address')
        if len(part) > 1 and part.startswith('0'):
            raise NetError('invalid IPv4 address')
        number = int(part)
        if number > 255:
            raise NetError('invalid IPv4 address')
        numbers.append(number)
    return numbers


def _format_ipv4(numbers):
    return '{0}.{1}.{2}.{3}'.format(numbers[0], numbers[1], numbers[2], numbers[3])


def _canonical_ipv4(value):
    return _format_ipv4(_parse_ipv4(value))


def _ip_u32(value):
    numbers = _parse_ipv4(value)
    return (numbers[0] << 24) | (numbers[1] << 16) | (numbers[2] << 8) | numbers[3]


def _u32_ip(value):
    if not _is_int(value) or value < 0 or value > 0xFFFFFFFF:
        raise NetError('unsupported profile format')
    return '{0}.{1}.{2}.{3}'.format((value >> 24) & 255, (value >> 16) & 255,
                                    (value >> 8) & 255, value & 255)


def _wire_ip(value):
    return struct.unpack('=I', socket.inet_aton(_canonical_ipv4(value)))[0]


def _ip_from_wire(value):
    return socket.inet_ntoa(struct.pack('=I', value))


def _mask(prefix):
    if prefix <= 0:
        return 0
    if prefix >= 32:
        return 0xFFFFFFFF
    return (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF


def _network(addr, prefix):
    return addr & _mask(prefix)


def _broadcast(addr, prefix):
    mask = _mask(prefix)
    return (addr & mask) | ((~mask) & 0xFFFFFFFF)


def _is_global_bad(addr):
    if addr == 0 or addr == 0xFFFFFFFF:
        return True
    if (addr & 0xFF000000) == 0:
        return True
    if (addr & 0xF0000000) == 0xE0000000:
        return True
    if (addr & 0xF0000000) == 0xF0000000:
        return True
    if (addr & 0xFFFF0000) == 0xA9FE0000:
        return True
    return False


def _is_loopback(addr):
    return (addr & 0xFF000000) == 0x7F000000


def _validate_host(addr, prefix, label):
    if prefix < 1 or prefix > 30:
        raise NetError('unsupported prefix')
    if _is_global_bad(addr) or _is_loopback(addr):
        raise NetError('invalid ' + label)
    if addr == _network(addr, prefix) or addr == _broadcast(addr, prefix):
        raise NetError('invalid ' + label)
    return addr


def _validate_dns(items):
    if not isinstance(items, list) or not items or len(items) > 3:
        raise NetError('invalid DNS')
    seen = []
    for item in items:
        addr = _ip_u32(_canonical_ipv4(item))
        if addr == 0x7F000001:
            text = '127.0.0.1'
        else:
            if _is_global_bad(addr) or _is_loopback(addr):
                raise NetError('invalid DNS')
            text = _u32_ip(addr)
        if text in seen:
            raise NetError('invalid DNS')
        seen.append(text)
    return seen


def _parse_apply(raw):
    data = _parse_object(raw)
    extra = set(data.keys()) - set(['interface', 'mode', 'address', 'prefix', 'gateway', 'dns'])
    if extra:
        raise NetError('unknown setting')
    if 'interface' not in data or 'mode' not in data:
        raise NetError('incomplete network settings')
    interface = data['interface']
    if not isinstance(interface, str) or not IFACE_RE.match(interface):
        raise NetError('unsupported interface')
    mode = data['mode']
    if mode not in ('dhcp', 'static'):
        raise NetError('unsupported address mode')
    if mode == 'dhcp':
        if 'address' in data and data['address'] != '':
            raise NetError('DHCP does not accept a static address')
        if 'gateway' in data and data['gateway'] != '':
            raise NetError('DHCP does not accept a static gateway')
        if 'dns' in data and data['dns'] != []:
            raise NetError('DHCP does not accept static DNS')
        if 'prefix' in data and not _is_int(data['prefix']):
            raise NetError('invalid prefix')
        # The settings UI always submits prefix 24 for DHCP. It is not applied.
        return {'interface': interface, 'mode': 'dhcp', 'address': None,
                'prefix': None, 'gateway': None, 'dns': []}
    for key in ('address', 'prefix', 'gateway', 'dns'):
        if key not in data:
            raise NetError('static configuration is incomplete')
    if not _is_int(data['prefix']):
        raise NetError('invalid prefix')
    prefix = data['prefix']
    address = _canonical_ipv4(data['address'])
    gateway = _canonical_ipv4(data['gateway'])
    addr_n = _validate_host(_ip_u32(address), prefix, 'address')
    gate_n = _validate_host(_ip_u32(gateway), prefix, 'gateway')
    if _network(addr_n, prefix) != _network(gate_n, prefix) or addr_n == gate_n:
        raise NetError('invalid gateway')
    dns = _validate_dns(data['dns'])
    return {'interface': interface, 'mode': 'static', 'address': address,
            'prefix': prefix, 'gateway': gateway, 'dns': dns}


def _parse_id_body(raw, confirm):
    data = _parse_object(raw)
    allowed = set(['id', 'server_address'] if confirm else ['id'])
    if set(data.keys()) != allowed:
        raise NetError('invalid request')
    ident = data.get('id')
    if not isinstance(ident, str) or not TXN_ID_RE.match(ident):
        raise NetError('invalid transaction')
    body = {'id': ident}
    if confirm:
        body['server_address'] = _canonical_ipv4(data.get('server_address'))
    return body


def _split_one(sig):
    if not sig:
        raise NetError('unsupported profile format')
    kind = sig[0]
    if kind in BASIC_SIGS:
        return 'basic', kind, sig[1:]
    if kind == 'v':
        return 'variant', None, sig[1:]
    if kind == 'a':
        if len(sig) > 1 and sig[1] == '{':
            end = _match_closer(sig, 1, '{', '}')
            return 'dict', sig[2:end], sig[end + 1:]
        _kind, _inner, rest = _split_one(sig[1:])
        element = sig[1:len(sig) - len(rest)] if rest else sig[1:]
        return 'array', element, rest
    if kind == '(':
        end = _match_closer(sig, 0, '(', ')')
        return 'struct', sig[1:end], sig[end + 1:]
    raise NetError('unsupported profile format')


def _match_closer(sig, start, opener, closer):
    depth = 0
    index = start
    while index < len(sig):
        char = sig[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise NetError('unsupported profile format')


def _complete_type(sig):
    kind, inner, rest = _split_one(sig)
    if rest:
        raise NetError('unsupported profile format')
    return kind, inner


def _field_sigs(sig):
    fields = []
    rest = sig
    while rest:
        _kind, _inner, nxt = _split_one(rest)
        fields.append(rest[:-len(nxt)] if nxt else rest)
        rest = nxt
    return fields


def _is_secret_key(name):
    lowered = name.lower()
    if lowered in SECRET_EXACT or lowered.startswith('wep-key') or 'private-key' in lowered:
        return True
    return lowered.endswith('-password') or lowered.endswith('-psk') or lowered.endswith('-secret') or lowered.endswith('-pin')


def _as_variant(value):
    options = {'variant_level': value.variant_level + 1}
    if getattr(value, 'signature', None) is not None:
        options['signature'] = value.signature
    return type(value)(value, **options)


def _constructor(dbus_mod, sig):
    names = {
        'y': 'Byte', 'n': 'Int16', 'q': 'UInt16', 'i': 'Int32', 'u': 'UInt32',
        'x': 'Int64', 't': 'UInt64', 'd': 'Double', 'b': 'Boolean', 's': 'String',
        'o': 'ObjectPath', 'g': 'Signature',
    }
    return getattr(dbus_mod, names[sig])


def _encode_basic(sig, value, dbus_mod):
    if sig == 'b':
        if not isinstance(value, bool):
            raise NetError('unsupported profile format')
        return dbus_mod.Boolean(value)
    if sig == 'd':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NetError('unsupported profile format')
        return dbus_mod.Double(value)
    if sig in BASIC_RANGES:
        if not _is_int(value):
            raise NetError('unsupported profile format')
        low, high = BASIC_RANGES[sig]
        if value < low or value > high:
            raise NetError('unsupported profile format')
        return _constructor(dbus_mod, sig)(value)
    if sig in ('s', 'o', 'g'):
        if not isinstance(value, str):
            raise NetError('unsupported profile format')
        return _constructor(dbus_mod, sig)(value)
    raise NetError('unsupported profile format')


def _encode(sig, body, dbus_mod, depth):
    if depth > MAX_DEPTH:
        raise NetError('unsupported profile format')
    kind, inner = _complete_type(sig)
    if kind == 'basic':
        return _encode_basic(inner, body, dbus_mod)
    if kind == 'variant':
        if not isinstance(body, dict) or set(body.keys()) != set(['sig', 'v']):
            raise NetError('unsupported profile format')
        encoded = _encode(body['sig'], body['v'], dbus_mod, depth + 1)
        return _as_variant(encoded)
    if kind == 'array':
        if not isinstance(body, list):
            raise NetError('unsupported profile format')
        items = [_encode(inner, item, dbus_mod, depth + 1) for item in body]
        return dbus_mod.Array(items, signature=inner)
    if kind == 'struct':
        fields = _field_sigs(inner)
        if not isinstance(body, list) or len(body) != len(fields):
            raise NetError('unsupported profile format')
        values = [_encode(field, item, dbus_mod, depth + 1) for field, item in zip(fields, body)]
        return dbus_mod.Struct(tuple(values), signature=inner)
    if kind == 'dict':
        return _encode_dict(inner, body, dbus_mod, depth)
    raise NetError('unsupported profile format')


def _encode_dict(inner, body, dbus_mod, depth):
    if not inner or inner[0] != 's' or not isinstance(body, dict):
        raise NetError('unsupported profile format')
    value_sig = inner[1:]
    _complete_type(value_sig)
    encoded = {}
    for key in sorted(body.keys()):
        if not isinstance(key, str):
            raise NetError('unsupported profile format')
        value = body[key]
        if value_sig == 'v':
            if not isinstance(value, dict) or 'sig' not in value:
                raise NetError('unsupported profile format')
            packed = _encode(value['sig'], value['v'], dbus_mod, depth + 1)
            encoded[dbus_mod.String(key)] = _as_variant(packed)
        else:
            if not isinstance(value, dict) or value.get('sig') != value_sig:
                raise NetError('unsupported profile format')
            encoded[dbus_mod.String(key)] = _encode(value_sig, value['v'], dbus_mod, depth + 1)
    return dbus_mod.Dictionary(encoded, signature=inner)


def encode_settings(settings, dbus_mod):
    if not isinstance(settings, dict):
        raise NetError('unsupported profile format')
    outer = {}
    for group in sorted(settings.keys()):
        body = settings[group]
        if not isinstance(group, str) or not isinstance(body, dict):
            raise NetError('unsupported profile format')
        inner = {}
        for key in sorted(body.keys()):
            tagged = body[key]
            if not isinstance(key, str) or not isinstance(tagged, dict) or 'sig' not in tagged:
                raise NetError('unsupported profile format')
            if _is_secret_key(key):
                raise NetError('unsupported profile format')
            packed = _encode(tagged['sig'], tagged['v'], dbus_mod, 0)
            inner[dbus_mod.String(key)] = _as_variant(packed)
        outer[dbus_mod.String(group)] = dbus_mod.Dictionary(inner, signature='sv')
    return dbus_mod.Dictionary(outer, signature='sa{sv}')


def _as_bool(value, dbus_mod):
    if isinstance(value, bool) or isinstance(value, dbus_mod.Boolean):
        return True if value else False
    raise NetError('unsupported profile format')


def _as_number(value, dbus_mod):
    if isinstance(value, bool) or isinstance(value, dbus_mod.Boolean) or not isinstance(value, int):
        raise NetError('unsupported profile format')
    return int(value)


def _infer_basic_sig(value, dbus_mod):
    if isinstance(value, dbus_mod.Boolean):
        return 'b'
    checks = (
        ('y', 'Byte'), ('n', 'Int16'), ('q', 'UInt16'), ('i', 'Int32'),
        ('u', 'UInt32'), ('x', 'Int64'), ('t', 'UInt64'), ('d', 'Double'),
        ('o', 'ObjectPath'), ('g', 'Signature'), ('s', 'String'),
    )
    for sig, name in checks:
        kind = getattr(dbus_mod, name, None)
        if kind is not None and isinstance(value, kind):
            return sig
    if isinstance(value, bool):
        return 'b'
    raise NetError('unsupported profile format')


def _plain_basic(sig, value, dbus_mod):
    if sig == 'b':
        return _as_bool(value, dbus_mod)
    if sig == 'd':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NetError('unsupported profile format')
        return float(value)
    if sig in BASIC_RANGES:
        number = _as_number(value, dbus_mod)
        low, high = BASIC_RANGES[sig]
        if number < low or number > high:
            raise NetError('unsupported profile format')
        return number
    if sig in ('s', 'o', 'g'):
        return _text(value)
    raise NetError('unsupported profile format')


def _decode_body(sig, value, dbus_mod, depth):
    if depth > MAX_DEPTH:
        raise NetError('unsupported profile format')
    kind, inner = _complete_type(sig)
    if kind == 'basic':
        return _plain_basic(inner, value, dbus_mod)
    if kind == 'variant':
        return _decode_value(value, dbus_mod, depth + 1)
    if kind == 'array':
        return [_decode_body(inner, item, dbus_mod, depth + 1) for item in value]
    if kind == 'struct':
        fields = _field_sigs(inner)
        values = list(value)
        if len(fields) != len(values):
            raise NetError('unsupported profile format')
        return [_decode_body(field, item, dbus_mod, depth + 1) for field, item in zip(fields, values)]
    if kind == 'dict':
        return _decode_dict_body(inner, value, dbus_mod, depth)
    raise NetError('unsupported profile format')


def _decode_dict_body(inner, value, dbus_mod, depth):
    if not inner or inner[0] != 's':
        raise NetError('unsupported profile format')
    value_sig = inner[1:]
    body = {}
    for key, item in value.items():
        text = _text(key)
        if value_sig == 'v':
            body[text] = _decode_value(item, dbus_mod, depth + 1)
        else:
            body[text] = {'sig': value_sig, 'v': _decode_body(value_sig, item, dbus_mod, depth + 1)}
    return body


def _decode_value(value, dbus_mod, depth):
    if depth > MAX_DEPTH:
        raise NetError('unsupported profile format')
    if getattr(value, 'variant_level', 0) > 1:
        raise NetError('unsupported nested variant')
    if isinstance(value, dbus_mod.ByteArray):
        return {'sig': 'ay', 'v': list(bytes(value))}
    if isinstance(value, dbus_mod.Dictionary):
        signature = getattr(value, 'signature', None)
        if not signature:
            raise NetError('unsupported profile format')
        return {'sig': 'a{' + signature + '}', 'v': _decode_dict_body(signature, value, dbus_mod, depth)}
    if isinstance(value, dbus_mod.Array):
        element = getattr(value, 'signature', None)
        if not element:
            raise NetError('unsupported profile format')
        return {'sig': 'a' + element, 'v': [_decode_body(element, item, dbus_mod, depth + 1) for item in value]}
    if isinstance(value, dbus_mod.Struct):
        interior = getattr(value, 'signature', None)
        if interior is None:
            interior = ''.join(_decode_value(item, dbus_mod, depth + 1)['sig'] for item in value)
        return {'sig': '(' + interior + ')', 'v': _decode_struct_body(interior, value, dbus_mod, depth)}
    signature = _infer_basic_sig(value, dbus_mod)
    return {'sig': signature, 'v': _plain_basic(signature, value, dbus_mod)}


def _decode_struct_body(interior, value, dbus_mod, depth):
    fields = _field_sigs(interior)
    values = list(value)
    if len(fields) != len(values):
        raise NetError('unsupported profile format')
    return [_decode_body(field, item, dbus_mod, depth + 1) for field, item in zip(fields, values)]


def decode_settings(value, dbus_mod):
    if not isinstance(value, dbus_mod.Dictionary) or getattr(value, 'signature', None) != 'sa{sv}':
        raise NetError('unsupported profile format')
    settings = {}
    for group, body in value.items():
        name = _text(group)
        decoded = _decode_value(body, dbus_mod, 0)
        if decoded.get('sig') != 'a{sv}' or not isinstance(decoded.get('v'), dict):
            raise NetError('unsupported profile format')
        for key in decoded['v']:
            if _is_secret_key(key):
                raise NetError('unsupported profile format')
        settings[name] = decoded['v']
    return settings


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _tagged(sig, value):
    return {'sig': sig, 'v': value}


def _group_value(group, key):
    if not group or key not in group:
        return None
    return group[key]


def _validate_profile(settings, interface, hwaddr):
    if not isinstance(settings, dict):
        raise NetError('unsupported profile format')
    for group in settings:
        if group not in ALLOWED_GROUPS:
            raise NetError('unsupported profile format')
        if not isinstance(settings[group], dict):
            raise NetError('unsupported profile format')
        for key in settings[group]:
            if _is_secret_key(key):
                raise NetError('unsupported profile format')
    connection = settings.get('connection')
    if not connection:
        raise NetError('unsupported profile format')
    kind = _group_value(connection, 'type')
    if not kind or kind.get('sig') != 's' or kind.get('v') != '802-3-ethernet':
        raise NetError('unsupported profile format')
    iface = _group_value(connection, 'interface-name')
    if iface is not None and iface.get('v') not in ('', interface):
        raise NetError('unsupported profile format')
    for key in ('master', 'slave-type'):
        value = _group_value(connection, key)
        if value is not None and value.get('v'):
            raise NetError('unsupported profile format')
    readonly = _group_value(connection, 'read-only')
    if readonly is not None and readonly.get('v') is True:
        raise NetError('unsupported profile format')
    secondaries = _group_value(connection, 'secondaries')
    if secondaries is not None and secondaries.get('v'):
        raise NetError('unsupported profile format')
    priority = _group_value(connection, 'autoconnect-priority')
    if priority is not None:
        if priority.get('sig') != 'i' or not _is_int(priority.get('v')) or priority['v'] < -999 or priority['v'] > 999:
            raise NetError('unsupported profile format')
    ethernet = settings.get('802-3-ethernet', {})
    mac = _group_value(ethernet, 'mac-address')
    if mac is not None:
        if mac.get('sig') != 'ay' or not isinstance(mac.get('v'), list) or len(mac['v']) != 6:
            raise NetError('unsupported profile format')
        if not hwaddr:
            raise NetError('hardware address unavailable')
        if _format_mac(mac['v']) != hwaddr.lower():
            raise NetError('unsupported profile format')
    blacklist = _group_value(ethernet, 'mac-address-blacklist')
    if blacklist is not None and blacklist.get('v'):
        if not hwaddr:
            raise NetError('hardware address unavailable')
        for item in blacklist['v']:
            if _text(item).lower() == hwaddr.lower():
                raise NetError('unsupported profile format')
    _validate_ipv4_profile(settings.get('ipv4'))
    if len(_canonical(settings)) > MAX_SETTINGS_BYTES:
        raise NetError('unsupported profile format')


def _format_mac(values):
    try:
        numbers = [int(item) for item in values]
    except (TypeError, ValueError):
        raise NetError('unsupported profile format')
    if any(item < 0 or item > 255 for item in numbers):
        raise NetError('unsupported profile format')
    return ':'.join('{0:02x}'.format(item) for item in numbers)


def _validate_ipv4_profile(ipv4):
    if not isinstance(ipv4, dict):
        raise NetError('unsupported profile format')
    method = _group_value(ipv4, 'method')
    if not method or method.get('sig') != 's' or method.get('v') not in ('auto', 'manual'):
        raise NetError('unsupported profile format')
    addresses = None
    if 'addresses' in ipv4:
        raw = ipv4['addresses']
        if raw.get('sig') != 'aau' or not isinstance(raw.get('v'), list):
            raise NetError('unsupported profile format')
        addresses = []
        for row in raw['v']:
            if not isinstance(row, list) or len(row) != 3 or any(not _is_int(item) for item in row):
                raise NetError('unsupported profile format')
            addr, prefix, gateway = row
            if addr < 0 or addr > 0xFFFFFFFF or gateway < 0 or gateway > 0xFFFFFFFF or prefix < 0 or prefix > 32:
                raise NetError('unsupported profile format')
            addresses.append((_ip_u32(_ip_from_wire(addr)), prefix, _ip_u32(_ip_from_wire(gateway))))
    address_data = None
    if 'address-data' in ipv4:
        raw = ipv4['address-data']
        if raw.get('sig') != 'aa{sv}' or not isinstance(raw.get('v'), list):
            raise NetError('unsupported profile format')
        address_data = []
        for item in raw['v']:
            if not isinstance(item, dict) or set(item.keys()) - set(['address', 'prefix']):
                raise NetError('unsupported profile format')
            if 'address' not in item or 'prefix' not in item:
                raise NetError('unsupported profile format')
            if item['address'].get('sig') != 's' or item['prefix'].get('sig') != 'u':
                raise NetError('unsupported profile format')
            address_data.append((_ip_u32(_canonical_ipv4(item['address']['v'])), int(item['prefix']['v'])))
    gateway = None
    if 'gateway' in ipv4:
        raw = ipv4['gateway']
        if raw.get('sig') != 's' or not isinstance(raw.get('v'), str):
            raise NetError('unsupported profile format')
        gateway = 0 if raw['v'] == '' else _ip_u32(_canonical_ipv4(raw['v']))
    labels = _group_value(ipv4, 'address-labels')
    if labels is not None and labels.get('v'):
        raise NetError('unsupported profile format')
    if addresses is not None and address_data is not None:
        if len(addresses) != len(address_data):
            raise NetError('inconsistent IPv4 address')
        for left, right in zip(addresses, address_data):
            if left[0] != right[0] or left[1] != right[1]:
                raise NetError('inconsistent IPv4 address')
    if addresses is not None:
        if not addresses:
            if gateway not in (None, 0) or address_data not in (None, []):
                raise NetError('inconsistent IPv4 address')
        elif len(addresses) == 1 and gateway is not None and gateway != addresses[0][2]:
            # NM 1.2.6 ignores the gateway string when addresses is present.
            raise NetError('inconsistent IPv4 address')
        elif len(addresses) > 1:
            raise NetError('unsupported profile format')
    if method['v'] == 'auto':
        if addresses not in (None, []) or address_data not in (None, []) or gateway not in (None, 0):
            raise NetError('unsupported profile format')
    if method['v'] == 'manual':
        count = len(addresses) if addresses is not None else (len(address_data) if address_data is not None else 0)
        if count != 1:
            raise NetError('unsupported profile format')
    return method['v']


def _manual_endpoint(settings):
    ipv4 = settings['ipv4']
    method = ipv4['method']['v']
    if method == 'auto':
        return {'mode': 'dhcp'}
    if 'addresses' in ipv4 and ipv4['addresses']['v']:
        addr, prefix, gateway = ipv4['addresses']['v'][0]
        gateway_text = None if gateway == 0 else _ip_from_wire(gateway)
        address = _ip_from_wire(addr)
    elif 'address-data' in ipv4 and ipv4['address-data']['v']:
        item = ipv4['address-data']['v'][0]
        address = _canonical_ipv4(item['address']['v'])
        prefix = int(item['prefix']['v'])
        gateway_text = ipv4.get('gateway', {}).get('v') or None
        if gateway_text == '':
            gateway_text = None
    else:
        raise NetError('unsupported profile format')
    dns = []
    if 'dns' in ipv4:
        dns = [_ip_from_wire(item) for item in ipv4['dns']['v']]
    return {'mode': 'static', 'address': address, 'prefix': int(prefix),
            'gateway': gateway_text, 'dns': sorted(dns)}


def _minimal_settings():
    return {
        'connection': {},
        '802-3-ethernet': {},
        'ipv4': {},
        'ipv6': {'method': _tagged('s', 'auto')},
    }


def _candidate_settings(original, ident, uuid, interface, request):
    settings = json.loads(_canonical(original)) if original else _minimal_settings()
    connection = settings.setdefault('connection', {})
    connection['id'] = _tagged('s', 'blakestream-' + ident[:8])
    connection['uuid'] = _tagged('s', uuid)
    connection['type'] = _tagged('s', '802-3-ethernet')
    connection['interface-name'] = _tagged('s', interface)
    connection['autoconnect'] = _tagged('b', False)
    connection['autoconnect-priority'] = _tagged('i', 0)
    connection.pop('timestamp', None)
    # The IPv4 form replaces IPv4 policy; do not inherit hidden static routes or DNS overrides.
    ipv4 = settings['ipv4'] = {}
    if request['mode'] == 'dhcp':
        ipv4['method'] = _tagged('s', 'auto')
        ipv4['addresses'] = _tagged('aau', [])
        ipv4['address-data'] = _tagged('aa{sv}', [])
        ipv4['gateway'] = _tagged('s', '')
        ipv4['dns'] = _tagged('au', [])
        ipv4['ignore-auto-dns'] = _tagged('b', False)
        ipv4['ignore-auto-routes'] = _tagged('b', False)
    else:
        ipv4['method'] = _tagged('s', 'manual')
        ipv4['addresses'] = _tagged('aau', [[_wire_ip(request['address']), request['prefix'], _wire_ip(request['gateway'])]])
        ipv4['address-data'] = _tagged('aa{sv}', [{
            'address': _tagged('s', request['address']),
            'prefix': _tagged('u', request['prefix']),
        }])
        ipv4['gateway'] = _tagged('s', request['gateway'])
        ipv4['dns'] = _tagged('au', [_wire_ip(item) for item in request['dns']])
    settings.setdefault('802-3-ethernet', {})
    settings.setdefault('ipv6', {'method': _tagged('s', 'auto')})
    return settings


class _FdLock(object):
    def __init__(self, fd):
        self.fd = fd

    def release(self):
        fd = self.fd
        self.fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class PosixFS(object):
    def __init__(self, state_dir=STATE_DIR, boot_id_path=BOOT_ID_PATH, require_root=True,
                 profiles_dir='/etc/NetworkManager/system-connections'):
        self.state_dir = state_dir
        self.boot_id_path = boot_id_path
        self.require_root = require_root
        self.profiles_dir = profiles_dir

    def profile_saved(self, uuid):
        # NM 1.2's Unsaved flag also covers timestamp changes to file-backed profiles.
        # Read only the UUID, never return keyfile contents or secrets.
        try:
            directory = os.open(self.profiles_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return False
            raise NetError('cannot inspect saved network profiles')
        try:
            names = os.listdir(directory)
            if len(names) > 128:
                raise NetError('too many saved network profiles')
            for name in names:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    continue
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(fd, 'r', encoding='utf-8') as source:
                    raw = source.read(MAX_SETTINGS_BYTES + 1)
                if len(raw) > MAX_SETTINGS_BYTES:
                    raise NetError('saved network profile is too large')
                parser = configparser.ConfigParser(interpolation=None)
                try:
                    parser.read_string(raw)
                except configparser.Error:
                    continue
                if parser.get('connection', 'uuid', fallback=None) == uuid:
                    return True
            return False
        except (OSError, UnicodeError):
            raise NetError('cannot inspect saved network profiles')
        finally:
            os.close(directory)

    def _flags(self, *names):
        value = 0
        for name in names:
            value |= getattr(os, name)
        return value

    def _openat(self, dirfd, name, flags, mode=0):
        try:
            return os.open(name, flags, mode, dir_fd=dirfd), 0
        except OSError as exc:
            return -1, exc.errno

    def _components(self, path):
        if not isinstance(path, str) or not path.startswith('/'):
            raise NetError('invalid path')
        parts = path.split('/')[1:]
        if not parts or any(part in ('', '.', '..') for part in parts):
            raise NetError('invalid path')
        return parts

    def _assert_dir(self, fd, secure=True):
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise NetError('invalid state directory')
        if secure and self.require_root and info.st_uid != 0:
            raise NetError('state directory is not owned by root')
        if secure and self.require_root and stat.S_IMODE(info.st_mode) & 0o022:
            raise NetError('state directory is writable by group or other users')

    def _walk(self, path, create_tail):
        parts = self._components(path)
        flags = self._flags('O_RDONLY', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC')
        fd, err = self._openat(-100, '/', flags)
        if fd < 0:
            raise NetError('cannot open state directory')
        self._assert_dir(fd, secure=False)
        create_from = len(parts) - create_tail
        for index, part in enumerate(parts):
            child, err = self._openat(fd, part, flags)
            if child < 0 and err == errno.ENOENT and create_tail and index >= create_from:
                self._mkdir(fd, part)
                child, err = self._openat(fd, part, flags)
                if child >= 0:
                    os.fchmod(child, 0o700)
            if child < 0:
                os.close(fd)
                if err == errno.ELOOP:
                    raise NetError('refusing symlink')
                if err == errno.ENOENT:
                    raise _Missing()
                raise NetError('cannot open state directory')
            os.close(fd)
            fd = child
            try:
                # Stock images assign / and /var to the administrator. Require the
                # state directory and its immediate parent to be root-owned instead.
                self._assert_dir(fd, secure=index >= len(parts) - 2)
            except NetError:
                os.close(fd)
                raise
        return fd

    def _mkdir(self, dirfd, name):
        try:
            os.mkdir(name, 0o700, dir_fd=dirfd)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise NetError('cannot create state directory')

    def _open_state_dir(self, create):
        try:
            fd = self._walk(self.state_dir, 2 if create else 0)
        except _Missing:
            raise NetError('state directory is missing')
        if create:
            os.fchmod(fd, 0o700)
            self._assert_dir(fd)
        return fd

    def ensure_state_dir(self):
        os.close(self._open_state_dir(True))

    def try_lock(self):
        dirfd = self._open_state_dir(False)
        fd = -1
        try:
            flags = self._flags('O_RDWR', 'O_CREAT', 'O_NOFOLLOW', 'O_CLOEXEC')
            fd, err = self._openat(dirfd, 'lock', flags, 0o600)
            if fd < 0:
                if err == errno.ELOOP:
                    raise NetError('refusing symlink')
                raise NetError('cannot lock network settings')
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise NetError('refusing symlink')
            if self.require_root and info.st_uid != 0:
                raise NetError('lock is not owned by root')
            if stat.S_IMODE(info.st_mode) & 0o022:
                raise NetError('lock is writable by group or other users')
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError) as exc:
                if getattr(exc, 'errno', None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise NetError('network settings are busy')
                raise NetError('cannot lock network settings')
            lock = _FdLock(fd)
            fd = -1
            return lock
        finally:
            if fd >= 0:
                os.close(fd)
            os.close(dirfd)

    def _parent(self, create):
        try:
            return self._walk(self.state_dir, 2 if create else 0)
        except _Missing:
            if create:
                raise NetError('state directory is missing')
            raise

    def read_txn(self):
        try:
            dirfd = self._parent(False)
        except _Missing:
            return None
        try:
            return self._read_child(dirfd, 'transaction.json')
        finally:
            os.close(dirfd)

    def write_txn(self, text):
        if not isinstance(text, str):
            raise NetError('cannot store transaction')
        data = text.encode('utf-8')
        if len(data) > MAX_TXN_BYTES:
            raise NetError('transaction is too large')
        dirfd = self._parent(False)
        tmp = 'transaction.json.' + binascii.hexlify(os.urandom(8)).decode('ascii') + '.tmp'
        fd = -1
        try:
            flags = self._flags('O_WRONLY', 'O_CREAT', 'O_EXCL', 'O_NOFOLLOW', 'O_CLOEXEC')
            fd, err = self._openat(dirfd, tmp, flags, 0o600)
            if fd < 0:
                raise NetError('cannot store transaction')
            os.fchmod(fd, 0o600)
            rest = data
            while rest:
                written = os.write(fd, rest)
                if written <= 0:
                    raise NetError('cannot store transaction')
                rest = rest[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            self._reject_symlink_target(dirfd, 'transaction.json')
            os.rename(tmp, 'transaction.json', src_dir_fd=dirfd, dst_dir_fd=dirfd)
            tmp = None
            os.fsync(dirfd)
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp:
                try:
                    os.unlink(tmp, dir_fd=dirfd)
                except OSError:
                    pass
            os.close(dirfd)

    def _reject_symlink_target(self, dirfd, name):
        try:
            info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return
            raise NetError('refusing symlink')
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise NetError('refusing symlink')
        if (self.require_root and info.st_uid != 0) or stat.S_IMODE(info.st_mode) & 0o022:
            raise NetError('transaction is not owned by root')

    def _read_child(self, dirfd, name):
        try:
            info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise NetError('cannot read transaction')
        if stat.S_ISLNK(info.st_mode):
            raise NetError('refusing symlink')
        if not stat.S_ISREG(info.st_mode):
            raise NetError('refusing symlink')
        if self.require_root and info.st_uid != 0:
            raise NetError('transaction is not owned by root')
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise NetError('transaction is writable by group or other users')
        flags = self._flags('O_RDONLY', 'O_NOFOLLOW', 'O_CLOEXEC')
        fd, err = self._openat(dirfd, name, flags)
        if fd < 0:
            if err == errno.ELOOP:
                raise NetError('refusing symlink')
            if err == errno.ENOENT:
                return None
            raise NetError('cannot read transaction')
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (self.require_root and info.st_uid != 0):
                raise NetError('unsafe transaction file')
            if stat.S_IMODE(info.st_mode) & 0o022:
                raise NetError('unsafe transaction permissions')
            chunks = []
            size = 0
            while True:
                piece = os.read(fd, 65536)
                if not piece:
                    break
                size += len(piece)
                if size > MAX_TXN_BYTES:
                    raise NetError('transaction state is corrupt')
                chunks.append(piece)
        finally:
            os.close(fd)
        return b''.join(chunks)

    def read_boot_id(self):
        try:
            fd = self._walk(os.path.dirname(self.boot_id_path), 0)
        except (NetError, _Missing):
            return None
        try:
            data = self._read_child(fd, os.path.basename(self.boot_id_path))
        except NetError:
            return None
        finally:
            os.close(fd)
        if data is None:
            return None
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return None


class Service(object):
    def __init__(self, bus, dbus_mod, fs, clock, sleep, urandom, geteuid,
                 grace=APPLY_GRACE_SECONDS, monotonic=time.monotonic):
        self.bus = bus
        self.dbus = dbus_mod
        self.fs = fs
        self.clock = clock
        self.sleep = sleep
        self.urandom = urandom
        self.geteuid = geteuid
        self.grace = grace
        self.monotonic = monotonic
        self.call_deadline = None
        self.owners = {}
        self._reporting = False

    def dispatch(self, verb, raw):
        self.call_deadline = self.monotonic() + (32 if verb in ('activate', 'tick', 'restore-confirmed') else 10)
        self.owners = {}
        if self.geteuid() != 0:
            return self._fail('root required', read_nm=False)
        try:
            if verb == 'status':
                return self.cmd_status()
            if verb == 'backup':
                return self.cmd_backup()
            if verb == 'pending':
                txn = self._load()
                return self._body(True, None, [], self._public_pending(txn), None)
            if verb == 'apply':
                return self.cmd_apply(raw)
            if verb == 'confirm':
                return self.cmd_confirm(raw)
            if verb == 'rollback':
                return self.cmd_rollback(raw)
            if verb == 'activate':
                return self.cmd_activate()
            if verb == 'tick':
                return self.cmd_tick()
            if verb == 'restore-confirmed':
                return self.cmd_restore_confirmed()
            return self._fail('invalid arguments', read_nm=False)
        except NetError as exc:
            try:
                pending = self._load()
            except Exception:
                pending = None
            return self._fail(str(exc), pending, read_nm=False)
        except Exception:
            return self._fail('internal error', read_nm=False)

    def cmd_status(self):
        nm_error = None
        try:
            interfaces = self.interfaces()
        except NetError as exc:
            interfaces = []
            nm_error = str(exc)
        try:
            txn = self._load()
        except NetError as exc:
            return self._body(False, str(exc), interfaces, None, None)
        if txn and txn['state'] == 'rollback-failed':
            error = 'rollback failed; previous settings were not restored'
            if txn.get('last_result') and txn['last_result'].get('error'):
                error = txn['last_result']['error']
            return self._body(False, error, interfaces, self._public_pending(txn), txn.get('last_result'))
        if nm_error:
            return self._body(False, nm_error, interfaces, self._public_pending(txn), None if not txn else txn.get('last_result'))
        return self._body(True, None, interfaces, self._public_pending(txn), None if not txn else txn.get('last_result'))

    def cmd_backup(self):
        # Read configured endpoints, never DHCP leases or an unconfirmed candidate.
        with self._locked():
            txn = self._load()
            found = []
            for path in self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'GetDevices', []):
                path = str(path)
                if int(self._prop(path, NM_DEVICE_IFACE, 'DeviceType')) != DEVICE_TYPE_ETHERNET:
                    continue
                name = _text(self._prop(path, NM_DEVICE_IFACE, 'Interface'))
                profile = self._backup_profile(path, name, txn)
                if not profile or not profile['persistent']:
                    continue
                item = {'interface': name, 'saved': True, 'address': None,
                        'prefix': None, 'gateway': None, 'dns': []}
                item.update(_manual_endpoint(profile['settings']))
                if item['mode'] == 'static':
                    # Endpoint comparisons sort DNS; backups retain configured priority.
                    item['dns'] = [_ip_from_wire(value) for value in
                                   profile['settings']['ipv4'].get('dns', {}).get('v', [])]
                found.append(item)
            return self._body(True, None, sorted(found, key=lambda item: item['interface']), None, None)

    def _backup_profile(self, device, interface, txn):
        if not bool(self._prop(device, NM_DEVICE_IFACE, 'Managed')):
            return None
        related = txn and txn['interface'] == interface
        if related and txn['state'] not in TERMINAL_STATES:
            # Keep the saved pre-change configuration, never the pending candidate.
            return txn.get('original')

        def saved_profile(path):
            if not path or path == NULL_PATH:
                return None
            profile = self._snapshot_connection(path, interface, device, False)
            return profile if profile['persistent'] else None

        active = str(self._prop(device, NM_DEVICE_IFACE, 'ActiveConnection'))
        if active and active != NULL_PATH:
            profile = saved_profile(str(self._prop(active, NM_ACTIVE_IFACE, 'Connection')))
            if profile:
                return profile
        if related and txn['state'] == 'confirmed':
            profile = saved_profile(self._lookup_uuid(txn['candidate']['uuid']))
            if profile:
                return profile
        available = self._prop(device, NM_DEVICE_IFACE, 'AvailableConnections')
        if len(available) > 64:
            raise NetError('too many connection profiles')
        saved = [profile for profile in (saved_profile(str(path)) for path in available) if profile]
        if len(saved) > 1:
            raise NetError('multiple saved connection profiles; connect the intended profile before backup')
        return saved[0] if saved else None

    def cmd_apply(self, raw):
        request = _parse_apply(raw)
        ident = None
        with self._locked():
            current = self._load()
            if current and current['state'] not in TERMINAL_STATES:
                raise NetError('a network change is already in progress')
            original = self._select_profile(request['interface'])
            boot_id = self._require_boot()
            self._arm_rollback()
            apply_path = self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'LoadUnit', [APPLY_UNIT], 'systemd')
            state = str(self._dbus(SD_SERVICE, str(apply_path), DBUS_PROPS, 'Get',
                                  ['org.freedesktop.systemd1.Unit', 'ActiveState'], 'systemd'))
            if state not in ('inactive', 'failed'):
                raise NetError('the previous network apply is still running')
            now = int(self.clock())
            mono = self.monotonic()
            ident = self._new_id()
            txn = {
                'version': 1,
                'id': ident,
                'state': 'pending',
                'created': now,
                'deadline': now + ROLLBACK_SECONDS,
                'created_mono': mono,
                'deadline_mono': mono + ROLLBACK_SECONDS,
                'boot_id': boot_id,
                'nm_owner': self.owners.get(NM_SERVICE),
                'interface': request['interface'],
                'mode': request['mode'],
                'address': request['address'],
                'prefix': request['prefix'],
                'gateway': request['gateway'],
                'dns': request['dns'],
                'original': original,
                'candidate': {'path': None, 'uuid': self._new_uuid(), 'settings': None},
                'last_result': None,
            }
            self._save(txn)
        try:
            self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'StartUnit', [APPLY_UNIT, 'replace'], 'systemd')
        except NetError:
            with self._locked():
                current = self._load()
                if current and current.get('id') == ident and current.get('state') == 'pending':
                    current['state'] = 'failed'
                    current['last_result'] = self._result('apply', False, 'failed to start apply service')
                    self._save(current)
                    txn = current
                else:
                    txn = current
            return self._fail('failed to start apply service', txn)
        # The worker can now hold the lock or replace the address. Return the queued snapshot.
        return self._body(True, None, [], self._public_pending(txn), txn.get('last_result'))

    def _arm_rollback(self):
        self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'StartUnit', [ROLLBACK_UNIT, 'replace'], 'systemd')
        path = str(self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'GetUnit', [ROLLBACK_UNIT], 'systemd'))
        for _attempt in range(10):
            active = self._dbus(SD_SERVICE, path, DBUS_PROPS, 'Get',
                                ['org.freedesktop.systemd1.Unit', 'ActiveState'], 'systemd')
            if str(active) == 'active':
                return
            self.sleep(0.1)
        raise NetError('rollback timer is not active')

    def cmd_activate(self):
        with self._locked():
            txn = self._load()
            if not txn or txn['state'] != 'pending':
                return self._body(True, None, [], self._public_pending(txn), None)
            if self._expired(txn):
                self._rollback(txn, 'activate')
                return self._fail('transaction expired', txn)
            self.sleep(self.grace)
            if self._expired(txn):
                self._rollback(txn, 'activate')
                return self._fail('transaction expired', txn)
            txn['state'] = 'applying'
            self._save(txn)
            try:
                self._arm_rollback()
                self._bring_up(txn)
            except NetError as exc:
                restored = self._rollback(txn, 'activate')
                if restored:
                    return self._fail('activation failed: {0}'.format(exc), txn)
                return self._fail('activation failed and rollback failed', txn)
            return self._ok(txn)

    def cmd_confirm(self, raw):
        body = _parse_id_body(raw, True)
        with self._locked():
            txn = self._load()
            if not txn or txn.get('id') != body['id']:
                return self._fail('transaction not found', None if not txn or txn.get('id') != body['id'] else txn)
            if txn['state'] in TERMINAL_STATES:
                return self._fail('transaction is no longer pending', txn)
            if self._expired(txn):
                self._rollback(txn, 'confirm')
                return self._fail('transaction expired', txn)
            if txn['state'] != 'awaiting-confirmation':
                return self._fail('transaction is not ready to confirm', txn)
            if not self._confirm_ready(txn, body['server_address']):
                return self._fail('candidate is not active at the reached address', txn)
            priority = 0
            if txn.get('original'):
                stored = txn['original']['settings']['connection'].get('autoconnect-priority')
                if stored:
                    priority = int(stored['v'])
            if priority >= 999:
                return self._fail('cannot outrank existing connection priority', txn)
            settings = json.loads(_canonical(txn['candidate']['settings']))
            settings['connection']['autoconnect'] = _tagged('b', True)
            settings['connection']['autoconnect-priority'] = _tagged('i', min(999, priority + 1))
            try:
                self._update_candidate(txn, settings, save=True)
                ready = not self._expired(txn) and self._confirm_ready(txn, body['server_address'])
                if ready:
                    self._retire_owned_original(txn)
                    ready = not self._expired(txn) and self._confirm_ready(txn, body['server_address'])
            except NetError as exc:
                self._rollback(txn, 'confirm')
                return self._fail('confirm failed: {0}'.format(exc), txn)
            if not ready:
                self._rollback(txn, 'confirm')
                return self._fail('connection changed or confirmation expired', txn)
            txn['candidate']['settings'] = settings
            txn['state'] = 'confirmed'
            txn['last_result'] = self._result('confirm', True, None)
            self._save(txn)
            return self._ok(txn)

    def cmd_rollback(self, raw):
        body = _parse_id_body(raw, False)
        with self._locked():
            txn = self._load()
            if not txn or txn.get('id') != body['id']:
                return self._fail('transaction not found', None)
            if txn['state'] == 'confirmed':
                return self._fail('transaction already confirmed', txn)
            if txn['state'] == 'rolled_back':
                return self._ok(txn)
            if txn['state'] == 'failed':
                return self._fail('transaction failed before the network changed', txn)
            restored = self._rollback(txn, 'rollback')
            if restored:
                return self._ok(txn)
            return self._fail(txn['last_result']['error'] or 'rollback failed', txn)

    def cmd_tick(self):
        txn = self._load()
        if not txn or txn['state'] in TERMINAL_STATES:
            return self._body(True, None, [], None, None)
        try:
            lock = self.fs.try_lock()
        except NetError as exc:
            if 'busy' in str(exc):
                return self._body(True, None, [], self._public_pending(txn), None)
            raise
        try:
            txn = self._load()
            if not txn or txn['state'] in TERMINAL_STATES:
                return self._body(True, None, [], None, None)
            if txn['state'] == 'rollback-failed' or txn['state'] == 'applying' or self._expired(txn):
                restored = self._rollback(txn, 'tick')
                if restored:
                    return self._ok(txn)
                return self._fail(txn['last_result']['error'] or 'rollback failed', txn)
            return self._body(True, None, [], self._public_pending(txn), txn.get('last_result'))
        finally:
            lock.release()

    def cmd_restore_confirmed(self):
        txn = self._load()
        if not txn or txn.get('state') != 'confirmed':
            return self._body(True, None, [], self._public_pending(txn), None)
        with self._locked():
            txn = self._load()
            if not txn or txn.get('state') != 'confirmed':
                return self._body(True, None, [], self._public_pending(txn), None)
            uuid = txn['candidate']['uuid']
            if not self.fs.profile_saved(uuid):
                raise NetError('confirmed connection is no longer saved')
            path = self._lookup_uuid(uuid)
            if path is None:
                raise NetError('confirmed connection is unavailable')
            settings = self._connection_settings(path)
            connection = settings.get('connection', {})
            if connection.get('autoconnect', {}).get('v') is False:
                return self._body(True, None, [], None, None)
            if (_manual_endpoint(settings) != _manual_endpoint(txn['candidate']['settings']) or
                    (txn['mode'] == 'dhcp' and not self._dhcp_profile(settings))):
                raise NetError('confirmed profile changed externally; recovery paused')
            observed = self._observe(txn['interface'])
            active = observed.get('active')
            owner = self.owners.get(NM_SERVICE)
            boot = self._boot()
            if not owner or not boot:
                raise NetError('network recovery identity unavailable')
            if owner == txn.get('nm_owner') and boot == txn.get('nm_owner_boot', txn.get('boot_id')):
                return self._body(True, None, [], None, None)
            if active and active['uuid'] != uuid and self.fs.profile_saved(active['uuid']):
                return self._body(True, None, [], None, None)
            if not self._activation_ready(txn):
                # Reclaim only the confirmed profile after a daemon restart, never
                # a pending transaction or another explicitly saved active profile.
                self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'ActivateConnection',
                           [self.dbus.ObjectPath(path), self.dbus.ObjectPath(observed['device']),
                            self.dbus.ObjectPath(NULL_PATH)])
                for _attempt in range(POLL_ATTEMPTS):
                    if self._activation_ready(txn):
                        break
                    if self.monotonic() >= self.call_deadline:
                        raise NetError('confirmed connection recovery timed out')
                    self.sleep(POLL_INTERVAL)
                else:
                    raise NetError('confirmed connection did not recover')
            txn['nm_owner'] = owner
            txn['nm_owner_boot'] = boot
            self._save(txn)
            return self._ok(txn)

    def interfaces(self):
        paths = self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'GetDevices', [])
        found = []
        for path in paths:
            path = str(path)
            if int(self._prop(path, NM_DEVICE_IFACE, 'DeviceType')) != DEVICE_TYPE_ETHERNET:
                continue
            name = _text(self._prop(path, NM_DEVICE_IFACE, 'Interface'))
            item = {'interface': name, 'address': None, 'prefix': None, 'gateway': None,
                    'dns': [], 'mode': None, 'saved': False}
            active = str(self._prop(path, NM_DEVICE_IFACE, 'ActiveConnection'))
            ip4 = str(self._prop(path, NM_DEVICE_IFACE, 'Ip4Config'))
            if active and active != NULL_PATH:
                connection = str(self._prop(active, NM_ACTIVE_IFACE, 'Connection'))
                ip4 = str(self._prop(active, NM_ACTIVE_IFACE, 'Ip4Config')) or ip4
                uuid = _text(self._prop(active, NM_ACTIVE_IFACE, 'Uuid'))
                item['saved'] = self.fs.profile_saved(uuid)
                item['mode'] = self._mode_of(connection)
            if ip4 and ip4 != NULL_PATH:
                live = self._read_ip4(ip4)
                item.update(live)
            found.append(item)
        return sorted(found, key=lambda item: item['interface'])

    def _mode_of(self, connection):
        try:
            settings = decode_settings(self._dbus(NM_SERVICE, connection, NM_CONNECTION_IFACE, 'GetSettings', []), self.dbus)
            method = settings.get('ipv4', {}).get('method', {}).get('v')
        except NetError:
            return None
        return MODES.get(method)

    def _bring_up(self, txn):
        original = None if not txn.get('original') else txn['original']['settings']
        settings = _candidate_settings(original, txn['id'], txn['candidate']['uuid'], txn['interface'], txn)
        path = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE, 'AddConnectionUnsaved',
                          [encode_settings(settings, self.dbus)])
        txn['candidate']['path'] = str(path)
        txn['candidate']['settings'] = settings
        self._save(txn)
        device = self._device(txn['interface'])
        self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'ActivateConnection',
                   [self.dbus.ObjectPath(str(path)), self.dbus.ObjectPath(device), self.dbus.ObjectPath(NULL_PATH)])
        self._wait_ready(txn)
        observed = self._observe(txn['interface'])
        if not observed['ip']['address']:
            raise NetError('candidate address did not appear')
        txn['address'] = observed['ip']['address']
        txn['state'] = 'awaiting-confirmation'
        self._save(txn)

    def _wait_ready(self, txn):
        for _attempt in range(POLL_ATTEMPTS):
            if self.call_deadline is not None and self.monotonic() >= self.call_deadline:
                break
            try:
                if self._activation_ready(txn):
                    return
            except NetError:
                # ActiveConnection and IP4Config objects are replaced during activation.
                pass
            self.sleep(POLL_INTERVAL)
        raise NetError('timed out waiting for the network change')

    def _activation_ready(self, txn):
        observed = self._observe(txn['interface'])
        active = observed.get('active')
        if not active or observed['device_state'] != DEVICE_STATE_ACTIVATED or active['state'] != AC_STATE_ACTIVATED:
            return False
        if active['uuid'] != txn['candidate']['uuid']:
            return False
        profile = self._connection_settings(active['connection'])
        if txn['mode'] == 'dhcp':
            return self._dhcp_profile(profile) and active['dhcp4'] != NULL_PATH and bool(observed['ip']['address'])
        return self._static_profile(profile, txn, observed)

    def _confirm_ready(self, txn, server_address):
        if not self._activation_ready(txn):
            return False
        observed = self._observe(txn['interface'])
        return self._address_is(observed, server_address) and observed['ip']['address'] == server_address

    def _static_profile(self, profile, txn, observed):
        if profile.get('ipv4', {}).get('method', {}).get('v') != 'manual':
            return False
        ip = observed['ip']
        return ip['address'] == txn['address'] and ip['prefix'] == txn['prefix'] and ip['gateway'] == txn['gateway'] and sorted(ip['dns']) == sorted(txn['dns'])

    def _dhcp_profile(self, profile):
        ipv4 = profile.get('ipv4', {})
        if ipv4.get('method', {}).get('v') != 'auto':
            return False
        if ipv4.get('addresses', {}).get('v'):
            return False
        if ipv4.get('address-data', {}).get('v'):
            return False
        if ipv4.get('gateway', {}).get('v'):
            return False
        if ipv4.get('dns', {}).get('v'):
            return False
        return True

    def _address_is(self, observed, address):
        return bool(observed.get('ip')) and observed['ip'].get('address') == address

    def _rollback(self, txn, action):
        if txn['state'] in TERMINAL_STATES:
            return txn['state'] == 'rolled_back'
        try:
            self._restore(txn)
        except NetError as exc:
            txn['state'] = 'rollback-failed'
            txn['last_result'] = self._result(action, False, str(exc))
            self._save(txn)
            return False
        except Exception as exc:
            txn['state'] = 'rollback-failed'
            txn['last_result'] = self._result(action, False, 'rollback failed')
            self._save(txn)
            return False
        txn['state'] = 'rolled_back'
        txn['last_result'] = self._result(action, True, None)
        self._save(txn)
        return True

    def _restore(self, txn):
        original = txn.get('original')
        device = self._device(txn['interface'])
        active = str(self._prop(device, NM_DEVICE_IFACE, 'ActiveConnection'))
        if active and active != NULL_PATH:
            current = _text(self._prop(active, NM_ACTIVE_IFACE, 'Uuid'))
            expected = [txn['candidate']['uuid']]
            if original and original['active']:
                expected.append(original['uuid'])
            boot = self._boot()
            rebooted = bool(boot and boot != txn['boot_id'])
            owner = self.owners.get(NM_SERVICE)
            restarted = bool(owner and txn.get('nm_owner') and owner != txn['nm_owner'])
            if current not in expected and not rebooted and not restarted:
                raise NetError('connection changed externally; automatic rollback paused')
        if original:
            path = self._restore_original(original)
        if original and original['active']:
            if not self._original_is_active(original, path):
                self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'ActivateConnection',
                           [self.dbus.ObjectPath(path), self.dbus.ObjectPath(device), self.dbus.ObjectPath(NULL_PATH)])
                self._wait_original(original)
            elif not self._original_matches(original):
                raise NetError('original connection did not return')
        else:
            active = str(self._prop(device, NM_DEVICE_IFACE, 'ActiveConnection'))
            if active and active != NULL_PATH:
                current = _text(self._prop(active, NM_ACTIVE_IFACE, 'Uuid'))
                if current == txn['candidate']['uuid']:
                    self._dbus(NM_SERVICE, device, NM_DEVICE_IFACE, 'Disconnect', [])
            for _attempt in range(POLL_ATTEMPTS):
                if not self._candidate_still_active(txn):
                    break
                if self.call_deadline is not None and self.monotonic() >= self.call_deadline:
                    raise NetError('timed out disconnecting candidate')
                self.sleep(POLL_INTERVAL)
            else:
                raise NetError('candidate was not disconnected')
        self._delete_candidate(txn)
        if self._candidate_still_active(txn):
            raise NetError('candidate was not removed')

    def _restore_original(self, original):
        located = self._locate(original)
        if located is None:
            method = 'AddConnection' if original.get('persistent', not original['unsaved']) else 'AddConnectionUnsaved'
            path = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE, method,
                              [encode_settings(original['settings'], self.dbus)])
            return str(path)
        path, settings, unsaved = located
        def stable(value):
            value = json.loads(_canonical(value))
            value['connection'].pop('timestamp', None)
            return value
        same = stable(settings) == stable(original['settings'])
        if not same:
            raise NetError('original profile changed externally; automatic rollback paused')
        return path

    def _retire_owned_original(self, txn):
        original = txn.get('original')
        if not original or original['uuid'] == txn['candidate']['uuid']:
            return
        connection = original['settings'].get('connection', {})
        name = connection.get('id', {}).get('v', '')
        if not re.match(r'^blakestream-[0-9a-f]{8}$', name):
            return
        located = self._locate(original)
        if located is None:
            return
        path, current, _unsaved = located
        expected = json.loads(_canonical(original['settings']))
        current = json.loads(_canonical(current))
        for profile in (expected, current):
            profile['connection'].pop('timestamp', None)
        if current != expected:
            raise NetError('original profile changed externally; confirmation paused')
        observed = self._observe(txn['interface'])
        active = observed.get('active')
        if not active or active['uuid'] != txn['candidate']['uuid']:
            raise NetError('candidate is no longer active')
        # NM 1.2 can assume an old static profile on restart despite lower priority.
        # Keep its full rollback snapshot in the journal until confirmation commits.
        self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'Delete', [])

    def _locate(self, original):
        path = self._lookup_uuid(original['uuid'])
        if path is None:
            return None
        settings = self._connection_settings(path)
        if settings['connection']['uuid']['v'] != original['uuid']:
            raise NetError('original connection identity changed')
        return path, settings, bool(self._prop(path, NM_CONNECTION_IFACE, 'Unsaved'))

    def _lookup_uuid(self, uuid):
        paths = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE, 'ListConnections', [])
        if len(paths) > 64:
            raise NetError('too many connection profiles')
        for path in paths:
            settings = self._connection_settings(str(path))
            if settings.get('connection', {}).get('uuid', {}).get('v') == uuid:
                return str(path)
        # Absence requires a successful, complete listing, never a timed-out lookup.
        return None

    def _wait_original(self, original):
        for _attempt in range(POLL_ATTEMPTS):
            if self.call_deadline is not None and self.monotonic() >= self.call_deadline:
                break
            try:
                if self._original_matches(original):
                    return
            except NetError:
                pass
            self.sleep(POLL_INTERVAL)
        raise NetError('original connection did not return')

    def _original_is_active(self, original, path):
        try:
            return self._original_matches(original)
        except NetError:
            return False

    def _original_matches(self, original):
        observed = self._observe(original_interface(original))
        active = observed.get('active')
        if not active or observed['device_state'] != DEVICE_STATE_ACTIVATED or active['state'] != AC_STATE_ACTIVATED:
            return False
        if active['uuid'] != original['uuid']:
            return False
        expected = _manual_endpoint(original['settings'])
        if expected['mode'] == 'dhcp':
            return bool(observed['ip']['address']) and active['dhcp4'] != NULL_PATH
        ip = observed['ip']
        return ip['address'] == expected['address'] and ip['prefix'] == expected['prefix'] and ip['gateway'] == expected['gateway'] and sorted(ip['dns']) == expected['dns']

    def _candidate_still_active(self, txn):
        try:
            observed = self._observe(txn['interface'])
        except NetError:
            return True
        active = observed.get('active')
        return bool(active) and active.get('uuid') == txn['candidate']['uuid']

    def _delete_candidate(self, txn):
        uuid = txn['candidate']['uuid']
        if txn.get('original') and txn['original']['uuid'] == uuid:
            raise NetError('refusing to delete the original connection')
        if self._candidate_still_active(txn):
            raise NetError('candidate still active')
        path = self._lookup_uuid(uuid)
        if path is None:
            return
        current = self._connection_settings(path)
        if current['connection']['uuid']['v'] != uuid:
            raise NetError('refusing to delete the original connection')
        self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'Delete', [])

    def _update_candidate(self, txn, settings, save):
        uuid = txn['candidate']['uuid']
        path = self._lookup_uuid(uuid)
        if path is None:
            raise NetError('candidate connection is missing')
        current = self._connection_settings(path)
        if current['connection']['uuid']['v'] != uuid or settings['connection']['uuid']['v'] != uuid:
            raise NetError('candidate identity changed')
        method = 'Update' if save else 'UpdateUnsaved'
        self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, method, [encode_settings(settings, self.dbus)])

    def _select_profile(self, interface):
        device = self._device(interface)
        if not bool(self._prop(device, NM_DEVICE_IFACE, 'Managed')):
            raise NetError('interface is not managed')
        active = str(self._prop(device, NM_DEVICE_IFACE, 'ActiveConnection'))
        if active and active != NULL_PATH:
            path = str(self._prop(active, NM_ACTIVE_IFACE, 'Connection'))
            return self._snapshot_connection(path, interface, device, True)
        available = [str(item) for item in self._prop(device, NM_DEVICE_IFACE, 'AvailableConnections')]
        available = [item for item in available if item and item != NULL_PATH]
        if len(available) > 1:
            raise NetError('multiple connection profiles')
        if len(available) == 1:
            return self._snapshot_connection(available[0], interface, device, False)
        return None

    def _snapshot_connection(self, path, interface, device, active):
        settings = self._connection_settings(path)
        hwaddr = self._hwaddr(device)
        _validate_profile(settings, interface, hwaddr)
        uuid = settings['connection']['uuid']['v']
        if not isinstance(uuid, str) or not UUID_RE.match(uuid):
            raise NetError('unsupported profile format')
        return {
            'path': path,
            'uuid': uuid,
            'unsaved': bool(self._prop(path, NM_CONNECTION_IFACE, 'Unsaved')),
            'persistent': self.fs.profile_saved(uuid),
            'settings': settings,
            'active': bool(active),
            'interface': interface,
        }

    def _hwaddr(self, device):
        try:
            value = self._prop(device, NM_DEVICE_WIRED_IFACE, 'HwAddress')
        except NetError:
            return None
        text = _text(value).strip().lower()
        if not re.match(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$', text):
            return None
        return text

    def _device(self, interface):
        for path in self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'GetDevices', []):
            path = str(path)
            if int(self._prop(path, NM_DEVICE_IFACE, 'DeviceType')) != DEVICE_TYPE_ETHERNET:
                continue
            if _text(self._prop(path, NM_DEVICE_IFACE, 'Interface')) == interface:
                return path
        raise NetError('unsupported interface')

    def _connection_settings(self, path):
        return decode_settings(self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'GetSettings', []), self.dbus)

    def _observe(self, interface):
        device = self._device(interface)
        state = int(self._prop(device, NM_DEVICE_IFACE, 'State'))
        active_path = str(self._prop(device, NM_DEVICE_IFACE, 'ActiveConnection'))
        active = None
        ip4 = str(self._prop(device, NM_DEVICE_IFACE, 'Ip4Config'))
        if active_path and active_path != NULL_PATH:
            active = {
                'state': int(self._prop(active_path, NM_ACTIVE_IFACE, 'State')),
                'uuid': _text(self._prop(active_path, NM_ACTIVE_IFACE, 'Uuid')),
                'connection': str(self._prop(active_path, NM_ACTIVE_IFACE, 'Connection')),
                'ip4': str(self._prop(active_path, NM_ACTIVE_IFACE, 'Ip4Config')),
                'dhcp4': str(self._prop(active_path, NM_ACTIVE_IFACE, 'Dhcp4Config')),
            }
            if active['ip4'] and active['ip4'] != NULL_PATH:
                ip4 = active['ip4']
        ip = {'address': None, 'prefix': None, 'gateway': None, 'dns': []}
        if ip4 and ip4 != NULL_PATH:
            ip = self._read_ip4(ip4)
        return {'device_state': state, 'active': active, 'ip': ip, 'device': device}

    def _read_ip4(self, path):
        data = self._prop(path, NM_IP4_IFACE, 'AddressData')
        address = None
        prefix = None
        if data:
            first = list(data)[0]
            raw_address = self._entry(first, 'address')
            raw_prefix = self._entry(first, 'prefix')
            address = _canonical_ipv4(_text(raw_address))
            prefix = int(raw_prefix)
        gateway = _text(self._prop(path, NM_IP4_IFACE, 'Gateway')).strip()
        gateway = _canonical_ipv4(gateway) if gateway else None
        dns = [_ip_from_wire(int(item)) for item in self._prop(path, NM_IP4_IFACE, 'Nameservers')]
        return {'address': address, 'prefix': prefix, 'gateway': gateway, 'dns': dns}

    def _entry(self, entry, key):
        return entry[key]

    def _prop(self, path, iface, name):
        return self._dbus(NM_SERVICE, path, DBUS_PROPS, 'Get', [iface, name])

    def _dbus(self, service, path, iface, method, args, what='NetworkManager'):
        try:
            remaining = 2 if self.call_deadline is None else self.call_deadline - self.monotonic()
            if remaining <= 0:
                raise NetError('network operation timed out')
            if service not in self.owners:
                self.owners[service] = self.bus.get_name_owner(service)
            # Bind one invocation to one daemon incarnation; never reuse object paths after restart.
            proxy = self.dbus.Interface(self.bus.get_object(self.owners[service], path, introspect=False), iface)
            return getattr(proxy, method)(*args, timeout=min(2.0, remaining))
        except NetError:
            raise
        except Exception as exc:
            raise NetError('{0} call failed: {1}'.format(what, exc))

    def _locked(self):
        return _LockCtx(self)

    def _load(self):
        raw = self.fs.read_txn()
        if raw is None:
            return None
        if isinstance(raw, bytes):
            try:
                raw = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise NetError('transaction state is corrupt')
        if not isinstance(raw, str) or len(raw) > MAX_TXN_BYTES:
            raise NetError('transaction state is corrupt')
        try:
            data = json.loads(raw)
        except ValueError:
            raise NetError('transaction state is corrupt')
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('state') not in ALL_STATES:
            raise NetError('transaction state is corrupt')
        if not isinstance(data.get('id'), str) or not isinstance(data.get('candidate'), dict):
            raise NetError('transaction state is corrupt')
        return data

    def _save(self, txn):
        text = json.dumps(txn, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        if len(text) > MAX_TXN_BYTES:
            raise NetError('transaction is too large')
        self.fs.write_txn(text)

    def _boot(self):
        raw = self.fs.read_boot_id()
        if not isinstance(raw, str):
            return None
        raw = raw.strip()
        if not BOOT_ID_RE.match(raw):
            return None
        return raw

    def _require_boot(self):
        boot_id = self._boot()
        if not boot_id:
            raise NetError('boot identity unavailable')
        return boot_id

    def _expired(self, txn):
        current = self._boot()
        if not current or current != txn.get('boot_id'):
            return True
        now = self.monotonic()
        start = txn.get('created_mono')
        deadline = txn.get('deadline_mono')
        if not isinstance(start, (int, float)) or not isinstance(deadline, (int, float)):
            return True
        return now < start or now >= deadline or abs(deadline - start - ROLLBACK_SECONDS) > 0.001

    def _new_id(self):
        return binascii.hexlify(self.urandom(16)).decode('ascii')

    def _new_uuid(self):
        value = binascii.hexlify(self.urandom(16)).decode('ascii')
        return '{0}-{1}-{2}-{3}-{4}'.format(value[0:8], value[8:12], value[12:16], value[16:20], value[20:32])

    def _result(self, action, ok, error):
        return {'action': action, 'ok': bool(ok), 'error': error, 'at': int(self.clock())}

    def _public_pending(self, txn):
        if not txn or txn.get('state') in TERMINAL_STATES:
            return None
        remaining = 0 if self._expired(txn) else max(0, int(txn['deadline_mono'] - self.monotonic()))
        return {
            'id': txn['id'],
            'state': txn['state'],
            'mode': txn['mode'],
            'address': txn.get('address'),
            'deadline': txn['deadline'],
            'seconds_remaining': remaining,
        }

    def _ok(self, txn):
        try:
            interfaces = self.interfaces()
        except NetError:
            interfaces = []
        return self._body(True, None, interfaces, self._public_pending(txn), None if not txn else txn.get('last_result'))

    def _fail(self, message, txn=None, read_nm=False):
        interfaces = []
        if read_nm and not self._reporting and self.geteuid() == 0:
            self._reporting = True
            try:
                interfaces = self.interfaces()
            except Exception:
                interfaces = []
            finally:
                self._reporting = False
        last = None if not txn else txn.get('last_result')
        pending = None
        if txn and txn.get('state') not in TERMINAL_STATES:
            try:
                pending = self._public_pending(txn)
            except Exception:
                pending = None
        return self._body(False, message, interfaces, pending, last)

    def _body(self, ok, error, interfaces, pending, last_result):
        return {
            'ok': bool(ok),
            'error': error,
            'interfaces': interfaces,
            'pending': pending,
            'last_result': last_result,
        }


def original_interface(original):
    if original.get('interface'):
        return original['interface']
    name = original.get('settings', {}).get('connection', {}).get('interface-name', {}).get('v')
    if not name:
        raise NetError('original connection did not return')
    return name


class _LockCtx(object):
    def __init__(self, service):
        self.service = service
        self.lock = None

    def __enter__(self):
        self.service.fs.ensure_state_dir()
        self.lock = self.service.fs.try_lock()
        return self.lock

    def __exit__(self, exc_type, exc, tb):
        if self.lock is not None:
            self.lock.release()
        return False


def real_service(journal_only=False):
    if journal_only:
        return Service(None, None, PosixFS(), time.time, time.sleep, os.urandom, os.geteuid)
    try:
        import dbus
    except ImportError:
        raise NetError('NetworkManager unavailable')
    return Service(dbus.SystemBus(), dbus, PosixFS(), time.time, time.sleep, os.urandom, os.geteuid)


def _empty(error):
    return {'ok': False, 'error': error, 'interfaces': [], 'pending': None, 'last_result': None}


def main(argv, stdin, stdout, service=None):
    result = None
    try:
        if not isinstance(argv, (list, tuple)) or len(argv) != 2 or argv[1] not in VERBS:
            result = _empty('invalid arguments')
        elif service is None and os.geteuid() != 0:
            result = _empty('root required')
        else:
            if service is None:
                service = real_service(journal_only=argv[1] == 'pending')
            raw = ''
            if argv[1] in INPUT_VERBS:
                raw = stdin.read(MAX_STDIN + 1)
                if raw is None:
                    raw = ''
                if len(raw) > MAX_STDIN:
                    result = service.dispatch('status', '')
                    result['ok'] = False
                    result['error'] = 'input too large'
            if result is None:
                result = service.dispatch(argv[1], raw)
    except NetError as exc:
        result = _empty(str(exc))
    except Exception:
        result = _empty('internal error')
    text = json.dumps(result, sort_keys=True, ensure_ascii=True) + '\n'
    stdout.write(text)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    def deadline(_signum, _frame):
        raise DeadlineExceeded()

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 35 if len(sys.argv) == 2 and sys.argv[1] in ('activate', 'tick', 'restore-confirmed') else 12)
    try:
        result = main(sys.argv, sys.stdin, sys.stdout)
    except DeadlineExceeded:
        sys.stdout.write(json.dumps(_empty('network operation timed out; refresh status')) + '\n')
        result = 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.exit(result)
