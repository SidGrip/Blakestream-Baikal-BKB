#!/usr/bin/env python3
"""Independent backup integration and file-boundary regressions."""
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'overlay/opt/scripta/bin'))
spec = importlib.util.spec_from_file_location('backup', ROOT / 'overlay/opt/scripta/bin/backup-settings.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
from measured_rate_store import Store


class BackupReview(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bkb-backup-review-')
        self.layout = b.Layout(self.root)
        os.makedirs(self.layout.etc)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_partial_writes_are_completed(self):
        dest = os.path.join(self.layout.etc, 'scripta.conf')
        write = os.write
        with mock.patch.object(b.os, 'write', side_effect=lambda fd, value: write(fd, value[:3])):
            b._write_replace(dest, b'complete configuration', 0o664)
        self.assertEqual(Path(dest).read_bytes(), b'complete configuration')

    def test_web_hash_roundtrip_is_private_and_unchanged(self):
        fixture = (ROOT / 'overlay/opt/scripta/etc/uipasswd').read_bytes()
        dest = Path(self.layout.etc, 'uipasswd')
        dest.write_bytes(fixture)
        b.snapshot(self.layout, 'web-password', network_status=lambda: {'ok': True, 'interfaces': []})
        dest.write_bytes(b'replaced')
        b.restore(self.layout, 'web-password')
        self.assertEqual(dest.read_bytes(), fixture)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_current_settings_and_histories_roundtrip_without_touching_network(self):
        saved = {
            'userAutoFan': True, 'userAutoFanMin': 40, 'userAutoFanTarget': 36,
            'userAutoFanMidTemp': 43, 'userAutoFanMidFan': 75, 'userAutoFanFull': 50,
            'userAdjustableClocks': True, 'userDev': False, 'userDevLogTarget': 'SD',
            'userNetworkLogTarget': 'RAM', 'userSystemLogTarget': 'SD',
            'userCoolingLogTarget': 'RAM', 'userWebErrorLogTarget': 'SD',
            'userChartsSaveTo': 'SD', 'userPersistPoolHistory': True,
            'userPersistFailoverHistory': True, 'userPersistClockHistory': True,
        }
        expected = {}
        for name in b.CONFIG_NAMES:
            data = saved if name == 'scripta.conf' else {'fixture': name}
            expected['etc/' + name] = json.dumps(data).encode()
        expected['etc/uipasswd'] = (ROOT / 'overlay/opt/scripta/etc/uipasswd').read_bytes()
        for prefix, names in (('var/', b.HISTORY_NAMES), ('ram/', b.RAM_HISTORY_NAMES)):
            for name in names:
                expected[prefix + name] = json.dumps({'fixture': prefix + name}).encode()
        for archive_path, data in expected.items():
            dest = Path(b._dest_for_archive(self.layout, archive_path))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        connections = Path(self.root, 'etc/NetworkManager/system-connections')
        connections.mkdir(parents=True)
        wired = connections / 'ethernet'
        wireless = connections / 'wifi'
        wired.write_text('[connection]\ntype=802-3-ethernet\n[ipv4]\nmethod=auto\n')
        wireless.write_text('[connection]\ntype=802-11-wireless\n[wifi-security]\npsk=fixture-only\n')
        radio = Path(self.root, 'var/lib/blakestream-wifi/radio.json')
        radio.parent.mkdir(parents=True)
        radio.write_text('{"enabled": true, "source": "user"}')
        original_network = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (wired, wireless, radio)}
        network = {'ok': True, 'interfaces': [{'interface': 'eth0', 'mode': 'static',
            'address': '192.0.2.5', 'prefix': 24, 'gateway': '192.0.2.1',
            'dns': ['192.0.2.1'], 'saved': True}]}
        b.snapshot(self.layout, 'current', network_status=lambda: network)
        exported = b.export_bytes(self.layout, 'current')
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            self.assertFalse(any('NetworkManager' in name or 'wifi' in name.lower() for name in archive.namelist()))
            self.assertFalse(any(b'fixture-only' in archive.read(name) for name in archive.namelist()))
        b.import_bytes(self.layout, 'imported-current', exported)
        for archive_path in expected:
            Path(b._dest_for_archive(self.layout, archive_path)).write_bytes(b'changed')
        with mock.patch.object(b.subprocess, 'Popen', side_effect=AssertionError('restore must not run network commands')) as spawn:
            result = b.restore(self.layout, 'imported-current')
        spawn.assert_not_called()
        self.assertTrue(result['network_staged'])
        for archive_path, data in expected.items():
            self.assertEqual(Path(b._dest_for_archive(self.layout, archive_path)).read_bytes(), data)
        for p, (data, modified) in original_network.items():
            self.assertEqual(p.read_bytes(), data)
            self.assertEqual(p.stat().st_mtime_ns, modified)
        staged = json.loads(Path(self.layout.network_restore).read_text())
        self.assertTrue(staged['staged'])
        self.assertFalse(staged['applied'])
        self.assertEqual(staged['portable']['interfaces'][0]['address'], '192.0.2.5')

    def test_backup_reads_configured_network_instead_of_live_status(self):
        self.assertEqual(b.NETWORK_STATUS_ARGV, ('/opt/scripta/bin/network-settings.py', 'backup'))

    def test_restore_and_export_keep_web_group(self):
        dest = os.path.join(self.layout.etc, 'scripta.conf')
        Path(dest).write_bytes(b'old')
        for target in (dest, os.path.join(self.layout.etc, 'new.json')):
            owner = os.stat(target if os.path.exists(target) else self.layout.etc)
            with mock.patch.object(b.os, 'geteuid', return_value=0), mock.patch.object(b.os, 'fchown') as chown:
                b._write_replace(target, b'new', 0o664)
                self.assertEqual(chown.call_args.args[1:], (owner.st_uid, owner.st_gid))

    def test_legacy_zip_directories_and_wrapped_export(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('old-backup/', '')
            archive.writestr('old-backup/etc/', '')
            archive.writestr('old-backup/etc/scripta.conf', '{}')
        result = b.import_bytes(self.layout, 'legacy', buffer.getvalue())
        self.assertEqual(result['format'], 'legacy')
        b.restore(self.layout, 'legacy')
        self.assertEqual(Path(self.layout.etc, 'scripta.conf').read_bytes(), b'{}')

    def test_prepare_does_not_delete_inflight_staging(self):
        b._prepare(self.layout)
        os.makedirs(self.layout.incoming)
        marker = Path(self.layout.incoming, 'marker')
        marker.write_text('active writer')
        b._prepare(self.layout)
        self.assertTrue(marker.exists())

    def test_reject_linked_ancestor(self):
        real = Path(self.root, 'real')
        (real / 'nested').mkdir(parents=True)
        linked = Path(self.root, 'linked')
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(b.BackupError):
            b._write_replace(str(linked / 'nested/file'), b'no', 0o664)
        self.assertFalse((real / 'nested/file').exists())

    def test_full_roundtrip_pending_without_runtime_replay(self):
        Path(self.layout.etc, 'scripta.conf').write_text('{"userChartsSaveTo":"RAM"}')
        store = Store(self.layout.ram, self.layout.var)
        store.set_target('SD', now=999)
        def interval(meta, runtime):
            runtime['stale_counter'] = 99999
            context = {'board':0, 'installation':meta['board_ids']['0'], 'profile':30}
            ident = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
            return [{'context_id':ident, 'context':context, 'end':self.at, 'seconds':60,
                     'difficulty':960, 'accepted':30, 'rejected':0, 'hw':0,
                     'temp_sum':2400, 'temp_min':39, 'temp_max':41, 'fan_sum':4800, 'fixed32':True}]
        self.at = 1000
        store.tick(interval, self.at, force=True)
        self.at = 1060
        before = store.tick(interval, self.at)
        self.assertTrue(before['unsaved'])
        status = lambda: {'ok':True, 'interfaces':[{'interface':'eth0','mode':'dhcp','address':'192.0.2.10',
                            'prefix':24,'gateway':'192.0.2.1','dns':['192.0.2.1'],'saved':True}]}
        control = Path(self.layout.var, 'measured-rates-control.json').read_bytes()
        b.snapshot(self.layout, 'original', network_status=status)
        blob = b.export_bytes(self.layout, 'original')
        other = b.Layout(os.path.join(self.root, 'other'))
        os.makedirs(other.etc)
        b.import_bytes(other, 'imported', blob)
        restored = b.restore(other, 'imported')
        self.assertTrue(restored['network_staged'])
        self.assertEqual(json.loads(control), json.loads(Path(other.var, 'measured-rates-control.json').read_bytes()))
        states = list(Path(other.ram).glob('measured-rates-state-*.json'))
        self.assertEqual(json.loads(states[0].read_text())['runtime'], {})
        self.assertFalse(Path(other.ram, 'accepted-work-rates.json').exists())
        restored_store = Store(other.ram, other.var)
        result = restored_store.tick(lambda meta,runtime: [], 1061, force=True)
        self.assertEqual(result['contexts'][0]['accepted'], 60)
        again = restored_store.tick(lambda meta,runtime: [], 1062, force=True)
        self.assertEqual(again['contexts'][0]['accepted'], 60, 'pending intervals must not replay twice')
        self.assertEqual(Path(other.etc, 'scripta.conf').read_text(), '{"userChartsSaveTo":"RAM"}')

    @unittest.skipUnless(os.geteuid() == 0, 'root ownership case runs in the build-server container')
    def test_actual_root_export_group(self):
        b._prepare(self.layout)
        os.chown(self.layout.ram, 0, 33)
        path = Path(self.layout.ram, 'backup-export.zip')
        b._write_replace(str(path), b'archive', 0o640)
        self.assertEqual(path.stat().st_gid, 33)

    def snapshot_config(self):
        Path(self.layout.etc, 'scripta.conf').write_text('{"value":"original"}')
        b.snapshot(self.layout, 'saved', network_status=lambda: {'ok':True,'interfaces':[]})
        Path(self.layout.etc, 'scripta.conf').write_text('{"value":"current"}')

    def test_checksum_failure_changes_nothing(self):
        self.snapshot_config()
        Path(self.layout.backup_root, 'saved/etc/scripta.conf').write_text('tampered')
        with self.assertRaises(b.BackupError):
            b.restore(self.layout, 'saved')
        self.assertIn('current', Path(self.layout.etc, 'scripta.conf').read_text())

    def test_failed_restore_rolls_back_and_interrupt_is_recoverable(self):
        self.snapshot_config()
        def fail(step):
            if step == 'restore-wrote':
                raise OSError('fixture write failure')
        with mock.patch.object(b, '_fault_hook', fail):
            with self.assertRaises(b.BackupError):
                b.restore(self.layout, 'saved')
        self.assertIn('current', Path(self.layout.etc, 'scripta.conf').read_text())
        def interrupt(step):
            if step == 'restore-wrote':
                raise b._LeaveJournal()
        with mock.patch.object(b, '_fault_hook', interrupt):
            with self.assertRaises(b._LeaveJournal):
                b.restore(self.layout, 'saved')
        self.assertTrue(Path(self.layout.rollback_dir).is_dir())
        b.list_backups(self.layout)
        self.assertIn('current', Path(self.layout.etc, 'scripta.conf').read_text())
        self.assertFalse(Path(self.layout.rollback_dir).exists())

    def test_archive_boundaries_and_manage(self):
        for names in (['../escape'], ['etc/scripta.conf', 'etc/scripta.conf'], ['/etc/scripta.conf'], ['etc/../../escape']):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name in names:
                    archive.writestr(name, '{}')
            with self.assertRaises(b.BackupError):
                b.import_bytes(self.layout, 'bad', buffer.getvalue())
        self.snapshot_config()
        b.manage_backup(self.layout, 'rename', 'saved', 'renamed')
        self.assertEqual(b.list_backups(self.layout)['backups'][0]['dir'], 'renamed')
        b.manage_backup(self.layout, 'delete', 'renamed')
        self.assertEqual(b.list_backups(self.layout)['backups'], [])

    def test_interrupted_temp_does_not_block_recovery(self):
        path = str(Path(self.layout.etc, 'scripta.conf'))
        Path(path + '.bktmp').write_text('interrupted')
        b._write_replace(path, b'complete', 0o664)
        self.assertEqual(Path(path).read_bytes(), b'complete')

    def test_history_larger_than_database_limit_roundtrips(self):
        b._prepare(self.layout)
        history = b'[' + b'0,' * (2 * 1024 * 1024) + b'0]'
        Path(self.layout.ram, 'clock-history.json').write_bytes(history)
        b.snapshot(self.layout, 'large-history', network_status=lambda: {'ok':True,'interfaces':[]})
        archive = b.export_bytes(self.layout, 'large-history')
        b.import_bytes(self.layout, 'large-import', archive)
        files, fmt = b._load_backup(self.layout, 'large-import')
        self.assertEqual(files['ram/clock-history.json'], history)
        self.assertEqual(fmt, 'v2')

    def test_collection_rejects_over_budget_before_loading_next_file(self):
        Path(self.layout.etc, 'scripta.conf').write_bytes(b'x' * 10)
        Path(self.layout.etc, 'miner.options.json').write_bytes(b'x' * 10)
        with self.assertRaises(b.BackupError):
            b._collect_exact(self.layout.etc, ('scripta.conf', 'miner.options.json'), 'etc/', budget=15)


if __name__ == '__main__':
    unittest.main(verbosity=2)
