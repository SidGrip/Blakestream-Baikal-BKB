#!/usr/bin/python3
"""Root-only NetworkManager helper for Settings Wi-Fi.

Target runtime is Python 3.5 with dbus-python and NetworkManager 1.2.6.
Supports open and WPA-PSK only. WPA3, enterprise and WEP fail closed.
Passwords travel on stdin or a root-only secret file, never argv.

Radio On/Off uses NetworkManager WirelessEnabled (software radio), not
NetworkingEnabled. A first-run provision writes WirelessEnabled=false
before NetworkManager starts. Existing radio.json is never replaced by
the image default.
"""
from __future__ import print_function

import binascii
import configparser
import errno
import hashlib
import importlib.util
import json
import math
import os
import signal
import stat
import sys
import time

_BIN = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    'blakestream_network_settings', os.path.join(_BIN, 'network-settings.py'))
net = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(net)

NM_SERVICE = net.NM_SERVICE
NM_PATH = net.NM_PATH
NM_IFACE = net.NM_IFACE
NM_SETTINGS_PATH = net.NM_SETTINGS_PATH
NM_SETTINGS_IFACE = net.NM_SETTINGS_IFACE
NM_CONNECTION_IFACE = net.NM_CONNECTION_IFACE
NM_DEVICE_IFACE = net.NM_DEVICE_IFACE
NM_ACTIVE_IFACE = net.NM_ACTIVE_IFACE
NM_IP4_IFACE = net.NM_IP4_IFACE
DBUS_PROPS = net.DBUS_PROPS
SD_SERVICE = net.SD_SERVICE
SD_PATH = net.SD_PATH
SD_MANAGER = net.SD_MANAGER
NULL_PATH = net.NULL_PATH
DEVICE_STATE_ACTIVATED = net.DEVICE_STATE_ACTIVATED
SCAN_READY_STATES = (30, DEVICE_STATE_ACTIVATED, 120)
AC_STATE_ACTIVATED = net.AC_STATE_ACTIVATED

NM_WIFI_IFACE = 'org.freedesktop.NetworkManager.Device.Wireless'
NM_AP_IFACE = 'org.freedesktop.NetworkManager.AccessPoint'
DEVICE_TYPE_WIFI = 2
DEVICE_TYPE_ETHERNET = net.DEVICE_TYPE_ETHERNET

AP_FLAG_PRIVACY = 0x1
SEC_PAIR_WEP40 = 0x1
SEC_PAIR_WEP104 = 0x2
SEC_PAIR_TKIP = 0x4
SEC_PAIR_CCMP = 0x8
SEC_GROUP_WEP40 = 0x10
SEC_GROUP_WEP104 = 0x20
SEC_GROUP_TKIP = 0x40
SEC_GROUP_CCMP = 0x80
SEC_KEY_MGMT_PSK = 0x100
SEC_KEY_MGMT_802_1X = 0x200
SEC_KNOWN = (SEC_PAIR_WEP40 | SEC_PAIR_WEP104 | SEC_PAIR_TKIP | SEC_PAIR_CCMP |
             SEC_GROUP_WEP40 | SEC_GROUP_WEP104 | SEC_GROUP_TKIP | SEC_GROUP_CCMP |
             SEC_KEY_MGMT_PSK | SEC_KEY_MGMT_802_1X)

STATE_DIR = '/var/lib/blakestream-wifi'
NM_STATE_PATH = '/var/lib/NetworkManager/NetworkManager.state'
BOOT_ID_PATH = net.BOOT_ID_PATH
APPLY_UNIT = 'blakestream-wifi-apply.service'
ROLLBACK_UNIT = 'blakestream-wifi-rollback.timer'
ROLLBACK_SECONDS = 90
CONNECT_SECONDS = 60
APPLY_BUDGET_SECONDS = 80
APPLY_GRACE_SECONDS = 1
POLL_ATTEMPTS = 80
POLL_INTERVAL = 0.35
SCAN_ATTEMPTS = 28
SCAN_INTERVAL = 0.25
SCAN_COOLDOWN_SECONDS = 15
MAX_STDIN = 8192
MAX_TXN_BYTES = net.MAX_TXN_BYTES
MAX_SCAN_BYTES = 16384
MAX_APS = 64
MAX_SAVED = 32
MAX_CONNECTIONS = 64
WIFI_PRIORITY = -10
WIFI_METRIC = 600
PROFILE_PREFIX = 'blakestream-wifi-'

VERBS = ('status', 'pending', 'scan', 'connect', 'disconnect', 'forget',
         'radio', 'activate', 'tick', 'provision', 'sync-radio', 'migrate-secrets')
INPUT_VERBS = ('scan', 'connect', 'disconnect', 'forget', 'radio')
WORKER_VERBS = ('activate', 'tick', 'provision', 'sync-radio', 'migrate-secrets')
TERMINAL_STATES = ('connected', 'failed', 'rolled_back')
PENDING_STATES = ('pending', 'applying', 'rollback-failed')
ALL_STATES = TERMINAL_STATES + PENDING_STATES
NAMED_FILES = ('transaction.json', 'secret', 'scan.json', 'radio.json')
SECURITIES = ('open', 'wpa-psk')

UUID_RE = net.UUID_RE
BOOT_ID_RE = net.BOOT_ID_RE
IFACE_RE = net.IFACE_RE


class WifiError(Exception):
    pass


class DeadlineExceeded(BaseException):
    pass


def _as_net(exc):
    text = str(exc)
    if 'network settings' in text:
        text = text.replace('network settings', 'Wi-Fi settings')
    return WifiError(text)


def _text(value):
    try:
        return net._text(value)
    except net.NetError:
        raise WifiError('unsupported Wi-Fi value')


def _is_int(value):
    return net._is_int(value)


def _parse_object(raw):
    try:
        return net._parse_object(raw)
    except net.NetError as exc:
        raise _as_net(exc)


def _ssid_bytes(value):
    if not isinstance(value, str):
        raise WifiError('invalid SSID')
    if '\x00' in value:
        raise WifiError('invalid SSID')
    try:
        data = value.encode('utf-8')
    except UnicodeEncodeError:
        raise WifiError('invalid SSID')
    if not data or len(data) > 32:
        raise WifiError('invalid SSID')
    return data


def _ssid_text(data):
    if isinstance(data, list):
        try:
            data = bytes(bytearray(data))
        except (TypeError, ValueError):
            return None
    if isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes) or not data or len(data) > 32 or b'\x00' in data:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _parse_psk(value):
    if not isinstance(value, str):
        raise WifiError('invalid password')
    if '\x00' in value or any(ord(char) < 32 for char in value):
        raise WifiError('invalid password')
    if len(value) == 64:
        for char in value:
            if char not in '0123456789abcdefABCDEF':
                raise WifiError('invalid password')
        return value
    if len(value) < 8 or len(value) > 63:
        raise WifiError('invalid password')
    return value


def derived_psk(ssid, value):
    value = _parse_psk(value)
    if not isinstance(ssid, bytes) or not 1 <= len(ssid) <= 32 or b'\x00' in ssid:
        raise WifiError('invalid SSID')
    if len(value) == 64:
        return value.lower()
    try:
        password = value.encode('utf-8')
    except UnicodeEncodeError:
        raise WifiError('invalid password')
    if len(password) > 63:
        raise WifiError('invalid password')
    # WPA/WPA2 fixes these PBKDF2 parameters; this is a network credential, not encryption.
    return binascii.hexlify(hashlib.pbkdf2_hmac('sha1', password, ssid, 4096, 32)).decode('ascii')


def classify_ap(flags, wpa, rsn):
    try:
        flags = int(flags)
        wpa = int(wpa)
        rsn = int(rsn)
    except (TypeError, ValueError):
        return 'unsupported'
    if flags < 0 or wpa < 0 or rsn < 0:
        return 'unsupported'
    combined = wpa | rsn
    if combined & ~SEC_KNOWN:
        return 'unsupported'
    if combined & SEC_KEY_MGMT_802_1X:
        return 'unsupported'
    if combined & SEC_KEY_MGMT_PSK:
        return 'wpa-psk'
    if combined:
        return 'unsupported'
    if flags & AP_FLAG_PRIVACY:
        return 'unsupported'
    return 'open'


