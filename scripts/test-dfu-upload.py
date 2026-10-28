#!/usr/bin/env python3
"""Check legacy DFU upload behavior without USB devices or elevated privileges."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / 'overlay/opt/scripta/firmware/dfu_flash.py').read_text()
UPLOAD = SOURCE[SOURCE.index('def dfu_upload(out):'):SOURCE.index('\ndef dfu_download(img):')]


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dfu upload ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'backup.bin'
        self.out.write_bytes(b'previous verified backup')
        self.payload = b'\xa5' * 0x100000
        self.calls = []
        self.rc = 0
        self.kind = 'regular'
        self.allowed = True
        self.namespace = {
            'os': os, 'stat': stat, 'tempfile': tempfile,
            'subprocess': SimpleNamespace(call=self.upload, STDOUT=subprocess.STDOUT),
            'ensure_dir_for': lambda path: None,
            'secure_output_parent': lambda path: self.allowed,
            'DFU': '0483:df11', 'FLASH_ADDR': '0x08000000',
            'FLASH_LEN': '0x100000', 'FLASH_SIZE': 0x100000,
        }
        exec(compile(UPLOAD, '<dfu_upload>', 'exec'), self.namespace)

    def upload(self, args, **kwargs):
        self.calls.append(args)
        self.assertEqual(args[:5], ['dfu-util', '-a', '0', '-d', '0483:df11'])
        self.assertEqual(args[-2:], ['-s', '0x08000000:0x100000'])
        target = Path(args[args.index('-U') + 1])
        self.assertFalse(target.exists() or target.is_symlink())
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        if self.kind == 'symlink':
            target.symlink_to(self.protected)
        elif self.kind == 'hardlink':
            os.link(self.protected, target)
        else:
            # dfu-util 0.8 opens uploads exclusively and rejects existing files.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(self.payload)
        return self.rc

    def run_upload(self):
        return self.namespace['dfu_upload'](str(self.out))

    def assert_old_backup(self):
        self.assertEqual(self.out.read_bytes(), b'previous verified backup')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['backup.bin'])

    def test_exclusive_upload_replaces_existing_output_only_after_complete_read(self):
        self.assertTrue(self.run_upload())
        self.assertEqual(self.out.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o600)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(list(self.root.iterdir()), [self.out])

    def test_failed_transfer_keeps_previous_output_and_removes_staging(self):
        self.payload = b'partial'
        self.rc = 74
        self.assertFalse(self.run_upload())
        self.assert_old_backup()

    def test_short_successful_transfer_is_rejected(self):
        self.payload = b'partial'
        self.assertFalse(self.run_upload())
        self.assert_old_backup()

    def test_unsafe_parent_is_rejected_before_transfer(self):
        self.allowed = False
        self.assertFalse(self.run_upload())
        self.assertEqual(self.calls, [])
        self.assert_old_backup()

    def test_symlink_destination_is_rejected_before_transfer(self):
        self.out.unlink()
        self.out.symlink_to(self.root / 'missing')
        self.assertFalse(self.run_upload())
        self.assertEqual(self.calls, [])
        self.assertTrue(self.out.is_symlink())

    def test_linked_upload_cannot_replace_output_or_change_another_file(self):
        self.protected = self.root / 'protected.bin'
        self.protected.write_bytes(self.payload)
        self.protected.chmod(0o640)
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                self.kind = kind
                self.assertFalse(self.run_upload())
                self.assertEqual(self.out.read_bytes(), b'previous verified backup')
                self.assertEqual(self.protected.read_bytes(), self.payload)
                self.assertEqual(stat.S_IMODE(self.protected.stat().st_mode), 0o640)
                self.assertEqual(self.protected.stat().st_nlink, 1)
                self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                                 ['backup.bin', 'protected.bin'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
