#!/usr/bin/env python3
"""Low-memory defaults and retained-tail checks, no miner or network access."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('log_writer', ROOT / 'overlay/opt/scripta/bin/capped-log-writer.py')
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)


class MemoryDefaults(unittest.TestCase):
    @staticmethod
    def fixture_log_writer(root, paths):
        source = (ROOT / 'overlay/opt/scripta/bin/capped-log-writer.py').read_text()
        old = "LOG_PATHS = ('/run/blakestream-miner/sgminer.log',\n             '/var/log/blakestream-miner/sgminer.log')"
        replacement = 'LOG_PATHS = ' + repr(tuple(str(path) for path in paths))
        if old not in source:
            raise AssertionError('production log allowlist changed unexpectedly')
        writer = root / 'capped-log-writer.py'
        writer.write_text(source.replace(old, replacement, 1))
        writer.chmod(0o755)
        return writer

    def test_log_writer_oom_protection_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(L.protect_log_writer(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])
            modern = Path(tmp) / 'oom_score_adj'
            legacy = Path(tmp) / 'oom_adj'
            modern.write_text('0')
            legacy.write_text('0')
            self.assertTrue(L.protect_log_writer(tmp))
            self.assertEqual(modern.read_text(), '-900')
            self.assertEqual(legacy.read_text(), '0')
            modern.unlink()
            self.assertTrue(L.protect_log_writer(tmp))
            self.assertEqual(legacy.read_text(), '-16')

    def test_php_worker_memory_is_bounded(self):
        config = (ROOT / 'overlay/etc/lighttpd/conf-available/15-fastcgi-php.conf').read_text()
        self.assertIn('"max-procs" => 1', config)
        self.assertIn('"PHP_FCGI_CHILDREN" => "2"', config)
        self.assertIn('"PHP_FCGI_MAX_REQUESTS" => "100"', config)

    def test_log_target_is_independent_of_dev_tools(self):
        launcher = (ROOT / 'overlay/opt/scripta/startup/miner-start.sh').read_text()
        block = launcher[launcher.index('SGMINER_LOG_MAX='):launcher.index('\nlog()')]
        block = block.replace('/usr/bin/python', shlex.quote(sys.executable))
        self.assertNotIn('userDev\'', block)
        cases = [(None, 'RAM'), ('broken JSON', 'RAM'), ([], 'RAM'), ({}, 'RAM')]
        for dev in (False, True):
            for target, expected in [('RAM', 'RAM'), ('SD', 'SD'), ('sd', 'SD'),
                                     ('invalid', 'RAM'), (None, 'RAM'), ([], 'RAM')]:
                cases.append(({'userDev': dev, 'userDevLogTarget': target}, expected))
        with tempfile.TemporaryDirectory(prefix='bkb memory ') as tmp:
            config = Path(tmp) / 'settings.json'
            for settings, expected in cases:
                if settings is None:
                    if config.exists(): config.unlink()
                else:
                    config.write_text(settings if isinstance(settings, str) else json.dumps(settings))
                env = dict(os.environ, SCRIPTA_CONF=str(config))
                result = subprocess.check_output(['bash', '-c', block +
                    '\nprintf "%s %s %s" "$SGMINER_LOG_TARGET" "$SGMINER_LOG_MAX" "$SGMINER_LOG_KEEP"'], env=env)
                self.assertEqual(result.decode(), expected + ' 4194304 2097152')

    def test_both_launch_paths_capture_and_cap_output(self):
        launcher = (ROOT / 'overlay/opt/scripta/startup/miner-start.sh').read_text()
        start = launcher.index('case $BUILD_RC in\n    0)') + len('case $BUILD_RC in\n    0)')
        block = launcher[start:launcher.index('\n        ;;', start)]
        block = block.replace('/usr/bin/python', shlex.quote(sys.executable))
        with tempfile.TemporaryDirectory(prefix='bkb memory ') as tmp:
            root = Path(tmp)
            miner = root / 'fixture-miner'
            miner.write_text('#!/bin/sh\n[ "$1" = "--text-only" ] || exit 5\n'
                             'i=0\nwhile [ "$i" -lt 2000 ]; do\n'
                             'printf "Accepted fixture share %s\\n" "$i"\n'
                             'i=$((i + 1))\ndone\nprintf "last stderr line\\n" >&2\n')
            miner.chmod(0o755)
            ram, sd = root / 'ram.log', root / 'sd.log'
            block = block.replace('/run/blakestream-miner/sgminer.log', shlex.quote(str(ram))).replace('/var/log/blakestream-miner/sgminer.log', shlex.quote(str(sd)))
            block = block.replace('chown root:www-data "$SGMINER_LOG" || exit 1', ':')
            writer = self.fixture_log_writer(root, (ram, sd))
            for target, destination in [('RAM', ram), ('SD', sd)]:
                env = dict(os.environ, SGMINER_LOG_TARGET=target, SGMINER_LOG=str(destination), SGMINER_LOG_CONFIG_OK='1',
                           SGMINER_LOG_MAX='8192', SGMINER_LOG_KEEP='4096', SGMINER=str(miner),
                           LOG_WRITER=str(writer), RUNTIME_CONF='unused')
                prelude = 'log() { :; }\nfix_screen_dir() { :; }\nscreen() { shift 2; "$@"; }\n'
                subprocess.run(['bash', '-c', prelude + block], env=env, check=True)
                output = destination.read_bytes()
                self.assertLessEqual(len(output), 8192)
                self.assertIn(b'log trimmed;', output)
                self.assertIn(b'Accepted fixture share 1999', output)
                self.assertIn(b'last stderr line', output)

    def test_launcher_never_imports_or_deletes_sealed_logs(self):
        launcher = (ROOT / 'overlay/opt/scripta/startup/miner-start.sh').read_text()
        self.assertIn('SGMINER_LOG=/run/blakestream-miner/sgminer.log', launcher)
        self.assertIn('SGMINER_LOG=/var/log/blakestream-miner/sgminer.log', launcher)
        self.assertNotIn('/run/blakestream/sgminer.log', launcher)
        self.assertNotIn('rm -f "$SGMINER_LOG"', launcher)
        self.assertNotIn('mv "$SGMINER_LOG"', launcher)
        prepare = (ROOT / 'overlay/opt/scripta/bin/prepare-runtime-dir.sh').read_text()
        self.assertIn('/run/blakestream/sgminer.log', prepare)
        self.assertIn('Never migrate or read these former privileged outputs', prepare)

    def test_ram_cap_matches_launcher_and_storage_ui(self):
        self.assertEqual(L.DEFAULT_MAX_BYTES, 4194304)
        self.assertEqual(L.DEFAULT_KEEP_BYTES, 2097152)
        launcher = (ROOT / 'overlay/opt/scripta/startup/miner-start.sh').read_text()
        self.assertIn('SGMINER_LOG_MAX=${SGMINER_LOG_MAX:-4194304}', launcher)
        self.assertIn('SGMINER_LOG_KEEP=${SGMINER_LOG_KEEP:-2097152}', launcher)
        self.assertIn('$SGMINER_CAP = 4194304;', (ROOT / 'overlay/var/www/f_dev.php').read_text())
        self.assertIn("storage_stat('sgminer.log', '/var/log/blakestream-miner/sgminer.log', $SGMINER_CAP)",
                      (ROOT / 'overlay/var/www/f_dev.php').read_text())
        self.assertIn("$scope.devLogRamCap = '4 MiB';", (ROOT / 'overlay/var/www/ng/controllers.js').read_text())

    def test_no_extra_daily_sgminer_archives(self):
        config = (ROOT / 'overlay/etc/logrotate.d/sgminer').read_text()
        active = [line.strip() for line in config.splitlines() if line.strip() and not line.startswith('#')]
        self.assertEqual(active, [])

    def test_trim_preserves_recent_bytes_within_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sgminer.log'
            tail = b'recent log\n' * 200
            path.write_bytes(b'old log\n' * 2000 + tail)
            size = L.trim_log(str(path), 8192, 4096)
            self.assertLessEqual(size, 8192)
            self.assertTrue(path.read_bytes().endswith(tail))
            self.assertIn(b'log trimmed;', path.read_bytes())

    def test_small_log_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sgminer.log'
            path.write_bytes(b'keep this log\n')
            self.assertEqual(L.trim_log(str(path), 8192, 4096), len(b'keep this log\n'))
            self.assertEqual(path.read_bytes(), b'keep this log\n')

    def test_maintenance_cadence_is_preserved_without_alert_collisions(self):
        slots = []
        for name, start in [('system-perms', 4), ('runtime-dir', 8)]:
            content = (ROOT / ('overlay/etc/cron.d/blakestream-' + name)).read_text()
            active = [line for line in content.splitlines() if line and not line.startswith('#')]
            if name == 'system-perms':
                self.assertEqual(len(active), 1)
                unit = (ROOT / 'overlay/etc/systemd/system/blakestream-system-perms.service').read_text()
                self.assertIn('ExecStart=/opt/scripta/bin/repair-system-perms.sh', unit)
            else:
                self.assertEqual(len(active), 2)
                self.assertTrue(active[0].startswith('@reboot root '))
            self.assertEqual(active[-1].split()[0], '%d-59/15' % start)
            minutes = set(range(start, 60, 15))
            self.assertEqual(len(minutes), 4)
            self.assertFalse(minutes & set(range(0, 60, 5)))
            self.assertFalse(minutes & set(range(0, 60, 3)))
            slots.append(minutes)
        self.assertFalse(slots[0] & slots[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