def _redact(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and net._is_secret_key(key):
                continue
            out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def encode_wifi(spec, dbus_mod, psk=None):
    if spec['security'] not in SECURITIES:
        raise WifiError('unsupported security')
    if spec['security'] == 'wpa-psk' and not isinstance(psk, str):
        raise WifiError('invalid password')
    if spec['security'] == 'open' and psk not in (None, ''):
        raise WifiError('open networks do not accept a password')
    connection = dbus_mod.Dictionary({
        'id': dbus_mod.String(spec['id'], variant_level=1),
        'uuid': dbus_mod.String(spec['uuid'], variant_level=1),
        'type': dbus_mod.String('802-11-wireless', variant_level=1),
        'interface-name': dbus_mod.String(spec['interface'], variant_level=1),
        'autoconnect': dbus_mod.Boolean(True, variant_level=1),
        'autoconnect-priority': dbus_mod.Int32(WIFI_PRIORITY, variant_level=1),
    }, signature='sv')
    wireless = {
        'ssid': dbus_mod.ByteArray(spec['ssid'], variant_level=1),
        'mode': dbus_mod.String('infrastructure', variant_level=1),
        'hidden': dbus_mod.Boolean(bool(spec['hidden']), variant_level=1),
    }
    if spec['security'] == 'wpa-psk':
        wireless['security'] = dbus_mod.String('802-11-wireless-security', variant_level=1)
    groups = {
        'connection': connection,
        '802-11-wireless': dbus_mod.Dictionary(wireless, signature='sv'),
        'ipv4': dbus_mod.Dictionary({
            'method': dbus_mod.String('auto', variant_level=1),
            'route-metric': dbus_mod.Int64(WIFI_METRIC, variant_level=1),
        }, signature='sv'),
        'ipv6': dbus_mod.Dictionary({
            'method': dbus_mod.String('auto', variant_level=1),
            'route-metric': dbus_mod.Int64(WIFI_METRIC, variant_level=1),
        }, signature='sv'),
    }
    if spec['security'] == 'wpa-psk':
        psk = derived_psk(spec['ssid'], psk)
        groups['802-11-wireless-security'] = dbus_mod.Dictionary({
            'key-mgmt': dbus_mod.String('wpa-psk', variant_level=1),
            'psk': dbus_mod.String(psk, variant_level=1),
        }, signature='sv')
    return dbus_mod.Dictionary(groups, signature='sa{sv}')


def _parse_connect(raw):
    data = _parse_object(raw)
    extra = set(data.keys()) - set(['ssid', 'security', 'psk', 'hidden'])
    if extra:
        raise WifiError('unknown setting')
    if 'ssid' not in data or 'security' not in data:
        raise WifiError('incomplete Wi-Fi settings')
    ssid = _ssid_bytes(data['ssid'])
    security = data['security']
    if security not in SECURITIES:
        raise WifiError('unsupported security')
    hidden = data.get('hidden', False)
    if hidden is not True and hidden is not False:
        raise WifiError('invalid hidden flag')
    psk = None
    if security == 'wpa-psk':
        if 'psk' not in data:
            raise WifiError('password required')
        psk = _parse_psk(data['psk'])
    elif 'psk' in data and data['psk'] not in ('', None):
        raise WifiError('open networks do not accept a password')
    return {'ssid': ssid, 'ssid_text': data['ssid'], 'security': security,
            'hidden': hidden, 'psk': psk}


def _parse_uuid_body(raw, required):
    data = _parse_object(raw)
    allowed = set(['uuid'] if required else ['uuid'])
    if required:
        if set(data.keys()) != allowed:
            raise WifiError('invalid request')
    else:
        if set(data.keys()) - allowed:
            raise WifiError('invalid request')
        if not data:
            return {'uuid': None}
    ident = data.get('uuid')
    if not isinstance(ident, str) or not UUID_RE.match(ident):
        raise WifiError('invalid connection')
    return {'uuid': ident}


def _parse_radio(raw):
    data = _parse_object(raw)
    if set(data.keys()) != set(['enabled']):
        raise WifiError('invalid request')
    enabled = data.get('enabled')
    if enabled is not True and enabled is not False:
        raise WifiError('invalid radio state')
    return enabled


def _parse_scan(raw):
    data = _parse_object(raw) if raw else {}
    if data:
        raise WifiError('invalid request')
    return {}


class WifiFS(net.PosixFS):
    def write_named(self, name, text):
        if name not in NAMED_FILES:
            raise net.NetError('invalid state file')
        if name == 'transaction.json':
            return self.write_txn(text)
        if not isinstance(text, str):
            raise net.NetError('cannot store Wi-Fi state')
        if name == 'secret' and (len(text) != 64 or any(c not in '0123456789abcdef' for c in text)):
            raise net.NetError('Wi-Fi handoff requires a derived key')
        data = text.encode('utf-8')
        limit = 256 if name == 'secret' else MAX_SCAN_BYTES
        if len(data) > limit:
            raise net.NetError('Wi-Fi state is too large')
        dirfd = self._parent(False)
        tmp = name + '.' + binascii.hexlify(os.urandom(8)).decode('ascii') + '.tmp'
        fd = -1
        try:
            flags = self._flags('O_WRONLY', 'O_CREAT', 'O_EXCL', 'O_NOFOLLOW', 'O_CLOEXEC')
            fd, err = self._openat(dirfd, tmp, flags, 0o600)
            if fd < 0:
                raise net.NetError('cannot store Wi-Fi state')
            os.fchmod(fd, 0o600)
            rest = data
            while rest:
                written = os.write(fd, rest)
                if written <= 0:
                    raise net.NetError('cannot store Wi-Fi state')
                rest = rest[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            self._reject_symlink_target(dirfd, name)
            os.rename(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
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

    def read_named(self, name):
        if name not in NAMED_FILES:
            raise net.NetError('invalid state file')
        if name == 'transaction.json':
            return self.read_txn()
        try:
            dirfd = self._parent(False)
        except net._Missing:
            return None
        try:
            return self._read_child(dirfd, name)
        finally:
            os.close(dirfd)

    def unlink_named(self, name):
        if name not in NAMED_FILES:
            raise net.NetError('invalid state file')
        try:
            dirfd = self._parent(False)
        except net._Missing:
            return
        try:
            try:
                info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    return
                raise net.NetError('cannot remove Wi-Fi state')
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise net.NetError('refusing symlink')
            os.unlink(name, dir_fd=dirfd)
        finally:
            os.close(dirfd)

    def wifi_keyfiles(self):
        found = []
        try:
            directory = os.open(self.profiles_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return found
            raise net.NetError('cannot inspect saved network profiles')
        try:
            names = os.listdir(directory)
            if len(names) > 128:
                raise net.NetError('too many saved network profiles')
            for name in names:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    continue
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(fd, 'r') as source:
                    raw = source.read(net.MAX_SETTINGS_BYTES + 1)
                if len(raw) > net.MAX_SETTINGS_BYTES:
                    continue
                parser = configparser.ConfigParser(interpolation=None)
                try:
                    parser.read_string(raw)
                except configparser.Error:
                    continue
                kind = parser.get('connection', 'type', fallback='')
                if kind in ('802-11-wireless', 'wifi'):
                    found.append(kind)
            return found
        except (OSError, UnicodeError):
            raise net.NetError('cannot inspect saved network profiles')
        finally:
            os.close(directory)


def read_nm_state_wireless(path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise WifiError('invalid NetworkManager state path')
    try:
        info = os.lstat(path)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None, None
        raise WifiError('cannot read NetworkManager radio state')
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise WifiError('refusing symlink')
    if info.st_size > 8192:
        raise WifiError('NetworkManager radio state is too large')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(fd, 8193)
    finally:
        os.close(fd)
    if len(raw) > 8192:
        raise WifiError('NetworkManager radio state is too large')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise WifiError('NetworkManager radio state is corrupt')
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error:
        raise WifiError('NetworkManager radio state is corrupt')
    if not parser.has_section('main'):
        return None, text
    networking = None
    wireless = None
    if parser.has_option('main', 'NetworkingEnabled'):
        networking = parser.getboolean('main', 'NetworkingEnabled')
    if parser.has_option('main', 'WirelessEnabled'):
        wireless = parser.getboolean('main', 'WirelessEnabled')
    return {'networking': networking, 'wireless': wireless}, text


def write_nm_state_wireless(path, enabled, existing_text=None):
    if not isinstance(path, str) or not path.startswith('/'):
        raise WifiError('invalid NetworkManager state path')
    directory = os.path.dirname(path)
    name = os.path.basename(path)
    if name != 'NetworkManager.state' or not directory:
        raise WifiError('invalid NetworkManager state path')
    try:
        dirinfo = os.lstat(directory)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return False
        raise WifiError('cannot write NetworkManager radio state')
    if stat.S_ISLNK(dirinfo.st_mode) or not stat.S_ISDIR(dirinfo.st_mode):
        raise WifiError('refusing symlink')
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if existing_text:
        try:
            parser.read_string(existing_text)
        except configparser.Error:
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
    if not parser.has_section('main'):
        parser.add_section('main')
    if not parser.has_option('main', 'NetworkingEnabled'):
        parser.set('main', 'NetworkingEnabled', 'true')
    parser.set('main', 'WirelessEnabled', 'true' if enabled else 'false')
    if not parser.has_option('main', 'WWANEnabled'):
        parser.set('main', 'WWANEnabled', 'true')
    handle = _stringio()
    parser.write(handle, space_around_delimiters=False)
    text = handle.getvalue()
    data = text.encode('utf-8')
    tmp = name + '.' + binascii.hexlify(os.urandom(8)).decode('ascii') + '.tmp'
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = -1
    try:
        try:
            info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise WifiError('refusing symlink')
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise WifiError('cannot write NetworkManager radio state')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dirfd)
        os.fchmod(fd, 0o644)
        rest = data
        while rest:
            written = os.write(fd, rest)
            if written <= 0:
                raise WifiError('cannot write NetworkManager radio state')
            rest = rest[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.rename(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        tmp = None
        os.fsync(dirfd)
        return True
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp:
            try:
                os.unlink(tmp, dir_fd=dirfd)
            except OSError:
                pass
        os.close(dirfd)


def _stringio():
    import io
    return io.StringIO()


def _boottime():
    # AP.LastSeen uses boot time, including suspend, not wall-clock/NTP time.
    with open('/proc/uptime') as handle:
        return float(handle.read().split()[0])


class Service(object):
    def __init__(self, bus, dbus_mod, fs, clock, sleep, urandom, geteuid,
                 grace=APPLY_GRACE_SECONDS, monotonic=time.monotonic,
                 nm_state_path=NM_STATE_PATH, boottime=_boottime):
        self.bus = bus
        self.dbus = dbus_mod
        self.fs = fs
        self.clock = clock
        self.sleep = sleep
        self.urandom = urandom
        self.geteuid = geteuid
        self.grace = grace
        self.monotonic = monotonic
        self.boottime = boottime
        self.nm_state_path = nm_state_path
        self.call_deadline = None
        self.owners = {}
        self._psk = None
        self._key = None
        self._reporting = False

    def dispatch(self, verb, raw):
        timeout = APPLY_BUDGET_SECONDS if verb == 'activate' else 32 if verb in ('tick', 'sync-radio', 'migrate-secrets') else 10
        self.call_deadline = self.monotonic() + timeout
        self.owners = {}
        self._psk = None
        self._key = None
        if self.geteuid() != 0:
            return self._fail('root required', read_nm=False)
        try:
            if verb == 'status':
                return self.cmd_status()
            if verb == 'pending':
                return self._body(True, None, pending=self._public_pending(self._load()))
            if verb == 'scan':
                _parse_scan(raw)
                return self.cmd_scan()
            if verb == 'connect':
                return self.cmd_connect(raw)
            if verb == 'disconnect':
                return self.cmd_disconnect(raw)
            if verb == 'forget':
                return self.cmd_forget(raw)
            if verb == 'radio':
                return self.cmd_radio(raw)
            if verb == 'activate':
                return self.cmd_activate()
            if verb == 'tick':
                return self.cmd_tick()
            if verb == 'provision':
                return self.cmd_provision()
            if verb == 'sync-radio':
                return self.cmd_sync_radio()
            if verb == 'migrate-secrets':
                return self.cmd_migrate_secrets()
            return self._fail('invalid arguments', read_nm=False)
        except WifiError as exc:
            return self._fail(self._scrub(str(exc)), read_nm=False)
        except net.NetError as exc:
            return self._fail(self._scrub(str(_as_net(exc))), read_nm=False)
        except Exception:
            return self._fail('internal error', read_nm=False)
        finally:
            self._psk = None
            self._key = None

    def _scrub(self, text):
        for secret in (self._key, self._psk):
            if secret and isinstance(text, str):
                text = text.replace(secret, '********')
        return text

    def cmd_provision(self):
        try:
            self.fs.ensure_state_dir()
        except net.NetError as exc:
            return self._body(True, None, extra={'provisioned': False, 'error': str(_as_net(exc))})
        radio = self._read_radio()
        created = False
        source = None
        enabled = False
        if radio is None:
            nm = None
            try:
                parsed, _unused = read_nm_state_wireless(self.nm_state_path)
                nm = parsed
            except WifiError:
                nm = None
            wireless = None if not nm else nm.get('wireless')
            profiles = []
            try:
                profiles = self.fs.wifi_keyfiles()
            except net.NetError:
                profiles = []
            if profiles and wireless is True:
                enabled = True
                source = 'user'
            else:
                enabled = False
                source = 'default'
            self._write_radio(enabled, source)
            created = True
        else:
            enabled = radio['enabled']
            source = radio.get('source')
        patched = False
        try:
            parsed, text = read_nm_state_wireless(self.nm_state_path)
            patched = write_nm_state_wireless(self.nm_state_path, enabled, text)
        except WifiError:
            patched = False
        return self._body(True, None, extra={
            'provisioned': True,
            'created': created,
            'radio': {'enabled': enabled, 'source': source, 'persisted': True},
            'nm_state_written': patched,
        })

    def cmd_sync_radio(self):
        radio = self._read_radio()
        if radio is None:
            return self.cmd_provision()
        try:
            parsed, text = read_nm_state_wireless(self.nm_state_path)
            write_nm_state_wireless(self.nm_state_path, radio['enabled'], text)
        except WifiError:
            pass
        if self.bus is None:
            return self._body(True, None, extra={'radio': radio})
        self._set_wireless_enabled(radio['enabled'])
        return self.cmd_status()

    def cmd_migrate_secrets(self):
        migrated = 0
        with self._locked():
            self._reject_busy()
            paths = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE, 'ListConnections', [])
            if len(paths) > MAX_CONNECTIONS:
                raise WifiError('too many connection profiles')
            for path in paths:
                try:
                    settings = self._dbus(NM_SERVICE, str(path), NM_CONNECTION_IFACE, 'GetSettings', [])
                    if str(settings.get('connection', {}).get('type', '')) != '802-11-wireless':
                        continue
                    security = settings.get('802-11-wireless-security', {})
                    if str(security.get('key-mgmt', '')) != 'wpa-psk' or int(security.get('psk-flags', 0)) != 0:
                        continue
                    secrets = {}
                    # NetworkManager 1.2 requires a setting name, not an empty all-settings request.
                    secret_groups = ['802-11-wireless-security']
                    if '802-1x' in settings:
                        secret_groups.append('802-1x')
                    for group in secret_groups:
                        secrets.update(self._dbus(NM_SERVICE, str(path), NM_CONNECTION_IFACE,
                                                  'GetSecrets', [str(group)]))
                    psk = secrets.get('802-11-wireless-security', {}).get('psk')
                    if not psk:
                        continue
                    self._psk = str(psk)
                    ssid = bytes(settings['802-11-wireless']['ssid'])
                    self._key = derived_psk(ssid, self._psk)
                    if self._key == self._psk.lower() and len(self._psk) == 64:
                        continue
                    # Preserve every profile option and unrelated secret; update only the PSK.
                    for group, values in secrets.items():
                        if group not in settings:
                            settings[group] = self.dbus.Dictionary({}, signature='sv')
                        settings[group].update(values)
                    settings['802-11-wireless-security']['psk'] = self.dbus.String(self._key, variant_level=1)
                    self._dbus(NM_SERVICE, str(path), NM_CONNECTION_IFACE, 'Update', [settings])
                    saved = self._dbus(NM_SERVICE, str(path), NM_CONNECTION_IFACE, 'GetSecrets', ['802-11-wireless-security'])
                    if str(saved.get('802-11-wireless-security', {}).get('psk', '')) != self._key:
                        raise WifiError('Wi-Fi key migration was not persisted')
                    migrated += 1
                except Exception:
                    raise WifiError('Saved Wi-Fi key migration failed; existing connections were not restarted')
                finally:
                    self._psk = None
                    self._key = None
        return self._body(True, None, extra={'migrated_profiles': migrated})

    def cmd_status(self):
        return self._ok()

    def cmd_scan(self):
        with self._locked():
            self._reject_busy()
            wifi = self._require_wifi()
            self._require_radio_on()
            if self._scan_retry_after() or wifi['state'] not in SCAN_READY_STATES:
                body = self._ok()
                body['scan_deferred'] = True
                return body
            deferred = False
            seen_since = int(self.boottime())
            try:
                self._dbus(NM_SERVICE, wifi['device'], NM_WIFI_IFACE, 'RequestScan',
                           [self.dbus.Dictionary({}, signature='sv')])
            except WifiError as exc:
                message = str(exc)
                if ('org.freedesktop.NetworkManager.Device.NotAllowed' not in message or
                        not any(reason in message for reason in (
                            'immediately following previous scan', 'while already scanning',
                            'while unavailable', 'at this time'))):
                    raise
                deferred = True
            if deferred:
                seen_since = self._scan_seen_since()
                networks = self._access_points(wifi['device'], seen_since)
            else:
                # RequestScan is asynchronous. Old AP objects are not evidence
                # that this scan finished; only accept fresh observations.
                networks = []
                for _attempt in range(SCAN_ATTEMPTS):
                    if self.monotonic() >= self.call_deadline - 2:
                        break
                    self.sleep(SCAN_INTERVAL)
                    networks = self._access_points(wifi['device'], seen_since)
                    if networks:
                        break
                # Publish an empty snapshot too. Status keeps this cutoff and
                # can pick up late observations without reviving old APs.
            self._write_scan(networks, seen_since)
            body = self._ok()
            body['networks'] = networks
            body['scan_deferred'] = deferred
            return body

    def cmd_connect(self, raw):
        saved_uuid = _parse_uuid_body(raw, True)['uuid'] if 'uuid' in _parse_object(raw) else None
        request = {} if saved_uuid else _parse_connect(raw)
        self._psk = request.get('psk')
        if self._psk is not None:
            self._key = derived_psk(request['ssid'], self._psk)
            request['psk'] = self._key
        ident = None
        with self._locked():
            self._reject_busy()
            wifi = self._require_wifi()
            self._require_radio_on()
            candidate = None
            if saved_uuid:
                path, settings = self._saved_connection(saved_uuid)
                ssid = self._ssid_of(settings)
                request = {
                    'ssid': _ssid_bytes(ssid), 'ssid_text': ssid,
                    'security': self._security_of(settings),
                    'hidden': bool(settings.get('802-11-wireless', {}).get('hidden', {}).get('v')),
                }
                candidate = {'path': path, 'uuid': saved_uuid, 'saved': True}
            self._reject_unsupported_visible(wifi['device'], request)
            original = self._snapshot_wifi(wifi)
            ethernet = self._snapshot_ethernet()
            boot_id = self._require_boot()
            self._arm_rollback()
            apply_path = self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'LoadUnit',
                                    [APPLY_UNIT], 'systemd')
            state = str(self._dbus(SD_SERVICE, str(apply_path), DBUS_PROPS, 'Get',
                                   ['org.freedesktop.systemd1.Unit', 'ActiveState'], 'systemd'))
            if state not in ('inactive', 'failed'):
                raise WifiError('the previous Wi-Fi change is still running')
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
                'interface': wifi['interface'],
                'ssid': request['ssid_text'],
                'security': request['security'],
                'hidden': request['hidden'],
                'original': original,
                'ethernet': ethernet,
                'candidate': candidate or {'path': None, 'uuid': self._new_uuid()},
                'last_result': None,
            }
            self._save(txn)
            if not saved_uuid and request['security'] == 'wpa-psk':
                self._fs_named_write('secret', request['psk'])
            else:
                self._fs_named_unlink('secret')
        try:
            self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'StartUnit',
                       [APPLY_UNIT, 'replace'], 'systemd')
        except WifiError:
            with self._locked():
                current = self._load()
                if current and current.get('id') == ident and current.get('state') == 'pending':
                    self._fs_named_unlink('secret')
                    current['state'] = 'failed'
                    current['last_result'] = self._result('connect', False, 'failed to start Wi-Fi apply service')
                    self._save(current)
                    txn = current
                else:
                    txn = current
            return self._fail('failed to start Wi-Fi apply service', txn)
        return self._body(True, None, pending=self._public_pending(txn))

    def cmd_activate(self):
        with self._locked():
            txn = self._load()
            if not txn or txn['state'] != 'pending':
                return self._body(True, None, pending=self._public_pending(txn))
            if self._expired(txn):
                self._rollback(txn, 'activate', cause='transaction expired')
                return self._fail('transaction expired', txn)
            self.sleep(self.grace)
            if self._expired(txn):
                self._rollback(txn, 'activate', cause='transaction expired')
                return self._fail('transaction expired', txn)
            self._require_radio_on()
            txn['state'] = 'applying'
            self._save(txn)
            psk = None
            try:
                self._arm_rollback()
                saved = txn['candidate'].get('saved') is True
                if not saved and txn['security'] == 'wpa-psk':
                    psk = self._read_secret()
                    self._psk = psk
                    self._fs_named_unlink('secret')
                    self._key = derived_psk(_ssid_bytes(txn['ssid']), psk)
                    psk = self._key
                self._bring_up(txn, psk)
                if not saved:
                    self._persist(txn, psk)
                self._assert_ethernet(txn.get('ethernet') or [])
                txn['state'] = 'connected'
                txn['last_result'] = self._result('connect', True, None)
                self._save(txn)
                # Keep the rollback profile until the new connection is committed.
                try:
                    if not saved:
                        self._retire_same_ssid(txn)
                except WifiError:
                    pass
            except WifiError as exc:
                message = 'Wi-Fi connection failed: {0}'.format(self._scrub(str(exc)))
                restored = self._rollback(txn, 'activate', cause=message)
                if restored:
                    return self._fail(message, txn)
                return self._fail('Wi-Fi connection failed and the previous network was not restored', txn)
            finally:
                self._psk = None
                self._key = None
                self._fs_named_unlink('secret')
            return self._ok(txn)

    def cmd_disconnect(self, raw):
        body = _parse_uuid_body(raw, False) if raw else {'uuid': None}
        with self._locked():
            self._reject_busy()
            wifi = self._require_wifi()
            self._require_radio_on()
            ethernet = self._snapshot_ethernet()
            if body['uuid']:
                active = wifi.get('active') or {}
                if active.get('uuid') != body['uuid']:
                    raise WifiError('that network is not the active Wi-Fi connection')
            if wifi.get('active'):
                self._dbus(NM_SERVICE, wifi['device'], NM_DEVICE_IFACE, 'Disconnect', [])
                for _attempt in range(POLL_ATTEMPTS):
                    observed = self._observe_wifi()
                    if not observed.get('active'):
                        break
                    self.sleep(POLL_INTERVAL)
                else:
                    raise WifiError('Wi-Fi did not disconnect')
            self._assert_ethernet(ethernet)
            return self._ok()

    def cmd_forget(self, raw):
        body = _parse_uuid_body(raw, True)
        with self._locked():
            self._reject_busy()
            ethernet = self._snapshot_ethernet()
            path, settings = self._wifi_connection(body['uuid'])
            active = None
            try:
                wifi = self._wifi_device()
            except WifiError:
                wifi = None
            if wifi and wifi.get('active') and wifi['active'].get('uuid') == body['uuid']:
                active = wifi
            if active:
                self._require_radio_on()
                self._dbus(NM_SERVICE, active['device'], NM_DEVICE_IFACE, 'Disconnect', [])
            self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'Delete', [])
            self._assert_ethernet(ethernet)
            return self._ok()

    def cmd_radio(self, raw):
        enabled = _parse_radio(raw)
        with self._locked():
            txn = self._load()
            if txn and txn['state'] not in TERMINAL_STATES:
                if not enabled:
                    self._rollback(txn, 'radio')
                else:
                    raise WifiError('a Wi-Fi change is already in progress')
            ethernet = []
            try:
                ethernet = self._snapshot_ethernet()
            except WifiError:
                ethernet = []
            self._write_radio(enabled, 'user')
            try:
                parsed, text = read_nm_state_wireless(self.nm_state_path)
                write_nm_state_wireless(self.nm_state_path, enabled, text)
            except WifiError:
                pass
            self._set_wireless_enabled(enabled)
            if enabled:
                # Enabling the radio starts NetworkManager's own discovery scan.
                self._write_scan([], int(self.boottime()))
            if ethernet:
                self._assert_ethernet(ethernet)
            return self._ok()

    def cmd_tick(self):
        txn = self._load()
        if not txn or txn['state'] in TERMINAL_STATES:
            self._fs_named_unlink('secret')
            return self._body(True, None)
        try:
            lock = self.fs.try_lock()
        except net.NetError as exc:
            if 'busy' in str(exc):
                return self._body(True, None, pending=self._public_pending(txn))
            raise _as_net(exc)
        try:
            txn = self._load()
            if not txn or txn['state'] in TERMINAL_STATES:
                self._fs_named_unlink('secret')
                return self._body(True, None)
            if txn['state'] == 'rollback-failed' or txn['state'] == 'applying' or self._expired(txn):
                cause = None
                last = txn.get('last_result') or {}
                if txn['state'] == 'rollback-failed':
                    cause = last.get('error') or 'rollback failed'
                elif self._expired(txn):
                    cause = 'transaction expired'
                else:
                    cause = 'Wi-Fi connection failed'
                restored = self._rollback(txn, 'tick', cause=cause)
                if restored:
                    return self._ok(txn)
                return self._fail((txn.get('last_result') or {}).get('error') or 'rollback failed', txn)
            return self._body(True, None, pending=self._public_pending(txn), last=txn.get('last_result'))
        finally:
            lock.release()

    def _bring_up(self, txn, psk):
        wifi = self._require_wifi()
        if txn['candidate'].get('saved') is True:
            path, settings = self._saved_connection(txn['candidate']['uuid'])
            if self._ssid_of(settings) != txn['ssid'] or self._security_of(settings) != txn['security']:
                raise WifiError('saved Wi-Fi network changed; reload Wi-Fi settings')
            self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'ActivateConnection',
                       [self.dbus.ObjectPath(path), self.dbus.ObjectPath(wifi['device']),
                        self.dbus.ObjectPath(NULL_PATH)])
            self._wait_ready(txn)
            return
        spec = {
            'id': PROFILE_PREFIX + txn['id'][:8],
            'uuid': txn['candidate']['uuid'],
            'interface': txn['interface'],
            'ssid': _ssid_bytes(txn['ssid']),
            'hidden': bool(txn.get('hidden')),
            'security': txn['security'],
        }
        encoded = encode_wifi(spec, self.dbus, psk)
        path = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE,
                          'AddConnectionUnsaved', [encoded])
        txn['candidate']['path'] = str(path)
        self._save(txn)
        self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'ActivateConnection',
                   [self.dbus.ObjectPath(str(path)), self.dbus.ObjectPath(wifi['device']),
                    self.dbus.ObjectPath(NULL_PATH)])
        self._wait_ready(txn)

    def _persist(self, txn, psk):
        uuid = txn['candidate']['uuid']
        path = self._lookup_uuid(uuid)
        if path is None:
            raise WifiError('Wi-Fi profile was not created')
        spec = {
            'id': PROFILE_PREFIX + txn['id'][:8],
            'uuid': uuid,
            'interface': txn['interface'],
            'ssid': _ssid_bytes(txn['ssid']),
            'hidden': bool(txn.get('hidden')),
            'security': txn['security'],
        }
        encoded = encode_wifi(spec, self.dbus, psk)
        self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'Update', [encoded])
        if not self.fs.profile_saved(uuid):
            raise WifiError('Wi-Fi profile was not saved')

    def _wait_ready(self, txn):
        failed = False
        deadline = self.monotonic() + CONNECT_SECONDS
        if self.call_deadline is not None:
            deadline = min(deadline, self.call_deadline)
        for _attempt in range(int(CONNECT_SECONDS / POLL_INTERVAL) + 1):
            if self.monotonic() >= deadline:
                break
            try:
                observed = self._observe_wifi()
                if observed.get('device_state') == 120:
                    failed = True
                    break
                if self._activation_ready(txn, observed):
                    return
            except WifiError:
                pass
            self.sleep(min(POLL_INTERVAL, max(0, deadline - self.monotonic())))
        if failed:
            raise WifiError('association failed')
        raise WifiError('timed out waiting for Wi-Fi')

    def _activation_ready(self, txn, observed=None):
        observed = observed or self._observe_wifi()
        active = observed.get('active')
        if not active or observed.get('device_state') != DEVICE_STATE_ACTIVATED:
            return False
        if active.get('state') != AC_STATE_ACTIVATED:
            return False
        if active.get('uuid') != txn['candidate']['uuid']:
            return False
        if observed.get('ssid') != txn['ssid']:
            return False
        return True

    def _rollback(self, txn, action, cause=None):
        self._fs_named_unlink('secret')
        if txn['state'] in TERMINAL_STATES:
            return txn['state'] == 'rolled_back'
        try:
            self._restore(txn)
        except WifiError as exc:
            txn['state'] = 'rollback-failed'
            txn['last_result'] = self._result(action, False, self._scrub(str(exc)))
            self._save(txn)
            return False
        except Exception:
            txn['state'] = 'rollback-failed'
            txn['last_result'] = self._result(action, False, 'rollback failed')
            self._save(txn)
            return False
        txn['state'] = 'rolled_back'
        if cause:
            txn['last_result'] = self._result(action, False, cause)
        elif txn.get('last_result') and not txn['last_result'].get('ok'):
            pass
        else:
            txn['last_result'] = self._result(action, True, None)
        self._save(txn)
        return True

    def _active_uuid(self, observed):
        active = observed.get('active') if observed else None
        if not active:
            return None
        return active.get('uuid')

    def _connection_activated(self, uuid, observed=None):
        if not uuid:
            return False
        observed = observed or self._observe_wifi()
        active = observed.get('active')
        if not active or active.get('uuid') != uuid:
            return False
        if observed.get('device_state') != DEVICE_STATE_ACTIVATED:
            return False
        if active.get('state') != AC_STATE_ACTIVATED:
            return False
        return True

    def _wait_activated(self, uuid):
        for _attempt in range(POLL_ATTEMPTS):
            if self.call_deadline is not None and self.monotonic() >= self.call_deadline:
                break
            try:
                if self._connection_activated(uuid):
                    return True
            except WifiError:
                pass
            self.sleep(POLL_INTERVAL)
        try:
            return self._connection_activated(uuid)
        except WifiError:
            return False

    def _restore(self, txn):
        wifi = self._wifi_device()
        if wifi is None:
            self._delete_candidate(txn)
            return
        original = txn.get('original') or {}
        candidate = txn.get('candidate') or {}
        original_uuid = original.get('uuid') if original.get('active') else None
        candidate_uuid = candidate.get('uuid')
        observed = self._observe_wifi()
        current = self._active_uuid(observed)
        if current and current != candidate_uuid and current != original_uuid:
            self._delete_candidate(txn)
            self._assert_ethernet(txn.get('ethernet') or [])
            return
        if current == candidate_uuid:
            self._dbus(NM_SERVICE, wifi['device'], NM_DEVICE_IFACE, 'Disconnect', [])
            for _attempt in range(POLL_ATTEMPTS):
                observed = self._observe_wifi()
                current = self._active_uuid(observed)
                if current != candidate_uuid:
                    break
                self.sleep(POLL_INTERVAL)
            else:
                raise WifiError('candidate was not disconnected')
        self._delete_candidate(txn)
        observed = self._observe_wifi()
        current = self._active_uuid(observed)
        if current and current != original_uuid and current != candidate_uuid:
            self._assert_ethernet(txn.get('ethernet') or [])
            return
        if original_uuid:
            path = self._lookup_uuid(original_uuid)
            if path is None:
                raise WifiError('previous Wi-Fi network is no longer available')
            wifi = self._require_wifi()
            if not self._connection_activated(original_uuid):
                self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'ActivateConnection',
                           [self.dbus.ObjectPath(path), self.dbus.ObjectPath(wifi['device']),
                            self.dbus.ObjectPath(NULL_PATH)])
                if not self._wait_activated(original_uuid):
                    raise WifiError('previous Wi-Fi network did not return')
        self._assert_ethernet(txn.get('ethernet') or [])

    def _delete_candidate(self, txn):
        # Reconnecting borrows a saved profile; rollback must never remove it.
        if (txn.get('candidate') or {}).get('saved') is True:
            return
        uuid = (txn.get('candidate') or {}).get('uuid')
        original = txn.get('original') or {}
        if not uuid:
            return
        if original.get('uuid') == uuid:
            raise WifiError('refusing to delete the previous Wi-Fi network')
        path = self._lookup_uuid(uuid)
        if path is None:
            return
        settings = self._connection_settings(path)
        if settings.get('connection', {}).get('type', {}).get('v') != '802-11-wireless':
            raise WifiError('refusing to delete a non-Wi-Fi profile')
        if settings.get('connection', {}).get('uuid', {}).get('v') != uuid:
            raise WifiError('refusing to delete a non-Wi-Fi profile')
        self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'Delete', [])

    def _retire_same_ssid(self, txn):
        uuid = txn['candidate']['uuid']
        for path, settings in self._wifi_connections():
            ident = settings.get('connection', {}).get('uuid', {}).get('v')
            name = settings.get('connection', {}).get('id', {}).get('v') or ''
            if ident == uuid:
                continue
            if not isinstance(name, str) or not name.startswith(PROFILE_PREFIX):
                continue
            if self._ssid_of(settings) != txn['ssid']:
                continue
            self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'Delete', [])

    def _reject_unsupported_visible(self, device, request):
        for item in self._access_points(device):
            if item['ssid'] != request['ssid_text']:
                continue
            if not item['supported']:
                raise WifiError('unsupported security')
            if item['security'] != request['security']:
                raise WifiError('unsupported security')
            return

    def _access_points(self, device, seen_since=None):
        paths = self._dbus(NM_SERVICE, device, NM_WIFI_IFACE, 'GetAccessPoints', [])
        if len(paths) > MAX_APS:
            paths = list(paths)[:MAX_APS]
        found = {}
        for path in paths:
            path = str(path)
            try:
                if seen_since is not None:
                    last_seen = int(self._prop(path, NM_AP_IFACE, 'LastSeen'))
                    if last_seen < seen_since:
                        continue
                ssid = _ssid_text(self._prop(path, NM_AP_IFACE, 'Ssid'))
                if not ssid:
                    continue
                flags = self._prop(path, NM_AP_IFACE, 'Flags')
                wpa = self._prop(path, NM_AP_IFACE, 'WpaFlags')
                rsn = self._prop(path, NM_AP_IFACE, 'RsnFlags')
                strength = int(self._prop(path, NM_AP_IFACE, 'Strength'))
            except (WifiError, TypeError, ValueError):
                continue
            security = classify_ap(flags, wpa, rsn)
            row = {
                'ssid': ssid,
                'security': security,
                'supported': security in SECURITIES,
                'strength': max(0, min(100, strength)),
            }
            previous = found.get(ssid)
            if previous is None or row['strength'] > previous['strength']:
                found[ssid] = row
        return sorted(found.values(), key=lambda item: (-item['strength'], item['ssid']))

    def _require_radio_on(self):
        radio = self._radio_view(read_nm=True)
        if not radio.get('enabled'):
            raise WifiError('Wi-Fi radio is off')
        if radio.get('blocked'):
            raise WifiError('Wi-Fi radio is blocked by hardware')

    def _radio_view(self, read_nm=True):
        persisted = self._read_radio()
        source = None if not persisted else persisted.get('source')
        if read_nm and self.bus is not None:
            software = bool(self._prop(NM_PATH, NM_IFACE, 'WirelessEnabled'))
            hardware = bool(self._prop(NM_PATH, NM_IFACE, 'WirelessHardwareEnabled'))
        else:
            software = bool(persisted['enabled']) if persisted else False
            hardware = True
        return {
            'enabled': bool(software),
            'hardware': bool(hardware),
            'blocked': not bool(hardware),
            'persisted': persisted is not None,
            'source': source,
        }

    def _set_wireless_enabled(self, enabled):
        if self.bus is None or self.dbus is None:
            return
        value = self.dbus.Boolean(bool(enabled), variant_level=1)
        self._dbus(NM_SERVICE, NM_PATH, DBUS_PROPS, 'Set',
                   [NM_IFACE, 'WirelessEnabled', value])

    def _require_wifi(self):
        wifi = self._wifi_device()
        if wifi is None:
            raise WifiError('no supported Wi-Fi adapter')
        if wifi.get('firmware_missing'):
            raise WifiError('Wi-Fi adapter firmware is missing')
        if not wifi.get('managed'):
            raise WifiError('Wi-Fi adapter is not managed')
        return wifi

    def _wifi_device(self):
        if self.bus is None:
            return None
        paths = self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'GetDevices', [])
        found = []
        for path in paths:
            path = str(path)
            try:
                kind = int(self._prop(path, NM_DEVICE_IFACE, 'DeviceType'))
            except (WifiError, TypeError, ValueError):
                continue
            if kind != DEVICE_TYPE_WIFI:
                continue
            name = _text(self._prop(path, NM_DEVICE_IFACE, 'Interface'))
            if not IFACE_RE.match(name):
                continue
            managed = bool(self._prop(path, NM_DEVICE_IFACE, 'Managed'))
            try:
                missing = bool(self._prop(path, NM_DEVICE_IFACE, 'FirmwareMissing'))
            except WifiError:
                missing = False
            try:
                driver = _text(self._prop(path, NM_DEVICE_IFACE, 'Driver'))
            except WifiError:
                driver = ''
            state = int(self._prop(path, NM_DEVICE_IFACE, 'State'))
            item = {
                'present': True,
                'interface': name,
                'device': path,
                'managed': managed,
                'firmware_missing': missing,
                'driver': driver,
                'state': state,
                'active': None,
                'ssid': None,
            }
            active_path = str(self._prop(path, NM_DEVICE_IFACE, 'ActiveConnection'))
            if active_path and active_path != NULL_PATH:
                item['active'] = {
                    'path': active_path,
                    'state': int(self._prop(active_path, NM_ACTIVE_IFACE, 'State')),
                    'uuid': _text(self._prop(active_path, NM_ACTIVE_IFACE, 'Uuid')),
                    'connection': str(self._prop(active_path, NM_ACTIVE_IFACE, 'Connection')),
                }
                try:
                    ap = str(self._prop(path, NM_WIFI_IFACE, 'ActiveAccessPoint'))
                    if ap and ap != NULL_PATH:
                        item['ssid'] = _ssid_text(self._prop(ap, NM_AP_IFACE, 'Ssid'))
                except WifiError:
                    pass
            found.append(item)
        if not found:
            return None
        if len(found) > 1:
            raise WifiError('multiple Wi-Fi adapters')
        return found[0]

    def _observe_wifi(self):
        wifi = self._wifi_device()
        if wifi is None:
            raise WifiError('no supported Wi-Fi adapter')
        return {
            'device': wifi['device'],
            'interface': wifi['interface'],
            'device_state': wifi['state'],
            'active': wifi.get('active'),
            'ssid': wifi.get('ssid'),
        }

    def _snapshot_wifi(self, wifi):
        active = wifi.get('active')
        if not active:
            return None
        settings = None
        try:
            settings = self._connection_settings(active['connection'])
        except WifiError:
            settings = None
        ssid = wifi.get('ssid') or (self._ssid_of(settings) if settings else None)
        return {
            'uuid': active['uuid'],
            'path': active['connection'],
            'active': True,
            'ssid': ssid,
        }

    def _snapshot_ethernet(self):
        if self.bus is None:
            return []
        found = []
        for path in self._dbus(NM_SERVICE, NM_PATH, NM_IFACE, 'GetDevices', []):
            path = str(path)
            try:
                kind = int(self._prop(path, NM_DEVICE_IFACE, 'DeviceType'))
            except (WifiError, TypeError, ValueError):
                continue
            if kind != DEVICE_TYPE_ETHERNET:
                continue
            name = _text(self._prop(path, NM_DEVICE_IFACE, 'Interface'))
            state = int(self._prop(path, NM_DEVICE_IFACE, 'State'))
            active_path = str(self._prop(path, NM_DEVICE_IFACE, 'ActiveConnection'))
            uuid = None
            if active_path and active_path != NULL_PATH:
                uuid = _text(self._prop(active_path, NM_ACTIVE_IFACE, 'Uuid'))
            found.append({
                'interface': name,
                'device': path,
                'uuid': uuid,
                'active': bool(uuid),
                'state': state,
            })
        return found

    def _ethernet_connected(self):
        for item in self._snapshot_ethernet():
            if item.get('active') and item.get('state') == DEVICE_STATE_ACTIVATED:
                return True
        return False

    def _assert_ethernet(self, snapshot):
        if not snapshot:
            return
        current = {}
        for item in self._snapshot_ethernet():
            current[item['interface']] = item
        for item in snapshot:
            if not item.get('active'):
                continue
            now = current.get(item['interface'])
            if not now or now.get('uuid') != item.get('uuid') or not now.get('active'):
                raise WifiError('Ethernet connection was interrupted')

    def _saved_networks(self):
        found = []
        for path, settings in self._wifi_connections():
            uuid = settings.get('connection', {}).get('uuid', {}).get('v')
            if not isinstance(uuid, str) or not UUID_RE.match(uuid):
                continue
            ssid = self._ssid_of(settings)
            security = self._security_of(settings)
            hidden = bool(settings.get('802-11-wireless', {}).get('hidden', {}).get('v'))
            found.append({
                'uuid': uuid,
                'ssid': ssid,
                'security': security,
                'supported': security in SECURITIES,
                'hidden': hidden,
                'saved': self.fs.profile_saved(uuid),
            })
            if len(found) >= MAX_SAVED:
                break
        return found

    def _wifi_connections(self):
        if self.bus is None:
            return []
        paths = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE, 'ListConnections', [])
        if len(paths) > MAX_CONNECTIONS:
            raise WifiError('too many connection profiles')
        found = []
        for path in paths:
            path = str(path)
            try:
                settings = self._connection_settings(path)
            except WifiError:
                continue
            if settings.get('connection', {}).get('type', {}).get('v') != '802-11-wireless':
                continue
            found.append((path, settings))
        return found

    def _wifi_connection(self, uuid):
        for path, settings in self._wifi_connections():
            if settings.get('connection', {}).get('uuid', {}).get('v') == uuid:
                return path, settings
        raise WifiError('unknown Wi-Fi network')

    def _saved_connection(self, uuid):
        path, settings = self._wifi_connection(uuid)
        if not self.fs.profile_saved(uuid):
            raise WifiError('Wi-Fi network is not saved')
        if self._security_of(settings) not in SECURITIES:
            raise WifiError('unsupported security')
        security = settings.get('802-11-wireless-security')
        if security and security.get('key-mgmt', {}).get('v') != 'wpa-psk':
            raise WifiError('unsupported security')
        wireless = settings.get('802-11-wireless', {})
        if wireless.get('mode', {}).get('v', 'infrastructure') != 'infrastructure':
            raise WifiError('unsupported Wi-Fi mode')
        return path, settings

    def _ssid_of(self, settings):
        raw = settings.get('802-11-wireless', {}).get('ssid', {})
        if raw.get('sig') == 'ay':
            return _ssid_text(raw.get('v'))
        if raw.get('sig') == 's':
            return raw.get('v') if isinstance(raw.get('v'), str) else None
        return None

    def _security_of(self, settings):
        sec = settings.get('802-11-wireless-security')
        if not sec:
            return 'open'
        key = sec.get('key-mgmt', {}).get('v')
        if key == 'wpa-psk':
            return 'wpa-psk'
        if key == 'none':
            return 'open'
        return 'unsupported'

    def _connection_settings(self, path):
        try:
            return net.decode_settings(
                self._dbus(NM_SERVICE, path, NM_CONNECTION_IFACE, 'GetSettings', []),
                self.dbus)
        except net.NetError as exc:
            raise _as_net(exc)

    def _lookup_uuid(self, uuid):
        paths = self._dbus(NM_SERVICE, NM_SETTINGS_PATH, NM_SETTINGS_IFACE, 'ListConnections', [])
        if len(paths) > MAX_CONNECTIONS:
            raise WifiError('too many connection profiles')
        for path in paths:
            try:
                settings = self._connection_settings(str(path))
            except WifiError:
                continue
            if settings.get('connection', {}).get('uuid', {}).get('v') == uuid:
                return str(path)
        return None

    def _current_connection(self, wifi):
        if not wifi or not wifi.get('active'):
            return None
        uuid = wifi['active']['uuid']
        security = None
        hidden = False
        saved = False
        try:
            settings = self._connection_settings(wifi['active']['connection'])
            security = self._security_of(settings)
            hidden = bool(settings.get('802-11-wireless', {}).get('hidden', {}).get('v'))
            saved = self.fs.profile_saved(uuid)
        except WifiError:
            security = None
        address = None
        try:
            ip4 = str(self._prop(wifi['device'], NM_DEVICE_IFACE, 'Ip4Config'))
            if ip4 and ip4 != NULL_PATH:
                data = self._prop(ip4, NM_IP4_IFACE, 'AddressData')
                if data:
                    first = list(data)[0]
                    address = _text(first['address']) if 'address' in first else None
        except (WifiError, TypeError, KeyError):
            address = None
        return {
            'active': True,
            'ssid': wifi.get('ssid'),
            'uuid': uuid,
            'security': security,
            'supported': security in SECURITIES if security else False,
            'hidden': hidden,
            'saved': saved,
            'address': address,
        }

    def _adapter_view(self):
        wifi = self._wifi_device()
        if not wifi:
            return {'present': False, 'interface': None, 'managed': False,
                    'firmware_missing': False, 'driver': '', 'state': None}
        return {
            'present': True,
            'interface': wifi['interface'],
            'managed': wifi['managed'],
            'firmware_missing': wifi['firmware_missing'],
            'driver': wifi.get('driver') or '',
            'state': wifi.get('state'),
        }

    def _scan_data(self):
        raw = self._fs_named_read('scan.json')
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
        except (ValueError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict) or data.get('v') != 1 or not isinstance(data.get('networks'), list):
            return {}
        return data

    def _scan_retry_after(self):
        data = self._scan_data()
        boot = self._boot()
        started = data.get('scanned_mono')
        if (not boot or data.get('boot_id') != boot or
                isinstance(started, bool) or not isinstance(started, (int, float)) or
                not math.isfinite(started)):
            return 0
        elapsed = self.monotonic() - started
        if elapsed < 0:
            return 0
        return max(0, int(math.ceil(SCAN_COOLDOWN_SECONDS - elapsed)))

    def _read_scan(self):
        data = self._scan_data()
        if data.get('boot_id') != self._boot():
            return []
        out = []
        for item in data.get('networks', [])[:MAX_APS]:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get('ssid'), str):
                continue
            security = item.get('security')
            out.append({
                'ssid': item['ssid'],
                'security': security if security in ('open', 'wpa-psk', 'unsupported') else 'unsupported',
                'supported': bool(item.get('supported')) and security in SECURITIES,
                'strength': int(item['strength']) if _is_int(item.get('strength')) else 0,
            })
        return out

    def _scan_seen_since(self):
        data = self._scan_data()
        value = data.get('seen_since')
        if (data.get('boot_id') == self._boot() and _is_int(value) and
                0 <= value <= self.boottime()):
            return value
        return None

    def _write_scan(self, networks, seen_since=None):
        payload = {'v': 1, 'at': int(self.clock()), 'networks': networks,
                   'boot_id': self._boot(), 'scanned_mono': self.monotonic(),
                   'seen_since': seen_since}
        self._fs_named_write('scan.json', _canonical(payload))

    def _read_radio(self):
        raw = self._fs_named_read('radio.json')
        if not raw:
            return None
        try:
            data = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or data.get('v') != 1:
            return None
        if data.get('enabled') is not True and data.get('enabled') is not False:
            return None
        source = data.get('source')
        if source not in ('default', 'user', None):
            source = None
        return {'enabled': data['enabled'], 'source': source}

    def _write_radio(self, enabled, source):
        payload = {'v': 1, 'enabled': bool(enabled), 'source': source}
        self._fs_named_write('radio.json', _canonical(payload))

    def _read_secret(self):
        raw = self._fs_named_read('secret')
        if not raw:
            raise WifiError('Wi-Fi password is unavailable')
        try:
            text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            raise WifiError('invalid password')
        return _parse_psk(text)

    def _fs_named_write(self, name, text):
        try:
            self.fs.ensure_state_dir()
            self.fs.write_named(name, text)
        except net.NetError as exc:
            raise _as_net(exc)

    def _fs_named_read(self, name):
        try:
            return self.fs.read_named(name)
        except net.NetError as exc:
            raise _as_net(exc)
        except AttributeError:
            if name == 'transaction.json':
                return self.fs.read_txn()
            return None

    def _fs_named_unlink(self, name):
        try:
            self.fs.unlink_named(name)
        except net.NetError as exc:
            raise _as_net(exc)
        except AttributeError:
            return

    def _reject_busy(self):
        txn = self._load()
        if txn and txn['state'] not in TERMINAL_STATES:
            raise WifiError('a Wi-Fi change is already in progress')

    def _arm_rollback(self):
        self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'StartUnit', [ROLLBACK_UNIT, 'replace'], 'systemd')
        path = str(self._dbus(SD_SERVICE, SD_PATH, SD_MANAGER, 'GetUnit', [ROLLBACK_UNIT], 'systemd'))
        for _attempt in range(10):
            active = self._dbus(SD_SERVICE, path, DBUS_PROPS, 'Get',
                                ['org.freedesktop.systemd1.Unit', 'ActiveState'], 'systemd')
            if str(active) == 'active':
                return
            self.sleep(0.1)
        raise WifiError('rollback timer is not active')

    def _prop(self, path, iface, name):
        return self._dbus(NM_SERVICE, path, DBUS_PROPS, 'Get', [iface, name])

    def _dbus(self, service, path, iface, method, args, what='NetworkManager'):
        try:
            remaining = 2 if self.call_deadline is None else self.call_deadline - self.monotonic()
            if remaining <= 0:
                raise WifiError('Wi-Fi operation timed out')
            if service not in self.owners:
                self.owners[service] = self.bus.get_name_owner(service)
            proxy = self.dbus.Interface(
                self.bus.get_object(self.owners[service], path, introspect=False), iface)
            return getattr(proxy, method)(*args, timeout=min(2.0, remaining))
        except WifiError:
            raise
        except Exception as exc:
            raise WifiError(self._scrub('{0} call failed: {1}'.format(what, exc)))

    def _locked(self):
        return _LockCtx(self)

    def _load(self):
        try:
            raw = self.fs.read_txn()
        except net.NetError as exc:
            raise _as_net(exc)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            try:
                raw = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise WifiError('transaction state is corrupt')
        if not isinstance(raw, str) or len(raw) > MAX_TXN_BYTES:
            raise WifiError('transaction state is corrupt')
        try:
            data = json.loads(raw)
        except ValueError:
            raise WifiError('transaction state is corrupt')
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('state') not in ALL_STATES:
            raise WifiError('transaction state is corrupt')
        if not isinstance(data.get('id'), str) or not isinstance(data.get('candidate'), dict):
            raise WifiError('transaction state is corrupt')
        return data

    def _save(self, txn):
        redacted = _redact(txn)
        # Public SSIDs and schema keys may equal a passphrase. Scrub free-form
        # errors, while _redact removes credential fields from structured data.
        if isinstance(redacted.get('last_result'), dict):
            redacted['last_result']['error'] = self._scrub(redacted['last_result'].get('error'))
        text = json.dumps(redacted, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        if len(text) > MAX_TXN_BYTES:
            raise WifiError('transaction is too large')
        try:
            self.fs.write_txn(text)
        except net.NetError as exc:
            raise _as_net(exc)

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
            raise WifiError('boot identity unavailable')
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
            'ssid': txn.get('ssid'),
            'security': txn.get('security'),
            'seconds_remaining': remaining,
        }

    def _ok(self, txn=None):
        txn = txn if txn is not None else self._load()
        return self._assemble(True, None, txn)

    def _fail(self, message, txn=None, read_nm=False):
        pending = None
        last = None if not txn else txn.get('last_result')
        if txn and txn.get('state') not in TERMINAL_STATES:
            try:
                pending = self._public_pending(txn)
            except Exception:
                pending = None
        extra = None
        if read_nm:
            return self._assemble(False, message, txn)
        return self._body(False, message, pending=pending, last=last, extra=extra)

    def _status_view(self):
        adapter = {'present': False, 'interface': None, 'managed': False,
                   'firmware_missing': False, 'driver': '', 'state': None}
        radio = self._radio_view(read_nm=self.bus is not None)
        connection = None
        saved = []
        ethernet = False
        networks = self._read_scan()
        if self.bus is not None:
            adapter = self._adapter_view()
            wifi = self._wifi_device()
            connection = self._current_connection(wifi)
            saved = self._saved_networks()
            ethernet = self._ethernet_connected()
            if (wifi and radio['enabled'] and wifi['managed'] and
                    wifi['state'] in SCAN_READY_STATES):
                seen_since = self._scan_seen_since()
                if seen_since is not None:
                    networks = self._access_points(wifi['device'], seen_since)
        return {
            'adapter': adapter,
            'radio': radio,
            'connection': connection,
            'saved': saved,
            'networks': networks,
            'scan_retry_after': self._scan_retry_after(),
            'ethernet_connected': ethernet,
        }

    def _assemble(self, ok, error, txn):
        extra = None
        try:
            extra = self._status_view()
        except WifiError as exc:
            if ok:
                ok = False
                error = self._scrub(str(exc))
            extra = None
        return self._body(ok, error, pending=self._public_pending(txn),
                          last=None if not txn else txn.get('last_result'), extra=extra)

    def _body(self, ok, error, pending=None, last=None, extra=None):
        body = {
            'ok': bool(ok),
            'error': error,
            'pending': pending,
            'last_result': last,
            'adapter': {'present': False},
            'radio': {'enabled': False, 'hardware': True, 'blocked': False, 'persisted': False, 'source': None},
            'connection': None,
            'saved': [],
            'networks': [],
            'ethernet_connected': False,
        }
        if extra:
            body.update(extra)
        return _redact(body)


