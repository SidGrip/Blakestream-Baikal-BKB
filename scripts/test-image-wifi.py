#!/usr/bin/env python3
"""Boot configuration transformation and fail-closed packaging checks."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('image_wifi', ROOT / 'scripts/prepare-image-wifi.py')
WIFI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIFI)
FEX = '[wifi_para]\nwifi_used = 0\nwifi_sdc_id = 1\n[mmc_para]\nsdc_used = 1\n'


class BootWifi(unittest.TestCase):
    def test_dual_network_filtering_keeps_validation_on_physical_interfaces(self):
        path = ROOT / 'overlay/etc/sysctl.d/90-blakestream-dual-network.conf'
        entries = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                key, value = line.split('=', 1)
                self.assertNotIn(key.strip(), entries)
                entries[key.strip()] = value.strip()
        self.assertEqual(entries, {
            'net.ipv4.conf.eth0.rp_filter': '2',
            'net.ipv4.conf.wlan0.rp_filter': '2',
        })
        installer = (ROOT / 'scripts/04-apply-overlay.sh').read_text()
        self.assertIn('chmod 0644 "${ROOTFS}/etc/sysctl.d/90-blakestream-dual-network.conf"', installer)

    def test_obsolete_forced_realtek_load_is_removed_without_other_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            path = root / 'etc/modules'
            keep = '# 8189es is not loaded here\ngpio_sunxi\nxradio_wlan\n'
            path.write_text(keep + '8189es\n  8189es # factory Wi-Fi\n8189es rtw_ips_mode=0\n')
            path.chmod(0o640)
            self.assertTrue(WIFI.prepare_modules(root))
            self.assertEqual(path.read_text(), keep)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertFalse(WIFI.prepare_modules(root))
            self.assertEqual(path.read_text(), keep)

    def test_module_cleanup_guards_and_missing_file(self):
        with self.assertRaises(ValueError): WIFI.prepare_modules('/')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError): WIFI.prepare_modules(root)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            self.assertFalse(WIFI.prepare_modules(root))
            other = root / 'outside'
            other.write_text('8189es\n')
            (root / 'etc/modules').symlink_to(other)
            with self.assertRaises(ValueError): WIFI.prepare_modules(root)
            self.assertEqual(other.read_text(), '8189es\n')
            (root / 'etc/modules').unlink()
            (root / 'etc').rmdir()
            other.unlink()
            other.mkdir()
            (other / 'modules').write_text('8189es\n')
            (root / 'etc').symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError): WIFI.prepare_modules(root)
            self.assertEqual((other / 'modules').read_text(), '8189es\n')

    def test_module_cleanup_is_part_of_image_preparation(self):
        pruner = (ROOT / 'scripts/prune-rootfs.sh').read_text()
        self.assertIn('prepare-image-wifi.py" --rootfs "$ROOTFS"', pruner)
        loader = (ROOT / 'overlay/etc/modules-load.d/blakestream-wifi.conf').read_text()
        self.assertIn('xradio_wlan', loader)
        self.assertNotIn('8189es', loader)

    def test_conversion_and_roundtrip_guards(self):
        for scenario in ('valid', 'unchanged', 'wrong-value', 'corrupt-roundtrip'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'script.bin'
                path.write_bytes(b'original')
                path.chmod(0o640)
                converted = []
                def run(args, check):
                    source, destination = Path(args[-2]), Path(args[-1])
                    if source == path:
                        text = FEX.replace('wifi_used = 0', 'wifi_used = 1') if scenario == 'unchanged' else FEX
                        if scenario == 'wrong-value': text = FEX.replace('wifi_used = 0', 'wifi_used = 7')
                        destination.write_text(text)
                    elif args[2] == 'fex':
                        converted.append(source.read_text())
                        destination.write_bytes(b'compiled')
                    else:
                        text = converted[-1]
                        if scenario == 'corrupt-roundtrip': text = text.replace('sdc_used = 1', 'sdc_used = 0')
                        destination.write_text(text)
                with patch.object(WIFI.shutil, 'which', return_value='/usr/bin/sunxi-fexc'), patch.object(WIFI.subprocess, 'run', side_effect=run):
                    if scenario in ('wrong-value','corrupt-roundtrip'):
                        with self.assertRaises(ValueError): WIFI.prepare(path)
                    else:
                        self.assertEqual(WIFI.prepare(path), scenario == 'valid')
                self.assertEqual(path.read_bytes(), b'compiled' if scenario == 'valid' else b'original')
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_missing_tool_and_symlink_leave_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'script.bin'
            path.write_bytes(b'original')
            with patch.object(WIFI.shutil, 'which', return_value=None):
                with self.assertRaises(RuntimeError): WIFI.prepare(path)
            link = Path(tmp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError): WIFI.prepare(link)
            self.assertEqual(path.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
