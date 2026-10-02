#!/usr/bin/env python3
"""Exercise the flash-job completion contract without USB, GPIO, or a miner."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'overlay/opt/scripta/bin/bkb-firmware.sh').read_text()
JOB = SOURCE[SOURCE.index('fw_run_flash_job() {'):SOURCE.index('\ncase "${1:-}" in')]


class FlashJobTests(unittest.TestCase):
    def run_job(self, op='revert', verified=True, normal=True, flash_rc=0,
                restart_rc=0, capability_rc=0):
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
            shell = '''set -u
cd "$FIXTURE"
PIDFILE=pid
PROG=progress
BRIDGE_GLOBAL_READY=ready
BRIDGE_GLOBAL_OLD_READY=old-ready
PY="$FIXTURE/fake-python"
DFU=unused
CUSTOM_MD5=custom
STOCK_MD5=stock
fw_set_phase() { echo "$1" >> phases; }
fw_write_identity() { printf '%s:%s' "$1" "$2" > identity; }
fw_mark_bridge_global_ready() { test ! -f identity || exit 99; touch capability-called; return "$CAPABILITY_RC"; }
fwprog_note() { echo "$*" >> "$PROG"; }
fw_restart_miner() { touch restart-called; return "$RESTART_RC"; }
fw_result_write() { printf '%s\\n' "$1" "$2" "$3" "$7" > result; }
fw_job_finish() { touch finished; }
''' + JOB + '\nfw_run_flash_job "$OP" unused unused "$OP"\n'
            env = dict(os.environ, FIXTURE=temp, OP=op, VERIFIED=str(int(verified)),
                       NORMAL=str(normal), FLASH_RC=str(flash_rc),
                       RESTART_RC=str(restart_rc), CAPABILITY_RC=str(capability_rc))
            proc = subprocess.run(['/bin/sh'], input=shell, text=True, env=env,
                                  capture_output=True, timeout=5)
            result = (root / 'result').read_text().splitlines()
            self.assertTrue((root / 'finished').exists())
            self.assertFalse((root / 'ready').exists())
            self.assertFalse((root / 'old-ready').exists())
            return proc.returncode, result, (root / 'restart-called').exists(), (root / 'phases').read_text()

    def test_stock_and_custom_success(self):
        for op in ('revert', 'flash-custom'):
            code, result, restarted, phases = self.run_job(op)
            self.assertEqual((code, result[:3], restarted), (0, [op, '1', '0'], True))
            self.assertIn('restart', phases)

    def test_slow_usb_snapshot_does_not_skip_restart(self):
        code, result, restarted, _ = self.run_job(normal=False)
        self.assertEqual((code, result[1], restarted), (0, '1', True))

    def test_restart_failure(self):
        code, result, _, _ = self.run_job(restart_rc=7)
        self.assertEqual((code, result[1:3]), (7, ['0', '7']))
        self.assertIn('restart failed', result[3])

    def test_missing_verification_marker(self):
        code, result, _, _ = self.run_job(verified=False)
        self.assertEqual((code, result[1:3]), (1, ['0', '1']))

    def test_flash_failure(self):
        code, result, _, _ = self.run_job(flash_rc=9)
        self.assertEqual((code, result[1:3]), (9, ['0', '9']))

    def test_capability_failure(self):
        code, result, _, _ = self.run_job('flash-custom', capability_rc=1)
        self.assertEqual((code, result[1:3]), (1, ['0', '1']))
        self.assertIn('capability activation failed', result[3])


if __name__ == '__main__':
    unittest.main()