class _LockCtx(object):
    def __init__(self, service):
        self.service = service
        self.lock = None

    def __enter__(self):
        try:
            self.service.fs.ensure_state_dir()
            self.lock = self.service.fs.try_lock()
        except net.NetError as exc:
            raise _as_net(exc)
        return self.lock

    def __exit__(self, exc_type, exc, tb):
        if self.lock is not None:
            self.lock.release()
        return False


def real_service(journal_only=False, nm_state_path=NM_STATE_PATH):
    fs = WifiFS(state_dir=STATE_DIR, boot_id_path=BOOT_ID_PATH, require_root=True)
    if journal_only:
        return Service(None, None, fs, time.time, time.sleep, os.urandom, os.geteuid,
                       nm_state_path=nm_state_path)
    try:
        import dbus
    except ImportError:
        raise WifiError('NetworkManager unavailable')
    return Service(dbus.SystemBus(), dbus, fs, time.time, time.sleep, os.urandom, os.geteuid,
                   nm_state_path=nm_state_path)


def _empty(error):
    return Service(None, None, None, time.time, time.sleep, os.urandom, lambda: 0)._body(False, error)


def main(argv, stdin, stdout, service=None):
    result = None
    try:
        if not isinstance(argv, (list, tuple)) or len(argv) != 2 or argv[1] not in VERBS:
            result = _empty('invalid arguments')
        elif service is None and os.geteuid() != 0:
            result = _empty('root required')
        else:
            if service is None:
                service = real_service(journal_only=argv[1] in ('pending', 'provision'))
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
    except WifiError as exc:
        result = _empty(str(exc))
    except Exception:
        result = _empty('internal error')
    result = _redact(result)
    text = json.dumps(result, sort_keys=True, ensure_ascii=True) + '\n'
    stdout.write(text)
    if argv and len(argv) == 2 and argv[1] == 'provision':
        return 0
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    def deadline(_signum, _frame):
        raise DeadlineExceeded()

    signal.signal(signal.SIGALRM, deadline)
    long_verb = len(sys.argv) == 2 and sys.argv[1] in ('activate', 'tick', 'sync-radio', 'migrate-secrets')
    signal.setitimer(signal.ITIMER_REAL, APPLY_BUDGET_SECONDS + 5 if len(sys.argv) == 2 and sys.argv[1] == 'activate' else 35 if long_verb else 12)
    try:
        result = main(sys.argv, sys.stdin, sys.stdout)
    except DeadlineExceeded:
        sys.stdout.write(json.dumps(_empty('Wi-Fi operation timed out; refresh status'),
                                    sort_keys=True, ensure_ascii=True) + '\n')
        result = 1 if not (len(sys.argv) == 2 and sys.argv[1] == 'provision') else 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.exit(result)
