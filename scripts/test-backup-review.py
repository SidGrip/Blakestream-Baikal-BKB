#!/usr/bin/env python3
"""Independent backup integration and file-boundary regressions."""
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
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
        b._fault_hook = None
        b._termination_requested = False
        b._termination_shield_depth = 0
        shutil.rmtree(self.root)

    def test_partial_writes_are_completed(self):
        dest = os.path.join(self.layout.etc, 'scripta.conf')
        write = os.write
        with mock.patch.object(b.os, 'write', side_effect=lambda fd, value: write(fd, value[:3])):
            b._write_replace(dest, b'complete configuration', 0o664)
        self.assertEqual(Path(dest).read_bytes(), b'complete configuration')

    def test_transactional_deletions_and_empty_directory_pruning_are_fsynced(self):
        target = Path(self.layout.etc, 'delete-me.json')
        target.write_text('temporary')
        with mock.patch.object(b.os, 'fsync', wraps=os.fsync) as synced:
            b._unlink_file(str(target))
        self.assertFalse(target.exists())
        self.assertGreaterEqual(synced.call_count, 1)

        nested = Path(self.layout.baselines, 'profile', 'board')
        nested.mkdir(parents=True)
        with mock.patch.object(b.os, 'fsync', wraps=os.fsync) as synced:
            b._prune_empty(str(nested), self.layout.baselines)
        self.assertFalse(Path(self.layout.baselines, 'profile').exists())
        self.assertGreaterEqual(synced.call_count, 2)

    def test_new_restore_directory_chain_is_durably_published(self):
        nested = Path(self.layout.baselines, 'campaign', 'algorithm')
        with mock.patch.object(b.os, 'fsync', wraps=os.fsync) as synced:
            b._mkdir(str(nested), 0o700)
        self.assertTrue(nested.is_dir())
        # Three new directories, each syncing itself and its parent.
        self.assertGreaterEqual(synced.call_count, 6)

    def test_snapshot_publish_and_manage_namespace_changes_are_fsynced(self):
        Path(self.layout.etc, 'scripta.conf').write_text('{}')
        with mock.patch.object(b, '_fsync_directory', wraps=b._fsync_directory) as synced:
            b.snapshot(self.layout, 'saved', network_status=lambda: {
                'ok': True, 'interfaces': []})
        synced_paths = [os.path.abspath(call.args[0]) for call in synced.call_args_list]
        self.assertIn(os.path.abspath(os.path.join(self.layout.backup_root, 'saved')),
                      synced_paths)
        self.assertIn(os.path.abspath(self.layout.backup_root), synced_paths)

        with mock.patch.object(b, '_fsync_directory', wraps=b._fsync_directory) as synced:
            b.manage_backup(self.layout, 'rename', 'saved', 'renamed')
        self.assertIn(os.path.abspath(self.layout.backup_root),
                      [os.path.abspath(call.args[0]) for call in synced.call_args_list])

        with mock.patch.object(b, '_fsync_directory', wraps=b._fsync_directory) as synced:
            b.manage_backup(self.layout, 'delete', 'renamed')
        self.assertGreaterEqual(
            [os.path.abspath(call.args[0]) for call in synced.call_args_list].count(
                os.path.abspath(self.layout.backup_root)), 2)

    def test_interrupted_backup_delete_tombstone_is_hidden_and_recovered(self):
        Path(self.layout.etc, 'scripta.conf').write_text('{}')
        b.snapshot(self.layout, 'saved', network_status=lambda: {
            'ok': True, 'interfaces': []})

        def interrupt(step):
            if step == 'backup-delete-retired':
                raise b._LeaveJournal()

        with mock.patch.object(b, '_fault_hook', interrupt):
            with self.assertRaises(b._LeaveJournal):
                b.manage_backup(self.layout, 'delete', 'saved')
        self.assertFalse(Path(self.layout.backup_root, 'saved').exists())
        self.assertTrue(Path(self.layout.delete_done).is_dir())
        self.assertEqual(b.list_backups(self.layout)['backups'], [])
        self.assertFalse(Path(self.layout.delete_done).exists())

    def test_rollback_of_new_file_uses_durable_unlink(self):
        b._prepare(self.layout)
        target = Path(self.layout.etc, 'scripta.conf')
        target.write_text('new transaction value')
        Path(self.layout.rollback_dir).mkdir(mode=0o700)
        journal = {'v': 1, 'entries': [
            {'key': 'etc/scripta.conf', 'existed': False},
        ]}
        b._write_replace(os.path.join(self.layout.rollback_dir, 'journal.json'),
                         b._json_bytes(journal), 0o600)
        with mock.patch.object(b, '_unlink_file', wraps=b._unlink_file) as unlinked:
            b.recover_rollback(self.layout)
        self.assertEqual(
            [call.args[0] for call in unlinked.call_args_list].count(str(target)), 1)
        self.assertFalse(target.exists())
        self.assertFalse(Path(self.layout.rollback_dir).exists())

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

    def test_reject_linked_target_without_touching_referent(self):
        protected = Path(self.root, 'protected')
        protected.write_bytes(b'unchanged')
        target = Path(self.layout.etc, 'scripta.conf')
        target.symlink_to(protected)
        with self.assertRaises(b.BackupError):
            b._write_replace(str(target), b'no', 0o664, owner=(33, 33))
        self.assertEqual(protected.read_bytes(), b'unchanged')
        self.assertTrue(target.is_symlink())

    def test_reject_hardlinked_source_without_exporting_referent(self):
        protected = Path(self.root, 'protected-hardlink')
        protected.write_bytes(b'private bytes')
        target = Path(self.layout.etc, 'scripta.conf')
        os.link(str(protected), str(target))
        with self.assertRaises(b.BackupError) as raised:
            b._read_nofollow(str(target), b.MAX_FILE_BYTES)
        self.assertEqual(raised.exception.code, 'io')
        with self.assertRaises(b.BackupError):
            b.snapshot(self.layout, 'linked', network_status=lambda: {
                'ok': True, 'interfaces': []})
        self.assertEqual(protected.read_bytes(), b'private bytes')
        self.assertFalse(Path(self.layout.backup_root, 'linked').exists())

    def test_replaced_random_temp_is_not_renamed_or_followed(self):
        protected = Path(self.root, 'protected-temp')
        protected.write_bytes(b'unchanged')
        real_mkstemp = tempfile.mkstemp
        planted = []
        def replace_temp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            os.unlink(path)
            os.symlink(str(protected), path)
            planted.append(path)
            return fd, path
        target = Path(self.layout.etc, 'scripta.conf')
        with mock.patch.object(b.tempfile, 'mkstemp', side_effect=replace_temp):
            with self.assertRaises(b.BackupError):
                b._write_replace(str(target), b'no', 0o664, owner=(33, 33))
        self.assertFalse(target.exists())
        self.assertEqual(protected.read_bytes(), b'unchanged')
        self.assertTrue(os.path.islink(planted[0]))
        os.unlink(planted[0])

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

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to verify export-group ownership')
    def test_actual_root_export_group(self):
        b._prepare(self.layout)
        os.chown(self.layout.private_ram, 0, 33)
        path = Path(self.layout.export_zip)
        b._write_replace(str(path), b'archive', 0o640)
        self.assertEqual(path.stat().st_gid, 33)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to verify atomic ownership changes')
    def test_atomic_replace_finalizes_metadata_before_publish(self):
        target = Path(self.layout.etc, 'scripta.conf')
        rename = os.rename
        observed = []
        def inspect(source, destination):
            info = os.stat(source)
            observed.append((info.st_uid, info.st_gid, info.st_mode & 0o777))
            return rename(source, destination)
        with mock.patch.object(b.os, 'rename', side_effect=inspect):
            b._write_replace(str(target), b'published', 0o640, owner=(33, 33))
        self.assertEqual(observed, [(33, 33, 0o640)])
        info = target.stat()
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777),
                         (33, 33, 0o640))

    def test_main_never_changes_live_web_directory_metadata(self):
        for path in (self.layout.var, self.layout.ram):
            os.makedirs(path, exist_ok=True)
        expected = []
        for path in (self.layout.etc, self.layout.var, self.layout.ram):
            os.chmod(path, 0o775)
            info = os.stat(path)
            expected.append((info.st_uid, info.st_gid, info.st_mode & 0o777))
        observed = []
        def inspect(_layout):
            observed.extend((os.stat(path).st_uid, os.stat(path).st_gid,
                             os.stat(path).st_mode & 0o777)
                            for path in (self.layout.etc, self.layout.var, self.layout.ram))
            return {'ok': True, 'backups': []}
        output = io.StringIO()
        with mock.patch.object(b, 'list_backups', side_effect=inspect):
            self.assertEqual(b.main(['backup-settings.py', 'list'], io.StringIO('{}'), output,
                                    layout=self.layout), 0)
        self.assertEqual(observed, expected)
        self.assertEqual([(os.stat(path).st_uid, os.stat(path).st_gid,
                           os.stat(path).st_mode & 0o777)
                          for path in (self.layout.etc, self.layout.var, self.layout.ram)], expected)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to verify restored ownership')
    def test_restore_absent_web_target_gets_canonical_owner(self):
        target = Path(self.layout.etc, 'scripta.conf')
        target.write_text('{"saved":true}')
        os.chown(str(target), 33, 33)
        b.snapshot(self.layout, 'owner', network_status=lambda: {'ok': True, 'interfaces': []})
        target.unlink()
        b.restore(self.layout, 'owner')
        info = target.stat()
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777),
                         (33, 33, 0o664))

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

    def test_sigterm_mid_apply_immediately_rolls_back_and_shields_recovery(self):
        self.snapshot_config()
        target = Path(self.layout.etc, 'scripta.conf')
        sent = {'first': False, 'second': False}
        def terminate(step):
            if step == 'restore-wrote' and not sent['first']:
                sent['first'] = True
                os.kill(os.getpid(), signal.SIGTERM)
        write_replace = b._write_replace
        def terminate_again(*args, **kwargs):
            if b._termination_shield_depth and not sent['second']:
                sent['second'] = True
                os.kill(os.getpid(), signal.SIGTERM)
            return write_replace(*args, **kwargs)
        output = io.StringIO()
        with mock.patch.object(b, '_fault_hook', terminate), \
                mock.patch.object(b, '_write_replace', side_effect=terminate_again):
            rc = b.main(['backup-settings.py', 'restore'],
                        io.StringIO('{"name":"saved"}'), output, layout=self.layout)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(output.getvalue())['error'], 'busy')
        self.assertTrue(sent['first'])
        self.assertTrue(sent['second'])
        self.assertIn('current', target.read_text())
        self.assertFalse(Path(self.layout.rollback_dir).exists())

    def test_sigterm_during_journal_retirement_cannot_strand_partial_recovery(self):
        self.snapshot_config()
        target = Path(self.layout.etc, 'scripta.conf')
        sent = {'term': False}

        def terminate_after_atomic_retire(step):
            if step == 'restore-journal-retired' and not sent['term']:
                sent['term'] = True
                os.kill(os.getpid(), signal.SIGTERM)

        output = io.StringIO()
        with mock.patch.object(b, '_fault_hook', terminate_after_atomic_retire):
            rc = b.main(['backup-settings.py', 'restore'],
                        io.StringIO('{"name":"saved"}'), output, layout=self.layout)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(output.getvalue())['error'], 'busy')
        self.assertTrue(sent['term'])
        # The restore was already committed before TERM. Neither a replayable
        # journal nor a partially removed tombstone may remain.
        self.assertIn('original', target.read_text())
        self.assertFalse(Path(self.layout.rollback_dir).exists())
        self.assertFalse(Path(self.layout.rollback_done).exists())

    def test_power_loss_during_committed_tombstone_cleanup_never_replays_old_state(self):
        self.snapshot_config()
        target = Path(self.layout.etc, 'scripta.conf')
        discard = b._discard_tree

        def interrupt_tombstone_cleanup(path, parent):
            if os.path.abspath(path) == os.path.abspath(self.layout.rollback_done):
                raise b._LeaveJournal()
            return discard(path, parent)

        with mock.patch.object(b, '_discard_tree', side_effect=interrupt_tombstone_cleanup):
            with self.assertRaises(b._LeaveJournal):
                b.restore(self.layout, 'saved')
        self.assertIn('original', target.read_text())
        self.assertFalse(Path(self.layout.rollback_dir).exists())
        self.assertTrue(Path(self.layout.rollback_done).is_dir())

        output = io.StringIO()
        self.assertEqual(b.main(['backup-settings.py', 'recover'], io.StringIO('{}'), output,
                                layout=self.layout), 0)
        self.assertFalse(json.loads(output.getvalue())['recovered'])
        self.assertIn('original', target.read_text())
        self.assertFalse(Path(self.layout.rollback_done).exists())

    def test_recover_verb_replays_journal_left_for_boot(self):
        self.snapshot_config()
        target = Path(self.layout.etc, 'scripta.conf')
        def interrupt(step):
            if step == 'restore-wrote':
                raise b._LeaveJournal()
        with mock.patch.object(b, '_fault_hook', interrupt):
            with self.assertRaises(b._LeaveJournal):
                b.restore(self.layout, 'saved')
        self.assertTrue(Path(self.layout.rollback_dir).is_dir())
        output = io.StringIO()
        self.assertEqual(b.main(['backup-settings.py', 'recover'], io.StringIO('{}'), output,
                                layout=self.layout), 0)
        self.assertTrue(json.loads(output.getvalue())['recovered'])
        self.assertIn('current', target.read_text())
        self.assertFalse(Path(self.layout.rollback_dir).exists())
        repair = (ROOT / 'overlay/opt/scripta/bin/repair-system-perms.sh').read_text()
        self.assertIn('backup-settings.py recover', repair)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to verify rollback ownership')
    def test_failed_restore_preserves_captured_owner_and_mode(self):
        self.snapshot_config()
        target = Path(self.layout.etc, 'scripta.conf')
        os.chown(str(target), 0, 0)
        os.chmod(str(target), 0o640)
        def fail(step):
            if step == 'restore-wrote':
                raise OSError('fixture write failure')
        with mock.patch.object(b, '_fault_hook', fail):
            with self.assertRaises(b.BackupError):
                b.restore(self.layout, 'saved')
        info = target.stat()
        self.assertIn('current', target.read_text())
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777),
                         (0, 0, 0o640))

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
        self.assertEqual(Path(path + '.bktmp').read_text(), 'interrupted')
        self.assertEqual(list(Path(self.layout.etc).glob('.scripta.conf.bktmp.*')), [])

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
