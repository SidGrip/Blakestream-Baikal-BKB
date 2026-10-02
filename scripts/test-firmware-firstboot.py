#!/usr/bin/env python3
"""First-install reconciliation tests without accessing miner hardware."""
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('firstboot', ROOT / 'overlay/opt/scripta/bin/firmware-firstboot.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class FirstBootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('etc', 'run', 'firmware', 'var'):
            (self.root / name).mkdir()
        values = {'ETC': str(self.root / 'etc'), 'RUN': str(self.root / 'run'),
                  'FW': str(self.root / 'firmware'), 'PENDING': str(self.root / 'etc/pending'),
                  'RESULT': str(self.root / 'etc/result.json'), 'LOG': str(self.root / 'var/firstboot.log'),
                  'ACTIVE': str(self.root / 'run/active'),
                  'LAUNCH_LOCK': str(self.root / 'launcher.lock')}
        for name, value in values.items():
            patcher = mock.patch.object(boot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('chown', 'fchown'):
            patcher = mock.patch.object(boot.os, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(boot, 'bridge_running', return_value=True)
        self.bridge_running = patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = self.root / 'etc/scripta.conf'
        self.options = self.root / 'etc/miner.options.json'
        self.settings.write_text(json.dumps({'userAdjustableClocks': False,
            'userAutoFan': False, 'userDev': False, 'userAutoFanMin': 40, 'userTz': 'UTC'}))
        self.options.write_text(json.dumps([
            {'key': 'api-port', 'value': '4028'},
            {'key': 'baikal-options', 'value': '600:40:55'},
            {'key': 'baikal-experimental-clock-index', 'value': True},
            {'key': 'baikal-board-clock-index', 'value': '8:8:8'},
            {'key': 'baikal-clock-mode', 'value': 'individual'}]))
        Path(boot.PENDING).write_text('pending')
        (self.root / 'firmware/ENCODER_READY').write_text('ready')

    def check(self, digest, expected):
        verifier = mock.Mock(return_value=digest)
        self.assertEqual(boot.reconcile(verifier), 0)
        verifier.assert_called_once()
        saved = json.loads(self.settings.read_text())
        self.assertIs(saved['userAdjustableClocks'], expected)
        self.assertIs(saved['userAutoFan'], False)
        self.assertIs(saved['userDev'], False)
        self.assertEqual(saved['userAutoFanMin'], 40)
        self.assertEqual(saved['userTz'], 'UTC')
        self.assertFalse(Path(boot.PENDING).exists())
        self.assertTrue(json.loads(Path(boot.RESULT).read_text())['ok'])
        for directory in ('etc', 'run'):
            identity = json.loads((self.root / directory / 'bkb-firmware-identity.json').read_text())
            self.assertEqual(identity['chip_md5'], digest)
        options = {entry['key']: entry['value'] for entry in json.loads(self.options.read_text())}
        self.assertEqual(options, {'api-port': '4028', 'baikal-options': '400:40:55'})
        self.assertEqual((self.root / 'firmware/BRIDGE_GLOBAL_V1_READY').exists(), expected)
        self.assertFalse(Path(boot.ACTIVE).exists())

    def test_custom_enables_only_clocks_at_default(self):
        self.check(boot.CUSTOM_MD5, True)

    def test_legacy_v1_still_detected_without_relabeling(self):
        digest = '0e8467eddabf589ad94549a8d572c759'
        self.check(digest, True)
        record = json.loads((self.root / 'etc/bkb-firmware-identity.json').read_text())
        self.assertEqual(record['identity'], 'CUSTOM V1')
        marker = (self.root / 'firmware/BRIDGE_GLOBAL_V1_READY').read_text()
        self.assertIn('bridge_firmware=CUSTOM V1', marker)
        self.assertIn('chip_md5=' + digest, marker)

    def test_stock_leaves_clocks_disabled(self):
        self.check(boot.STOCK_MD5, False)

    def test_later_boot_does_not_read_or_change_any_preferences(self):
        self.check(boot.CUSTOM_MD5, True)
        changed = {'userAdjustableClocks': False, 'userAutoFan': True, 'userDev': True}
        self.settings.write_text(json.dumps(changed))
        self.options.write_text('[{"key":"baikal-board-clock-index","value":"8:8:8"}]')
        before = self.options.read_bytes()
        verifier = mock.Mock(side_effect=AssertionError('must not read'))
        self.assertEqual(boot.reconcile(verifier), 0)
        self.assertEqual(json.loads(self.settings.read_text()), changed)
        self.assertEqual(self.options.read_bytes(), before)
        Path(boot.PENDING).write_text('interrupted after completed result')
        self.assertEqual(boot.reconcile(verifier), 0)
        self.assertEqual(json.loads(self.settings.read_text()), changed)
        verifier.assert_not_called()

    def test_normal_update_without_image_marker_is_noop(self):
        Path(boot.PENDING).unlink()
        before = self.settings.read_bytes()
        verifier = mock.Mock()
        self.assertEqual(boot.reconcile(verifier), 0)
        verifier.assert_not_called()
        self.assertEqual(self.settings.read_bytes(), before)

    def test_unknown_and_legacy_custom_are_not_authorized(self):
        self.assertEqual(boot.reconcile(lambda log: 'f' * 32), 1)
        self.assertFalse(json.loads(self.settings.read_text())['userAdjustableClocks'])
        for directory in ('etc', 'run'):
            self.assertFalse((self.root / directory / 'bkb-firmware-identity.json').exists())
        self.assertEqual(json.loads(Path(boot.RESULT).read_text())['identity'], 'UNKNOWN')
        self.assertFalse(Path(boot.PENDING).exists())

    def test_failed_read_is_recorded_and_not_repeated(self):
        verifier = mock.Mock(side_effect=RuntimeError('read failed'))
        self.assertEqual(boot.reconcile(verifier), 1)
        self.assertIn('read failed', Path(boot.RESULT).read_text())
        self.assertFalse(json.loads(self.settings.read_text())['userAdjustableClocks'])
        self.assertEqual(boot.reconcile(verifier), 0)
        verifier.assert_called_once()

    def test_missing_encoder_does_not_enable_custom(self):
        (self.root / 'firmware/ENCODER_READY').unlink()
        self.assertEqual(boot.reconcile(lambda log: boot.CUSTOM_MD5), 1)
        self.assertFalse(json.loads(self.settings.read_text())['userAdjustableClocks'])
        for directory in ('etc', 'run'):
            self.assertFalse((self.root / directory / 'bkb-firmware-identity.json').exists())

    def test_invalid_options_dont_touch_hardware(self):
        self.options.write_text('{}')
        verifier = mock.Mock()
        self.assertEqual(boot.reconcile(verifier), 1)
        verifier.assert_not_called()
        self.assertFalse(json.loads(self.settings.read_text())['userAdjustableClocks'])

    def test_busy_launcher_does_not_touch_settings(self):
        with open(boot.LAUNCH_LOCK, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verifier = mock.Mock()
            before = self.settings.read_bytes()
            self.assertEqual(boot.reconcile(verifier), 1)
            verifier.assert_not_called()
            self.assertEqual(self.settings.read_bytes(), before)
            self.assertFalse(Path(boot.PENDING).exists())
        self.settings.write_text('{"userAdjustableClocks":true,"userAutoFan":true}')
        self.assertEqual(boot.reconcile(verifier), 0)
        self.assertTrue(json.loads(self.settings.read_text())['userAdjustableClocks'])

    def test_active_firmware_job_is_untouched(self):
        (self.root / 'run/bkb-firmware.lock').write_text('other operation')
        verifier = mock.Mock()
        self.assertEqual(boot.reconcile(verifier), 1)
        verifier.assert_not_called()
        self.assertEqual((self.root / 'run/bkb-firmware.lock').read_text(), 'other operation')

    def test_verify_uses_read_only_job_and_requires_app_mode(self):
        def run(command, timeout, log):
            self.assertEqual(command, [boot.WRAPPER, 'run-job', 'verify'])
            self.assertEqual(timeout, 110)
            (self.root / 'run/bkb-firmware-result.env').write_text(
                'op=verify\nok=1\nchip_md5=' + boot.CUSTOM_MD5 + '\n')
            (self.root / 'run/bkb-firmware-progress.log').write_text('Normal mode: True\n')
            return 0
        with mock.patch.object(boot, 'run_command', side_effect=run):
            self.assertEqual(boot.verify_chip(io.StringIO()), boot.CUSTOM_MD5)

    def test_read_timeout_restores_gpio_and_clears_locks(self):
        for name in ('bkb-firmware.lock', 'bkb-firmware.pid'):
            (self.root / 'run' / name).write_text('test')
        with mock.patch.object(boot, 'run_command', side_effect=[subprocess.TimeoutExpired('read', 110), 0]) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                boot.verify_chip(io.StringIO())
            self.assertEqual(run.call_args[0][0][-1], 'restore-gpio')
            self.assertEqual(run.call_args[0][1], 12)
        self.assertFalse((self.root / 'run/bkb-firmware.lock').exists())

    def test_runtime_names_hashes_and_boot_order_match_existing_paths(self):
        wrapper = (ROOT / 'overlay/opt/scripta/bin/bkb-firmware.sh').read_text()
        for digest in (boot.STOCK_MD5, boot.CUSTOM_MD5):
            self.assertIn(digest, wrapper)
        service = (ROOT / 'overlay/etc/systemd/system/blakestream-firmware-firstboot.service').read_text()
        self.assertIn('Before=cron.service lighttpd.service', service)
        self.assertIn('After=local-fs.target blakestream-system-perms.service rc-local.service', service)
        self.assertIn('ConditionPathExists=/opt/scripta/etc/bkb-firmware-firstboot.pending', service)
        self.assertIn('TimeoutStartSec=140', service)
        self.assertIn('firmware-firstboot.py --recover', service)

    def test_service_timeout_cleanup_never_flashes_or_retries(self):
        Path(boot.ACTIVE).write_text('{}')
        (self.root / 'run/bkb-firmware.lock').write_text('owned')
        with mock.patch.object(boot, 'run_command', return_value=0) as run:
            self.assertEqual(boot.recover(), 0)
            self.assertEqual(run.call_args[0][0][-1], 'restore-gpio')
            self.assertFalse(Path(boot.ACTIVE).exists())
            self.assertFalse(Path(boot.PENDING).exists())
            self.assertEqual(boot.recover(), 0)
            run.assert_called_once()
        self.assertFalse(json.loads(self.settings.read_text())['userAdjustableClocks'])
        self.assertFalse(json.loads(Path(boot.RESULT).read_text())['ok'])

    def test_unconfirmed_recovery_gets_service_cleanup_retry(self):
        self.bridge_running.return_value = False
        with mock.patch.object(boot, 'run_command', return_value=0):
            verifier = mock.Mock(side_effect=boot.RecoveryError('not in app mode'))
            self.assertEqual(boot.reconcile(verifier), 1)
            self.assertTrue(Path(boot.ACTIVE).exists())
            self.assertEqual(boot.recover(), 1)
            self.assertTrue(Path(boot.ACTIVE).exists())
            self.assertTrue(json.loads(Path(boot.RESULT).read_text())['recovery_required'])
            self.bridge_running.return_value = True
            self.assertEqual(boot.recover(), 0)
            self.assertFalse(Path(boot.ACTIVE).exists())
        self.assertFalse(json.loads(self.settings.read_text())['userAdjustableClocks'])


if __name__ == '__main__':
    unittest.main()
