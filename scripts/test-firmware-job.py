#!/usr/bin/env python3
"""Exercise the flash-job completion contract without USB, GPIO, or a miner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / 'overlay/opt/scripta/bin/bkb-firmware.sh'
SOURCE = WRAPPER.read_text()
JOB = SOURCE[SOURCE.index('fw_run_flash_job() {'):SOURCE.index('\ncase "${1:-}" in')]
CLEAR_CAPABILITY = SOURCE[SOURCE.index('fw_clear_bridge_capability() {'):
                          SOURCE.index('\nfw_restart_miner() {')]


class FlashJobTests(unittest.TestCase):
    def run_job(self, op='revert', verified=True, normal=True, flash_rc=0,
                restart_rc=0, capability_rc=0, initial_recovery=False,
                check_index_reset=False, identity_sync_rc=0):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'fake-python'
            fake.write_text('#!/bin/sh\n'
                            'test "$VERIFIED" = 0 || echo "FLASH OK + VERIFIED: contents match"\n'
                            'echo "  Normal mode: $NORMAL"\n'
                            'exit "$FLASH_RC"\n')
            fake.chmod(0o700)
            (root / 'ready').touch()
            (root / 'old-ready').touch()
            (root / 'etc').mkdir()
            (root / 'etc/identity.json').write_text(json.dumps({
                'identity': 'CUSTOM V1', 'chip_md5': '18babe0c4c67d9146e230f9dc423f2f2'}))
            (root / 'etc/settings.json').write_text('{"userAdjustableClocks":true}')
            (root / 'check-runtime.py').write_text('''import importlib.util,json,os
spec=importlib.util.spec_from_file_location('runtime',os.environ['RUNTIME_BUILDER'])
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
options={'baikal-experimental-clock-index':True,'baikal-board-clock-index':'9:12:16'}
runtime.apply_experimental_clock_index(options,'individual',
    (os.environ['FIXTURE']+'/etc/identity.json',),os.environ['FIXTURE']+'/etc/settings.json')
assert 'baikal-board-clock-index' not in options, 'stale custom identity authorized old Index settings'
assert 'baikal-experimental-clock-index' not in options
''')
            if initial_recovery:
                (root / 'recovery').touch()
            shell = '''set -u
cd "$FIXTURE"
FW="$FIXTURE"
PIDFILE=pid
PROG=progress
RECOVERY_REQUIRED="$FIXTURE/recovery"
BRIDGE_GLOBAL_READY=ready
BRIDGE_GLOBAL_OLD_READY=old-ready
IDENTITY="$FIXTURE/etc/identity.json"
PY="$FIXTURE/fake-python"
DFU=unused
CUSTOM_MD5=custom
STOCK_MD5=stock
fw_set_phase() { echo "$1" >> phases; }
fw_write_identity() { printf '%s:%s' "$1" "$2" > identity; }
fw_mark_bridge_global_ready() { test ! -f identity || exit 99; touch capability-called; return "$CAPABILITY_RC"; }
fw_fsync_dir() { test "$1" != "$FIXTURE/etc" || return "$IDENTITY_SYNC_RC"; }
fw_mark_recovery_required() { touch "$RECOVERY_REQUIRED"; }
fw_clear_recovery_required() { rm -f -- "$RECOVERY_REQUIRED"; }
fwprog_note() { echo "$*" >> "$PROG"; }
fw_restart_miner() {
    if [ "$CHECK_INDEX_RESET" = 1 ]; then
        "$TEST_PYTHON" "$FIXTURE/check-runtime.py" || return 1
    fi
    touch restart-called
    return "$RESTART_RC"
}
fw_result_write() { printf '%s\\n' "$1" "$2" "$3" "$7" > result; }
fw_job_finish() { touch finished; }
fw_job_recovery_hold() { touch recovery-held finished; }
''' + CLEAR_CAPABILITY + '\n' + JOB + '\nfw_run_flash_job "$OP" unused unused "$OP"\n'
            env = dict(os.environ, FIXTURE=temp, OP=op, VERIFIED=str(int(verified)),
                       NORMAL=str(normal), FLASH_RC=str(flash_rc),
                       RESTART_RC=str(restart_rc), CAPABILITY_RC=str(capability_rc),
                       CHECK_INDEX_RESET=str(int(check_index_reset)), TEST_PYTHON=sys.executable,
                       RUNTIME_BUILDER=str(ROOT / 'overlay/opt/scripta/bin/build-miner-runtime.py'),
                       IDENTITY_SYNC_RC=str(identity_sync_rc))
            proc = subprocess.run(['/bin/sh'], input=shell, text=True, env=env,
                                  capture_output=True, timeout=5)
            self.assertTrue((root / 'result').exists(), proc.stderr + proc.stdout)
            result = (root / 'result').read_text().splitlines()
            self.assertTrue((root / 'finished').exists())
            self.assertFalse((root / 'ready').exists())
            self.assertFalse((root / 'old-ready').exists())
            return (proc.returncode, result, (root / 'restart-called').exists(),
                    (root / 'phases').read_text(), (root / 'recovery').exists(),
                    (root / 'recovery-held').exists())

    def test_stock_and_custom_success(self):
        for op in ('revert', 'flash-custom'):
            code, result, restarted, phases, recovery, held = self.run_job(op)
            self.assertEqual((code, result[:3], restarted), (0, [op, '1', '0'], True))
            self.assertIn('restart', phases)
            self.assertFalse(recovery)
            self.assertFalse(held)

    def test_stale_custom_identity_cannot_restore_indices_on_first_restart(self):
        for op in ('revert', 'flash-custom'):
            with self.subTest(operation=op):
                code, result, restarted, _, recovery, _ = self.run_job(op, check_index_reset=True)
                self.assertEqual((code, result[:3], restarted), (0, [op, '1', '0'], True))
                self.assertFalse(recovery)

    def test_identity_invalidation_must_be_durable_before_flash(self):
        code, result, restarted, _, recovery, _ = self.run_job(identity_sync_rc=1)
        self.assertEqual((code, result[:3], restarted), (1, ['revert', '0', '1'], False))
        self.assertIn('capability state could not be cleared', result[3])
        self.assertFalse(recovery)

    def test_slow_usb_snapshot_does_not_skip_restart(self):
        code, result, restarted, _, _, _ = self.run_job(normal=False)
        self.assertEqual((code, result[1], restarted), (0, '1', True))

    def test_restart_failure(self):
        code, result, _, _, _, _ = self.run_job(restart_rc=7)
        self.assertEqual((code, result[1:3]), (7, ['0', '7']))
        self.assertIn('restart failed', result[3])

    def test_missing_verification_marker(self):
        code, result, restarted, _, recovery, held = self.run_job(verified=False)
        self.assertEqual((code, result[1:3]), (1, ['0', '1']))
        self.assertFalse(restarted)
        self.assertTrue(recovery)
        self.assertTrue(held)

    def test_flash_failure(self):
        code, result, restarted, _, recovery, held = self.run_job(flash_rc=9)
        self.assertEqual((code, result[1:3]), (9, ['0', '9']))
        self.assertFalse(restarted)
        self.assertTrue(recovery)
        self.assertTrue(held)
        self.assertIn('recovery required', result[3])

    def test_safe_prewrite_failure_restarts_only_without_existing_hold(self):
        code, _, restarted, _, recovery, held = self.run_job(flash_rc=3)
        self.assertEqual(code, 3)
        self.assertTrue(restarted)
        self.assertFalse(recovery)
        self.assertFalse(held)
        code, _, restarted, _, recovery, held = self.run_job(flash_rc=3, initial_recovery=True)
        self.assertEqual(code, 3)
        self.assertFalse(restarted)
        self.assertTrue(recovery)
        self.assertTrue(held)

    def test_successful_explicit_recovery_clears_hold(self):
        code, result, restarted, _, recovery, held = self.run_job(initial_recovery=True)
        self.assertEqual((code, result[1:3], restarted), (0, ['1', '0'], True))
        self.assertFalse(recovery)
        self.assertFalse(held)

    def test_capability_failure(self):
        code, result, _, _, recovery, held = self.run_job('flash-custom', capability_rc=1)
        self.assertEqual((code, result[1:3]), (1, ['0', '1']))
        self.assertIn('capability activation failed', result[3])
        self.assertFalse(recovery)
        self.assertFalse(held)

    def test_recovery_marker_is_fsynced_before_publish_and_after_clear(self):
        mark = SOURCE[SOURCE.index('fw_mark_recovery_required() {'):
                      SOURCE.index('\nfw_clear_recovery_required() {')]
        self.assertLess(mark.index('fw_fsync_file "$recovery_tmp"'),
                        mark.index('mv -f -- "$recovery_tmp" "$RECOVERY_REQUIRED"'))
        self.assertLess(mark.index('mv -f -- "$recovery_tmp" "$RECOVERY_REQUIRED"'),
                        mark.index('fw_fsync_dir "$RECOVERY_DIR"'))
        clear = SOURCE[SOURCE.index('fw_clear_recovery_required() {'):
                       SOURCE.index('\nfw_restart_miner() {')]
        self.assertLess(clear.index('rm -f -- "$RECOVERY_REQUIRED"'),
                        clear.index('fw_fsync_dir "$RECOVERY_DIR"'))
        capability = SOURCE[SOURCE.index('fw_mark_bridge_global_ready() {'):
                            SOURCE.index('\nfw_fsync_file() {')]
        self.assertLess(capability.index('fw_fsync_file "$tmp"'),
                        capability.index('mv "$tmp" "$BRIDGE_GLOBAL_READY"'))
        self.assertLess(capability.index('mv "$tmp" "$BRIDGE_GLOBAL_READY"'),
                        capability.index('fw_fsync_dir "$FW"'))
        clear_capability = SOURCE[SOURCE.index('fw_clear_bridge_capability() {'):
                                  SOURCE.index('\nfw_restart_miner() {')]
        self.assertLess(clear_capability.index('rm -f -- "$BRIDGE_GLOBAL_READY"'),
                        clear_capability.index('fw_fsync_dir "$FW"'))
        self.assertLess(SOURCE.index('fw_clear_bridge_capability'),
                        SOURCE.index('BKB_DEFER_MINER_RESTART=1 BKB_RECOVERY_REQUIRED'))
        flasher = (ROOT / 'overlay/opt/scripta/firmware/dfu_flash.py').read_text()
        self.assertIn('os.fsync(fd)', flasher)
        self.assertLess(flasher.index("mark_recovery_required('target-write-started')"),
                        flasher.index('if not dfu_download(img):'))
        self.assertLess(flasher.index('os.fsync(fd)', flasher.index('def dfu_upload_verified')),
                        flasher.index('os.rename(first, out)', flasher.index('def dfu_upload_verified')))
        self.assertLess(flasher.index('os.rename(first, out)', flasher.index('def dfu_upload_verified')),
                        flasher.index('_fsync_dir(parent)', flasher.index('def dfu_upload_verified')))
        durable_dir = flasher[flasher.index('def ensure_durable_root_dir(path):'):
                              flasher.index('\ndef recovery_required_present()')]
        self.assertLess(durable_dir.index('os.mkdir(path, 0o700)'),
                        durable_dir.index('_fsync_dir(path)'))
        self.assertLess(durable_dir.index('_fsync_dir(path)'),
                        durable_dir.index('_fsync_dir(parent)'))
        upload = flasher[flasher.index('def dfu_upload_verified(out):'):
                         flasher.index('\ndef readback_md5')]
        self.assertLess(upload.index('ensure_durable_root_dir(parent)'),
                        upload.index('dfu_upload(first)'))

    def test_explicit_recovery_environment_records_preexisting_state(self):
        flasher = (ROOT / 'overlay/opt/scripta/firmware/dfu_flash.py').read_text()
        start = flasher.index('recovery_env = os.environ.get(RECOVERY_REQUIRED_ENV)')
        end = flasher.index('\n    # In an ordinary operation', start)
        selection = flasher[start:end]
        self.assertIn("if recovery_env in ('0', '1'):", selection)
        self.assertIn("recovery_mode = (recovery_env == '1')", selection)
        self.assertIn('else:\n        recovery_mode = recovery_required_present()', selection)

    def test_legacy_synchronous_verbs_are_denied(self):
        for args in (('verify',), ('revert', '--confirm'), ('flash-custom', '--confirm')):
            result = subprocess.run(['/bin/sh', str(WRAPPER)] + list(args),
                                    text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn('usage: start-job', result.stdout, args)
        dispatch = SOURCE[SOURCE.index('\ncase "${1:-}" in'):]
        self.assertNotIn('\n  revert)', dispatch)
        self.assertNotIn('\n  flash-custom)', dispatch)
        self.assertNotIn('\n  verify)', dispatch)


if __name__ == '__main__':
    unittest.main()
