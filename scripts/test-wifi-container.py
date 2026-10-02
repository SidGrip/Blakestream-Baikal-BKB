#!/usr/bin/env python3
"""Run only inside the disposable container started by test-wifi-container.sh."""
import importlib.util
import configparser
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

import dbus

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wifi', str(ROOT / 'overlay/opt/scripta/bin/wifi-settings.py'))
wifi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi)


class CheckedService(wifi.Service):
    last_call_error = None

    def _dbus(self, service, path, iface, method, args, what='NetworkManager'):
        try:
            return super(CheckedService, self)._dbus(service, path, iface, method, args, what)
        except Exception as exc:
            self.last_call_error = method + ': ' + str(exc)
            raise


def main():
    if not Path('/.dockerenv').exists():
        raise SystemExit('Run with test-wifi-container.sh, never on a miner or host bus')
    effective = configparser.ConfigParser(interpolation=None)
    effective.read_string(subprocess.check_output(['NetworkManager', '--print-config']).decode())
    assert effective.get('main', 'dns') == 'default'
    bus = dbus.SystemBus()
    props = dbus.Interface(bus.get_object(wifi.NM_SERVICE, wifi.NM_PATH), wifi.DBUS_PROPS)
    assert str(props.Get(wifi.NM_IFACE, 'Version')) == '1.2.6'
    manager = dbus.Interface(bus.get_object(wifi.NM_SERVICE, wifi.NM_SETTINGS_PATH), wifi.NM_SETTINGS_IFACE)
    password = 'container-only-passphrase'
    ssid = b' SSID with spaces '
    key = wifi.derived_psk(ssid, password)
    with tempfile.TemporaryDirectory() as root:
        fs = wifi.WifiFS(state_dir=root + '/state', require_root=True)
        service = CheckedService(bus, dbus, fs, time.time, time.sleep, os.urandom, os.geteuid,
                               nm_state_path=root + '/NetworkManager.state')
        assert service.dispatch('provision', '')['ok']
        for security in ('open', 'wpa-psk'):
            profile = wifi.encode_wifi({'id': 'qa-wifi',
                'uuid': '11111111-1111-4111-8111-111111111111', 'interface': 'wlan0',
                'ssid': ssid, 'hidden': True, 'security': security}, dbus,
                password if security == 'wpa-psk' else None)
            profile['802-11-wireless']['mtu'] = dbus.UInt32(1400, variant_level=1)
            path = manager.AddConnectionUnsaved(profile)
            connection = dbus.Interface(bus.get_object(wifi.NM_SERVICE, path), wifi.NM_CONNECTION_IFACE)
            try:
                connection.Update(profile)
                if security == 'wpa-psk':
                    assert str(connection.GetSecrets('802-11-wireless-security')['802-11-wireless-security']['psk']) == key
                    files = list(Path('/etc/NetworkManager/system-connections').iterdir())
                    assert files and any(key.encode() in p.read_bytes() for p in files)
                    for p in files:
                        assert password.encode() not in p.read_bytes()
                        assert p.stat().st_uid == 0 and stat.S_IMODE(p.stat().st_mode) == 0o600
                    # Convert an older profile without activating it or changing its options.
                    profile['802-11-wireless-security']['psk'] = dbus.String(password, variant_level=1)
                    connection.Update(profile)
                    converted = service.dispatch('migrate-secrets', '')
                    assert converted.get('migrated_profiles') == 1, (converted, service.last_call_error)
                    assert service.dispatch('migrate-secrets', '').get('migrated_profiles') == 0
                    assert str(connection.GetSecrets('802-11-wireless-security')['802-11-wireless-security']['psk']) == key
                    for p in files:
                        assert password.encode() not in p.read_bytes()
                stored = connection.GetSettings()
                saved_path, saved_settings = service._saved_connection(str(profile['connection']['uuid']))
                assert saved_path == str(path)
                assert service._ssid_of(saved_settings) == ssid.decode('utf-8')
                assert service._security_of(saved_settings) == security
                assert bytes(stored['802-11-wireless']['ssid']) == ssid
                assert stored['802-11-wireless']['mtu'] == 1400
                assert stored['connection']['uuid'] == profile['connection']['uuid']
                for family in ('ipv4', 'ipv6'):
                    assert isinstance(stored[family]['route-metric'], dbus.Int64)
                    assert stored[family]['route-metric'] == wifi.WIFI_METRIC
            finally:
                connection.Delete()
        for enabled in (False, True, False):
            result = service.dispatch('radio', json.dumps({'enabled': enabled}))
            assert result['ok'] and result['radio']['enabled'] is enabled, result
        status = service.dispatch('status', '')
        assert status['ok'] and not status['adapter']['present'], status
        assert not service.dispatch('scan', '{}')['ok']
        assert not service.dispatch('connect', json.dumps({'ssid': 'qa', 'security': 'open'}))['ok']
    print('PASS NetworkManager 1.2.6: derived PSK persistence, keyfile permissions, conversion, radio and absent-adapter guards')


if __name__ == '__main__':
    main()
