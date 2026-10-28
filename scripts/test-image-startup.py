#!/usr/bin/env python3
"""Offline startup configuration checks; no service or hardware operations."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('startup', ROOT / 'scripts/prepare-image-startup.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'etc').mkdir()
        (self.root / 'opt/scripta').mkdir(parents=True)
        self.rc = self.root / 'etc/rc.local'
        self.original = ('#!/bin/sh -e\n'
                         'sudo /usr/bin/update_fw.py\n'
                         'sudo /etc/init.d/watchdog restart\n'
                         'sudo echo 1 > /sys/class/gpio_sw/standby_led/data\n'
                         'sudo /usr/sbin/ntpdate -b -u 0.pool.ntp.org\n'
                         'exit 0\n')
        self.rc.write_text(self.original)
        self.rc.chmod(0o755)

    def test_only_obsolete_clock_command_is_removed(self):
        startup.prepare(self.root)
        expected = self.original.replace('sudo /usr/bin/update_fw.py\n', '')
        expected = expected.replace('sudo /usr/sbin/ntpdate -b -u 0.pool.ntp.org\n', '')
        self.assertEqual(self.rc.read_text(), expected)
        self.assertEqual(self.rc.stat().st_mode & 0o777, 0o755)
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/ntp.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/sendmail.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/shellinabox.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/dnsmasq.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/smbd.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/nmbd.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/avahi-daemon.socket'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service'),
                         '/lib/systemd/system/systemd-timesyncd.service')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/multi-user.target.wants/blakestream-system-perms.service'),
                         '../blakestream-system-perms.service')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/multi-user.target.wants/blakestream-device-identity.service'),
                         '../blakestream-device-identity.service')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/multi-user.target.wants/blakestream-terminal.service'),
                         '../blakestream-terminal.service')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/multi-user.target.wants/blakestream-firmware-recovery.service'),
                         '../blakestream-firmware-recovery.service')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/multi-user.target.wants/blakestream-firmware-firstboot.service'),
                         '../blakestream-firmware-firstboot.service')
        pending = self.root / 'var/lib/blakestream-firstboot/firmware-firstboot.pending'
        self.assertTrue(pending.is_file())
        self.assertEqual(pending.stat().st_mode & 0o777, 0o644)
        before = self.rc.stat().st_mtime_ns
        startup.prepare(self.root)
        self.assertEqual(self.rc.stat().st_mtime_ns, before)

    def test_unexpected_clock_command_fails_without_changes(self):
        text = self.original.replace('0.pool.ntp.org', '0.pool.ntp.org && echo retained')
        self.rc.write_text(text)
        with self.assertRaisesRegex(ValueError, 'unexpected ntpdate'):
            startup.prepare(self.root)
        self.assertEqual(self.rc.read_text(), text)
        self.assertFalse((self.root / 'etc/systemd').exists())

    def test_unexpected_factory_firmware_command_fails_without_changes(self):
        text = self.original.replace('/usr/bin/update_fw.py', '/usr/bin/update_fw.py --force')
        self.rc.write_text(text)
        with self.assertRaisesRegex(ValueError, 'unexpected update_fw.py'):
            startup.prepare(self.root)
        self.assertEqual(self.rc.read_text(), text)
        self.assertFalse((self.root / 'etc/systemd').exists())

    def test_sshd_allows_only_documented_root_account(self):
        ssh = self.root / 'etc/ssh'
        ssh.mkdir()
        config = ssh / 'sshd_config'
        config.write_text('Port 22\nPermitRootLogin yes\n')
        startup.prepare(self.root)
        self.assertIn('AllowUsers root\n', config.read_text())
        before = config.read_text()
        startup.prepare(self.root)
        self.assertEqual(config.read_text(), before)

    def test_conflicting_sshd_allowlist_is_rejected(self):
        ssh = self.root / 'etc/ssh'
        ssh.mkdir()
        config = ssh / 'sshd_config'
        config.write_text('AllowUsers root baikal\n')
        with self.assertRaisesRegex(ValueError, 'unexpected AllowUsers'):
            startup.prepare(self.root)
        self.assertEqual(config.read_text(), 'AllowUsers root baikal\n')

    def test_rc_symlink_is_rejected(self):
        other = self.root / 'outside'
        other.write_text(self.original)
        self.rc.unlink()
        self.rc.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'symlinked rc.local'):
            startup.prepare(self.root)
        self.assertEqual(other.read_text(), self.original)

    def test_service_parent_symlink_is_rejected(self):
        other = self.root / 'outside'
        other.mkdir()
        (self.root / 'etc/systemd').symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'through a symlink'):
            startup.prepare(self.root)
        self.assertEqual(self.rc.read_text(), self.original)
        self.assertEqual(list(other.iterdir()), [])

    def test_local_service_override_is_not_overwritten(self):
        unit = self.root / 'etc/systemd/system/sendmail.service'
        unit.parent.mkdir(parents=True)
        unit.write_text('local override')
        with self.assertRaisesRegex(ValueError, 'service override'):
            startup.prepare(self.root)
        self.assertEqual(unit.read_text(), 'local override')
        self.assertEqual(self.rc.read_text(), self.original)

    def test_host_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'staged Scripta rootfs'):
            startup.prepare('/')

    def test_boot_order_and_obsolete_hook_cleanup(self):
        unit = (ROOT / 'overlay/etc/systemd/system/blakestream-system-perms.service').read_text()
        self.assertIn('After=systemd-tmpfiles-setup.service', unit)
        self.assertIn('Before=cron.service lighttpd.service ssh.service shellinabox.service', unit)
        self.assertIn('ExecStart=/opt/scripta/bin/repair-system-perms.sh', unit)
        self.assertNotIn('ntpdate', (ROOT / 'overlay/etc/cron.d/scripta').read_text())
        pruner = (ROOT / 'scripts/prune-rootfs.sh').read_text()
        for path in ('etc/dhcp/dhclient-exit-hooks.d/ntp',
                     'etc/dhcp/dhclient-exit-hooks.d/ntpdate',
                     'etc/network/if-up.d/ntpdate'):
            self.assertIn('"' + path + '"', pruner)
        self.assertIn('prepare-image-startup.py', pruner)

    def test_ethernet_is_up_before_networkmanager_carrier_probe(self):
        unit = (ROOT / 'overlay/etc/systemd/system/NetworkManager.service.d/blakestream-recovery.conf').read_text()
        self.assertIn('ExecStartPre=-/sbin/ip link set dev eth0 up\n', unit)
        self.assertIn('ExecStartPost=-/bin/systemctl --no-block start blakestream-network-reconcile.service', unit)
        self.assertNotIn('wlan0', unit)
        self.assertNotIn('rp_filter', unit)


if __name__ == '__main__':
    unittest.main()
