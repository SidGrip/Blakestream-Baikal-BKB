#!/usr/bin/env python3
"""Check D-Bus signatures that a permissive fake daemon would otherwise accept."""
import importlib.util
from pathlib import Path
import time
import unittest
import dbus

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('wifi_bindings', ROOT / 'overlay/opt/scripta/bin/wifi-settings.py')
WIFI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIFI)


class Bindings(unittest.TestCase):
    def test_radio_set_is_a_variant(self):
        service = WIFI.Service(object(), dbus, None, time.time, time.sleep, None, lambda: 0)
        calls = []
        service._dbus = lambda *args: calls.append(args)
        service._set_wireless_enabled(False)
        self.assertEqual(calls[0][2:4], (WIFI.DBUS_PROPS, 'Set'))
        value = calls[0][4][2]
        self.assertIsInstance(value, dbus.Boolean)
        self.assertEqual(value.variant_level, 1)
        self.assertFalse(value)

    def test_profile_metric_is_signed_64_bit(self):
        settings = WIFI.encode_wifi({'id':'qa', 'uuid':'11111111-1111-4111-8111-111111111111',
                                     'interface':'wlan0', 'ssid':b' qa ', 'hidden':False,
                                     'security':'open'}, dbus)
        for family in ('ipv4', 'ipv6'):
            metric = settings[family]['route-metric']
            self.assertIsInstance(metric, dbus.Int64)
            self.assertEqual(metric.variant_level, 1)
        ssid = settings['802-11-wireless']['ssid']
        self.assertIsInstance(ssid, dbus.ByteArray)
        self.assertEqual(ssid, b' qa ')
        self.assertEqual(ssid.variant_level, 1)


if __name__ == '__main__':
    unittest.main()
