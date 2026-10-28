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
    def test_webroot_validator_rejects_hardlinks_symlinks_and_special_files(self):
        validator = ROOT / 'scripts/validate-webroot-tree.sh'
        with tempfile.TemporaryDirectory(prefix='bkb webroot ') as tmp:
            base = Path(tmp)
            web = base / 'www'
            web.mkdir()
            (web / 'index.php').write_text('safe')
            subprocess.run(['bash', str(validator), str(web)], check=True)

            protected = base / 'protected'
            protected.write_text('must not be chmodded')
            before = protected.stat().st_mode & 0o777
            os.link(protected, web / 'linked.php')
            result = subprocess.run(['bash', str(validator), str(web)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'hardlinked file', result.stderr)
            self.assertEqual(protected.stat().st_mode & 0o777, before)
            (web / 'linked.php').unlink()

            (web / 'linked.php').symlink_to(protected)
            result = subprocess.run(['bash', str(validator), str(web)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'symlink entry', result.stderr)
            (web / 'linked.php').unlink()

            os.mkfifo(web / 'unexpected.pipe')
            result = subprocess.run(['bash', str(validator), str(web)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'special entry', result.stderr)

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
            forbidden = [root / ('var/www/' + name) for name in
                         ('miner.php', 'info.php', 'test.php', 'index.html',
                          'index.lighttpd.html', 'lighttpd.pem',
                          'f_hostHardCtl.php', 'f_minerHardCtl.php', 'f_minercompat.php')]
            forbidden.append(root / 'var/www/inc/host.inc.php')
            terminal = root / 'var/www/partials/terminal.html'
            terminal.parent.mkdir(parents=True)
            terminal.write_text('supported terminal')
            forbidden.append(root / 'var/www/css/shellinabox.css')
            forbidden.append(root / 'etc/lighttpd/certs/lighttpd.pem')
            forbidden.append(root / 'var/lib/shellinabox/certificate.pem')
            forbidden.append(root / 'var/mail/root')
            forbidden.append(root / 'var/mail/www-data')
            for path in forbidden:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('forbidden inherited surface')
            for runlevel in ('2', '3', '4', '5'):
                link = root / ('etc/rc' + runlevel + '.d/S01shellinabox')
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to('../init.d/shellinabox')
                forbidden.append(link)
            cache = root / 'opt/scripta/bin/__pycache__'
            cache.mkdir(parents=True)
            bytecode = cache / 'host-build.cpython-312.pyc'
            bytecode.write_bytes(b'host bytecode')
            forbidden.append(bytecode)
            subprocess.run(['bash', str(ROOT / 'scripts/prune-rootfs.sh'), str(root)],
                           check=True, cwd='/')
            self.assertFalse(removed.exists())
            for path in forbidden:
                self.assertFalse(path.exists() or path.is_symlink(), str(path))
            self.assertFalse(cache.exists(), str(cache))
            self.assertEqual(kept.read_text(), 'retain')
            self.assertEqual(terminal.read_text(), 'supported terminal')

    def test_security_policy_and_tls_material_are_not_web_writable_or_shipped(self):
        policy = ROOT / 'overlay/opt/scripta/policy/miner-options.json'
        parsed = __import__('json').loads(policy.read_text())
        self.assertEqual(parsed['version'], 1)
        self.assertIn('api-allow', parsed['fixed_values'])
        self.assertEqual(parsed['fixed_values']['api-allow'], 'B:127.0.0.1')
        self.assertEqual(parsed['fixed_values']['api-groups'],
                         'B:*:restart:ascidentify:ascenable:ascdisable:bkbtrace:bkbfan:ascpool:ascfailover')
        group_verbs = set(parsed['fixed_values']['api-groups'].split(':')[2:])
        self.assertTrue({'restart', 'ascidentify', 'ascenable', 'ascdisable',
                         'bkbtrace', 'bkbfan', 'ascpool', 'ascfailover'} <= group_verbs)
        self.assertTrue({'save', 'addpool', 'removepool', 'quit', 'setconfig',
                         'ascset'}.isdisjoint(group_verbs))
        self.assertNotIn('monitor', parsed['allowed_keys'])
        repair = (ROOT / 'overlay/opt/scripta/bin/repair-system-perms.sh').read_text()
        self.assertIn('set_owner_mode /opt/scripta/policy/miner-options.json root:root 644', repair)
        self.assertIn('/bin/sh /opt/scripta/bin/prepare-runtime-dir.sh', repair)
        self.assertIn('cmp -s "$tmp" "$file"', repair)
        self.assertNotIn('ensure_blakestream_sudoers || true', repair)
        self.assertIn('install_sudo_policy /etc/sudoers.d/blakestream-backup', repair)
        self.assertIn('unsafe webroot: ownership, mode, type, or link count drift', repair)
        self.assertIn('-links +1', repair)
        self.assertNotIn('find -P /var/www -xdev -type f -links +1 -delete', repair)
        self.assertNotIn('chown -hR root:root /var/www', repair)
        self.assertNotIn('-type f -links 1 -exec chown root:root', repair)
        prepare_runtime = (ROOT / 'overlay/opt/scripta/bin/prepare-runtime-dir.sh').read_text()
        self.assertIn('miner_runtime_dir=/run/blakestream-miner', prepare_runtime)
        self.assertIn('prepare_sealed_dir "$miner_runtime_dir" root:www-data 0750', prepare_runtime)
        self.assertIn('prepare_sealed_dir "$firmware_runtime_dir" root:www-data 0750', prepare_runtime)
        self.assertIn('bkb-firmware-start.lock" root:root 0600', prepare_runtime)
        self.assertIn('firmware-recovery-required" 0600', prepare_runtime)
        self.assertIn('validate_critical_root_file "$firstboot_state_dir/firmware-recovery-required" 0600', prepare_runtime)
        self.assertNotIn('sanitize_root_file "$firstboot_state_dir/firmware-recovery-required"', prepare_runtime)
        self.assertIn("stat -c '%h'", prepare_runtime)
        firmware = (ROOT / 'overlay/opt/scripta/bin/bkb-firmware.sh').read_text()
        self.assertIn('flock -n 9', firmware)
        self.assertLess(firmware.index('flock -n 9'), firmware.index('if fw_active; then'))
        self.assertIn('run-job "$op" 9>&-', firmware)
        self.assertLess(firmware.index('rm -f -- "$PROG"'), firmware.index(': > "$PROG"'))
        self.assertIn('fw_mark_recovery_required "$op"', firmware)
        self.assertIn('fw_job_recovery_hold', firmware)
        self.assertIn('BKB_RECOVERY_REQUIRED="$was_recovery"', firmware)
        self.assertIn('fw_fsync_file "$recovery_tmp"', firmware)
        self.assertIn('fw_fsync_dir "$RECOVERY_DIR"', firmware)
        self.assertNotIn('\n  revert)', firmware[firmware.index('\ncase "${1:-}" in'):])
        self.assertFalse((ROOT / 'firmware-custom/flash/dfu_flash.py').exists())
        manual_flash = (ROOT / 'firmware-custom/flash/bkb-flash.sh').read_text()
        self.assertIn('wait_job', manual_flash)
        self.assertIn('/opt/scripta/bin/bkb-firmware.sh', manual_flash)
        self.assertIn('/run/blakestream-firmware', manual_flash)
        self.assertNotIn('/tmp/', manual_flash)
        self.assertNotIn('bkb-firmware-identity.json', manual_flash)
        self.assertIn('trap cleanup_remote_backup EXIT', manual_flash)
        self.assertIn('guarded_read backup stock "$remote_backup" || backup_rc=$?', manual_flash)
        self.assertIn('"root@${MINER}:${remote_backup}" "$local_backup"', manual_flash)
        self.assertLess(manual_flash.index('guarded_read backup stock "$remote_backup"'),
                        manual_flash.index('"root@${MINER}:${remote_backup}" "$local_backup"'))
        sudoers = (ROOT / 'overlay/etc/sudoers.d/blakestream-www').read_text()
        self.assertIn('/opt/scripta/startup/miner-restart.sh --preflight', sudoers)
        launcher = (ROOT / 'overlay/opt/scripta/startup/miner-start.sh').read_text()
        self.assertIn('RUNTIME_DIR=/run/blakestream-miner', launcher)
        self.assertIn('mktemp "$RUNTIME_DIR/.build-miner-runtime.err.XXXXXX"', launcher)
        self.assertIn('mktemp "$RUNTIME_DIR/.miner-api-hang.count.XXXXXX"', launcher)
        self.assertIn('FW_RECOVERY_REQUIRED=/var/lib/blakestream-firstboot/firmware-recovery-required', launcher)
        self.assertIn('firmware recovery required; bridge is held in DFU', launcher)
        recovery_hold = (ROOT / 'overlay/opt/scripta/bin/enforce-firmware-recovery-hold.sh').read_text()
        self.assertIn('firmware-recovery-required', recovery_hold)
        self.assertIn('exec "$PY" -u "$DFU" forcedfu', recovery_hold)
        recovery_unit = (ROOT / 'overlay/etc/systemd/system/blakestream-firmware-recovery.service').read_text()
        firstboot_unit = (ROOT / 'overlay/etc/systemd/system/blakestream-firmware-firstboot.service').read_text()
        cron_recovery = (ROOT / 'overlay/etc/systemd/system/cron.service.d/blakestream-firmware-recovery.conf').read_text()
        self.assertIn('Before=cron.service', recovery_unit)
        self.assertIn('Requires=blakestream-system-perms.service blakestream-firmware-recovery.service', firstboot_unit)
        self.assertIn('Requires=blakestream-system-perms.service blakestream-firmware-recovery.service', cron_recovery)
        self.assertNotIn('RUNTIME_CONF=/tmp/', launcher)
        repack = (ROOT / 'scripts/05-repack.sh').read_text()
        self.assertIn('validate-webroot-tree.sh" "${ROOTFS_SRC}/var/www"', repack)
        self.assertIn('-type f -links 1 -exec chmod 0644', repack)
        self.assertNotIn('chown -hR 0:0 "${ROOTFS_SRC}/var/www"', repack)
        self.assertNotIn('chown -hR 33:33 "${ROOTFS_SRC}/var/www"', repack)
        apply_overlay = (ROOT / 'scripts/04-apply-overlay.sh').read_text()
        self.assertIn("--exclude='__pycache__/'", apply_overlay)
        lighttpd = (ROOT / 'overlay/etc/lighttpd/lighttpd.conf').read_text()
        self.assertIn('url.access-deny             = ( "~", ".inc", ".pem", ".key" )', lighttpd)
        self.assertNotIn('BEGIN PRIVATE KEY', lighttpd)
        self.assertIn('0.0.0.0:443', lighttpd)
        self.assertIn('/var/empty/blakestream-http', lighttpd)
        self.assertIn('index-file.names            = ( "index.php" )', lighttpd)
        self.assertNotIn('"index.html"', lighttpd)
        self.assertNotIn(':HIGH:', lighttpd)
        tls_script = (ROOT / 'overlay/opt/scripta/bin/ensure-web-tls.sh').read_text()
        self.assertIn('-startdate 20200101000000Z -enddate 20491231235959Z', tls_script)
        self.assertIn('subjectAltName = DNS:$common_name', tls_script)
        self.assertIn('[ "$cert_pub" != "$key_pub" ]', tls_script)
        tls_unit = (ROOT / 'overlay/etc/systemd/system/blakestream-web-tls.service').read_text()
        lighttpd_dropin = (ROOT / 'overlay/etc/systemd/system/lighttpd.service.d/blakestream-runtime.conf').read_text()
        self.assertIn('Before=lighttpd.service', tls_unit)
        self.assertIn('Requires=blakestream-web-tls.service', lighttpd_dropin)
        self.assertIn('After=blakestream-web-tls.service', lighttpd_dropin)
        php_ini = (ROOT / 'overlay/etc/php/5.6/cgi/conf.d/99-blakestream-runtime.ini').read_text()
        php_ini_compact = php_ini.replace(' ', '')
        for directive in ('session.use_strict_mode=1', 'session.cookie_secure=1', 'session.cookie_httponly=1'):
            self.assertIn(directive, php_ini_compact)
        shell_defaults = (ROOT / 'overlay/etc/default/shellinabox').read_text()
        self.assertIn('SHELLINABOX_DAEMON_START=0', shell_defaults)
        self.assertIn('--localhost-only', shell_defaults)

    def test_every_privileged_action_has_one_method_classification(self):
        for relative, read_var, mutation_var in (
                ('overlay/var/www/f_blakestream.php', '$readActions', '$mutationActions'),
                ('overlay/var/www/f_dev.php', '$devReadActions', '$devMutationActions')):
            text = (ROOT / relative).read_text()
            dispatch = text.rsplit('switch ($action)', 1)[1]
            cases = set(__import__('re').findall(r"case '([^']+)'", dispatch))
            def mapped(var):
                block = text.split(var + ' = array(', 1)[1].split(');', 1)[0]
                return set(__import__('re').findall(r"'([^']+)'", block))
            reads, mutations = mapped(read_var), mapped(mutation_var)
            self.assertFalse(reads & mutations, relative)
            self.assertEqual(cases, reads | mutations, relative)

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
