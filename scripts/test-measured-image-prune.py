#!/usr/bin/env python3
"""Personal measurements and identities must not ship in an SD image."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PruneTests(unittest.TestCase):
    def test_temporary_state_is_emptied_without_following_links(self):
        with tempfile.TemporaryDirectory(prefix='bkb-temp-prune-') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            outside = root / 'outside'
            outside.mkdir()
            sentinel = outside / 'keep'
            sentinel.write_text('do not follow')
            for relative in ('tmp', 'var/tmp'):
                target = root / relative
                (target / 'systemd-private-fixture/service').mkdir(parents=True)
                (target / 'systemd-private-fixture/service/state').write_text('factory state')
                (target / 'outside-link').symlink_to(sentinel)
            subprocess.check_call(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp])
            self.assertEqual(sentinel.read_text(), 'do not follow')
            for relative in ('tmp', 'var/tmp'):
                target = root / relative
                self.assertEqual(list(target.iterdir()), [])
                self.assertEqual(target.stat().st_mode & 0o7777, 0o1777)

    def test_login_log_cleanup_accepts_standard_var_run_symlink(self):
        with tempfile.TemporaryDirectory(prefix='bkb-login-log-prune-') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            (root / 'run').mkdir()
            (root / 'var').mkdir()
            (root / 'var/run').symlink_to('../run', target_is_directory=True)
            (root / 'run/utmp').write_bytes(b'factory login state')
            subprocess.check_call(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp])
            self.assertTrue((root / 'var/run').is_symlink())
            self.assertEqual((root / 'run/utmp').read_bytes(), b'')

    def test_inactive_factory_web_error_log_is_removed(self):
        with tempfile.TemporaryDirectory(prefix='bkb-factory-log-prune-') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            logs = root / 'var/log/lighttpd'
            logs.mkdir(parents=True)
            (logs / 'error.log').write_text('obsolete factory log')
            (logs / 'unrelated.log').write_text('keep')
            subprocess.check_call(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp])
            self.assertFalse((logs / 'error.log').exists())
            self.assertEqual((logs / 'unrelated.log').read_text(), 'keep')

    def test_only_measurement_files_removed(self):
        with tempfile.TemporaryDirectory(prefix='bkb-image-prune-') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            for sub in ('opt/scripta/var', 'run/blakestream'):
                target = root / sub
                target.mkdir(parents=True)
                for name in ('measured-rates-control.json', 'measured-rates-ab.sqlite',
                             'measured-rates-ab.sqlite-journal', 'measured-rates-state-ab.json',
                             'measured-rates-status.json', 'measured-rates.lock',
                             'measured-rates-command.lock', 'accepted-work-rates.json',
                             'network-events.log', 'system-events.log', 'cooling-events.log',
                             'lighttpd-error.log', 'diagnostic-log-state.json', 'diagnostic-logs.lock'):
                    (target / name).write_text('private fixture')
                (target / 'other-history.json').write_text('keep')
            subprocess.check_call(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp])
            for sub in ('opt/scripta/var', 'run/blakestream'):
                self.assertEqual(sorted(os.listdir(root / sub)), ['other-history.json'])

    def test_reject_symlink(self):
        with tempfile.TemporaryDirectory(prefix='bkb-image-prune-') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            (root / 'outside').mkdir()
            sentinel = root / 'outside/measured-rates-control.json'
            sentinel.write_text('keep')
            (root / 'opt/scripta/var').symlink_to(root / 'outside', target_is_directory=True)
            result = subprocess.call(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp])
            self.assertNotEqual(result, 0)
            self.assertTrue(sentinel.exists())

    def test_private_archives_removed_but_public_baseline_kept(self):
        with tempfile.TemporaryDirectory(prefix='bkb-backup-prune-') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            for sub in ('opt/scripta/etc/backup/private', 'var/lib/blakestream-clock-baselines/board/algo', 'run/blakestream'):
                (root / sub).mkdir(parents=True)
                (root / sub / 'private.json').write_text('private')
            (root / 'opt/scripta/etc/network-restore.json').write_text('private network')
            (root / 'opt/scripta/etc/bkb-firmware-runtime.json').write_text('device-specific timing')
            firmware_private = ('opt/scripta/etc/bkb-firmware-firstboot.json',
                                'opt/scripta/etc/bkb-firmware-identity.json',
                                'run/blakestream/bkb-firmware-identity.json',
                                'opt/scripta/var/firmware-firstboot.log',
                                'var/lib/blakestream-firstboot/result.json',
                                'var/log/blakestream-firstboot/firmware-firstboot.log',
                                'run/blakestream-firmware/bkb-firmware-result.env',
                                'run/blakestream-backup/backup-export.zip',
                                'var/lib/blakestream-backups/private/manifest.json',
                                'opt/scripta/backups/bkb-preflash-backup.bin',
                                'opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY',
                                'opt/scripta/firmware/BRIDGE_GLOBAL_V11_READY')
            for sub in firmware_private:
                (root / sub).parent.mkdir(parents=True, exist_ok=True)
                (root / sub).write_text('installation state')
            (root / 'opt/scripta/etc/clock-baseline.json').write_text('public lab examples')
            (root / 'run/blakestream/backup-export.zip').write_text('private archive')
            subprocess.check_call(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp])
            for sub in ('opt/scripta/etc/backup', 'var/lib/blakestream-clock-baselines',
                        'opt/scripta/etc/network-restore.json', 'run/blakestream/backup-export.zip',
                        'opt/scripta/etc/bkb-firmware-runtime.json',
                        'run/blakestream-backup', 'var/lib/blakestream-backups',
                        'opt/scripta/backups'):
                self.assertFalse((root / sub).exists())
            self.assertEqual((root / 'opt/scripta/etc/clock-baseline.json').read_text(), 'public lab examples')
            for sub in firmware_private:
                self.assertFalse((root / sub).exists())
            self.assertTrue((root / 'var/lib/blakestream-firstboot/firmware-firstboot.pending').is_file())


if __name__ == '__main__':
    unittest.main()
