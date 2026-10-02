#!/usr/bin/env python3
"""Small packaging regressions without mounts, root, or real compression."""
import hashlib
import configparser
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class PackagingTests(unittest.TestCase):
    def test_fetch_rejects_existing_bad_image_without_replacing_it(self):
        with tempfile.TemporaryDirectory(prefix='bkb factory ') as tmp:
            root = Path(tmp)
            image = root / 'factory.img'
            image.write_bytes(b'keep original')
            checksum = root / 'factory.img.sha256'
            checksum.write_text('keep previous checksum')
            env = dict(os.environ, BKB_FACTORY_IMG=str(image))
            args = ['bash', str(ROOT / 'scripts/01-fetch.sh')]
            result = subprocess.run(args, cwd='/', env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'wrong size', result.stderr)
            # Simulate the expected size without allocating or hashing 3.9 GB.
            bindir = root / 'bin'
            bindir.mkdir()
            stat = bindir / 'stat'
            stat.write_text('#!/bin/sh\nprintf "3904897024\\n"\n')
            stat.chmod(0o755)
            env['PATH'] = str(bindir) + os.pathsep + os.environ['PATH']
            result = subprocess.run(args, cwd='/', env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'not accepting this image', result.stderr)
            self.assertEqual(image.read_bytes(), b'keep original')
            self.assertEqual(checksum.read_text(), 'keep previous checksum')
            self.assertFalse((root / 'factory.img.partial').exists())

    def test_explicit_cleanup_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory(prefix='bkb rootfs ') as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta/etc').mkdir(parents=True)
            (root / 'var/www').mkdir(parents=True)
            removed = root / 'opt/scripta/etc/miner.conf'
            kept = root / 'var/www/keep.php'
            removed.write_text('superseded')
            kept.write_text('retain')
            subprocess.run(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), str(root)],
                           check=True, cwd='/')
            self.assertFalse(removed.exists())
            self.assertEqual(kept.read_text(), 'retain')

    def test_cleanup_refuses_non_rootfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ['bash', str(ROOT / 'scripts/prune-rootfs.sh'), tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'refusing cleanup', result.stderr)

    def test_wifi_starts_off_without_disabling_ethernet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'opt/scripta').mkdir(parents=True)
            state = root / 'var/lib/NetworkManager/NetworkManager.state'
            state.parent.mkdir(parents=True)
            state.write_text('[main]\nNetworkingEnabled=false\nWirelessEnabled=true\nWWANEnabled=false\n')
            profiles = root / 'etc/NetworkManager/system-connections'
            profiles.mkdir(parents=True)
            wifi = profiles / 'wireless'
            wifi.write_text('[connection]\ntype=802-11-wireless\n[802-11-wireless-security]\npsk=test-secret\n')
            wired = profiles / 'wired'
            wired.write_text('[connection]\ntype=802-3-ethernet\n')
            subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-image-network.py'), str(root)], check=True)
            self.assertFalse(wifi.exists())
            self.assertTrue(wired.exists())
            config = configparser.ConfigParser()
            config.read(state)
            self.assertFalse(config.getboolean('main', 'WirelessEnabled'))
            self.assertTrue(config.getboolean('main', 'NetworkingEnabled'))
            self.assertFalse(config.getboolean('main', 'WWANEnabled'))
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            state.unlink()
            state.symlink_to(root / 'do-not-touch')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-image-network.py'), str(root)], stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'do-not-touch').exists())

    def test_compression_names_and_options(self):
        with tempfile.TemporaryDirectory(prefix='bkb image ') as tmp:
            root = Path(tmp)
            bindir = root / 'bin'
            bindir.mkdir()
            xz = bindir / 'xz'
            xz.write_text('#!/bin/bash\nset -eu\n'
                          'printf "%s\\n" "$@" > "$XZ_ARGS"\n'
                          'image="${@: -1}"\n'
                          'test ! -e "$image.xz"\n'
                          'cp "$image" "$image.xz"\n')
            xz.chmod(0o755)
            image = root / 'Blakestream-GaintB-v2.2.img'
            image.write_bytes(b'test image, not flashable')
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'],
                       BKB_IMAGE_OUT=str(root), BKB_XZ_THREADS='2', BKB_XZ_PRESET='6',
                       XZ_ARGS=str(root / 'args'))
            args = ['bash', str(ROOT / 'scripts/06-compress.sh')]
            subprocess.run(args, cwd='/', env=env, check=True, stdout=subprocess.PIPE)
            self.assertEqual((root / 'args').read_text().splitlines(),
                             ['-T2', '-6', '-k', str(image)])
            expected = hashlib.sha256(image.read_bytes()).hexdigest()
            checksum = root / 'Blakestream-GaintB-v2.2.img.xz.sha256'
            self.assertEqual(checksum.read_text(),
                             expected + '  Blakestream-GaintB-v2.2.img.xz\n')
            again = subprocess.run(args, cwd='/', env=env, stdout=subprocess.PIPE)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(checksum.read_text(),
                             expected + '  Blakestream-GaintB-v2.2.img.xz\n')
            env['BKB_XZ_THREADS'] = '0'
            invalid = subprocess.run(args, cwd='/', env=env, stderr=subprocess.PIPE)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn(b'BKB_XZ_THREADS must be positive', invalid.stderr)

    def test_repack_contract(self):
        script = (ROOT / 'scripts/05-repack.sh').read_text()
        self.assertNotIn('--delete', script)
        self.assertIn('Blakestream-GaintB-v2.2.img', script)
        self.assertIn('PARTIAL_IMG="${OUT_IMG}.partial"', script)
        self.assertIn('scripts/prune-rootfs.sh', script)
        self.assertIn('clock-profiles.json', script)
        self.assertIn('scripts/prepare-image-wifi.py', script)
        self.assertIn('depmod -b "${ROOTFS_SRC}" 3.4.39', script)
        self.assertIn('command -v fsck.fat', script)
        repair = script.index('fsck.fat -a "${LOOP}p1" || fat_status=$?')
        check = script.index('fsck.fat -n "${LOOP}p1"')
        boot_mount = script.index('mount "${LOOP}p1" "${MNT_BOOT}"')
        self.assertLess(script.index('cp --reflink=auto'), repair)
        self.assertLess(repair, check)
        self.assertLess(check, boot_mount)
        self.assertIn('if (( fat_status > 1 )); then', script)
        self.assertGreater(script.rindex('fsck.fat -n "${LOOP}p1"'),
                           script.index('umount "${MNT_BOOT}"', boot_mount))


if __name__ == '__main__':
    unittest.main()
