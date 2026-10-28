#!/usr/bin/env python3
"""Integration tests with real users, sudo, screen, HTTP and file-backed DFU."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

REPO = Path('/repo')
HERE = REPO / 'scripts/firmware-qa'
ETC = Path('/opt/scripta/etc')
FW = Path('/opt/scripta/firmware')
STATE = Path('/run/qa-hardware')
WEB_RUN = Path('/run/blakestream')
RUN = Path('/run/blakestream-firmware')
FIRSTBOOT_STATE = Path('/var/lib/blakestream-firstboot')
FIRSTBOOT_LOG = Path('/var/log/blakestream-firstboot')
WRAPPER = '/opt/scripta/bin/bkb-firmware.sh'
RESTART = '/opt/scripta/startup/miner-restart.sh'
STOCK = 'd5f844be2952fd713717a71a67e2be61'
CUSTOM = '18babe0c4c67d9146e230f9dc423f2f2'


def container_guard():
    marker = Path('/etc/bkb-firmware-qa-container')
    if (os.geteuid() != 0 or not Path('/.dockerenv').exists() or
            not marker.exists() or marker.read_text().strip() != 'bkb-firmware-qa-v1' or
            Path('/dev/bus/usb').exists()):
        raise SystemExit('Refusing: requires the isolated firmware QA container without USB devices')
    mounts = Path('/proc/mounts').read_text().splitlines()
    for path in ('/repo/scripts', '/repo/overlay'):
        if not any(row.split()[1] == path and 'ro' in row.split()[3].split(',') for row in mounts):
            raise SystemExit('Refusing: source must be mounted read-only at ' + path)
    if Path('/proc/1/cmdline').read_bytes().split(b'\0')[0].split(b'/')[-1] != b'docker-init':
        raise SystemExit('Refusing: requires Docker init in a private PID namespace')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    allowed_caps = sum(1 << bit for bit in (0, 1, 3, 4, 5, 6, 7, 29))
    if int(status['CapBnd'].strip(), 16) & ~allowed_caps:
        raise SystemExit('Refusing: unexpected container capabilities')
    for row in mounts:
        source, target, kind, options = row.split()[:4]
        if target == '/tmp' and kind != 'tmpfs':
            raise SystemExit('Refusing: /tmp must not be a host bind mount')
        if target == '/sys' and 'ro' not in options.split(','):
            raise SystemExit('Refusing: sysfs must be read-only')
        if target == '/sbin/docker-init' and 'ro' not in options.split(','):
            raise SystemExit('Refusing: Docker init must be read-only')
        if target in ('/', '/tmp', '/repo/scripts', '/repo/overlay',
                      '/etc/hosts', '/etc/hostname', '/etc/resolv.conf', '/sbin/docker-init'):
            continue
        if not any(target == base or target.startswith(base + '/') for base in ('/proc', '/sys', '/dev')):
            raise SystemExit('Refusing: unexpected mount at ' + target)
    if set(p.name for p in Path('/sys/class/net').iterdir()) != {'lo'}:
        raise SystemExit('Refusing: container networking must be disabled')


def command(args, user=None, check=True, timeout=45):
    if user:
        args = ['runuser', '-u', user, '--'] + args
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, timeout=timeout)
    if check and result.returncode:
        raise AssertionError('{0}: {1}'.format(args, result.stdout))
    return result


def put(path, value, uid=0, gid=0, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value)
    os.chown(str(path), uid, gid)
    path.chmod(mode)


def digest(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


class FirmwareContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        container_guard()
        shutil.copyfile(str(HERE / 'sitecustomize.py'), '/usr/lib/python2.7/sitecustomize.py')
        for name in ('lsusb', 'dfu-util'):
            target = Path('/usr/local/bin') / name
            shutil.copyfile(str(HERE / 'hardware.py'), str(target))
            target.chmod(0o755)
        put('/etc/sudoers', (REPO / 'overlay/etc/sudoers').read_text(), mode=0o440)
        for source in (REPO / 'overlay/etc/sudoers.d').iterdir():
            put('/etc/sudoers.d/' + source.name, source.read_text(), mode=0o440)
        command(['usermod', '-a', '-G', 'sudo', 'baikal'])
        if 'sudo' not in command(['id', '-Gn', 'baikal']).stdout.split():
            raise AssertionError('baikal must exercise password-required sudo membership')
        cls.screen_mode = stat.S_IMODE(os.stat('/usr/bin/screen').st_mode)
        cls.screen_uid = os.stat('/usr/bin/screen').st_uid
        cls.screen_gid = os.stat('/usr/bin/screen').st_gid
        print('Runtime: ' + command(['php', '-r', 'echo PHP_VERSION;']).stdout, flush=True)
        print(command(['/usr/bin/python', '--version']).stdout.strip(), flush=True)
        print(command(['screen', '--version'], check=False).stdout.strip(), flush=True)

    def setUp(self):
        self.http = None
        self.http_log = None
        self.addCleanup(self.cleanup)
        for directory in ('/opt/scripta', '/var/www', str(STATE), str(WEB_RUN), str(RUN),
                          str(FIRSTBOOT_STATE), str(FIRSTBOOT_LOG),
                          '/run/blakestream-miner', '/run/blakestream-control',
                          '/run/blakestream-security', '/run/blakestream-backup',
                          '/var/log/blakestream-miner', '/var/log/blakestream-security',
                          '/var/lib/blakestream-backups'):
            if os.path.exists(directory):
                shutil.rmtree(directory)
        shutil.copytree(str(REPO / 'overlay/opt/scripta'), '/opt/scripta')
        shutil.copytree(str(REPO / 'overlay/var/www'), '/var/www')
        command(['chown', '-R', 'root:root', '/opt/scripta'])
        for directory in ('/opt/scripta', '/opt/scripta/bin', '/opt/scripta/startup',
                          '/opt/scripta/firmware', '/opt/scripta/policy'):
            Path(directory).chmod(0o755)
        command(['chown', '-R', 'root:root', '/var/www'])
        for root, directories, files in os.walk('/var/www'):
            os.chmod(root, 0o755)
            for name in files:
                os.chmod(os.path.join(root, name), 0o644)
        for directory in (ETC, Path('/opt/scripta/var'), WEB_RUN):
            directory.mkdir(parents=True, exist_ok=True)
            os.chown(str(directory), 0, 33)
            directory.chmod(0o775)
        STATE.mkdir(mode=0o700)
        put(STATE / 'control.json', {})
        put(STATE / 'state.json', {'reads': 0, 'writes': 0, 'app_polls': 0})
        put(STATE / 'events.jsonl', '')
        put(STATE / 'miner-starts.jsonl', '')
        put(STATE / 'gpio.jsonl', '')
        for pin in ('PA18', 'PA10'):
            put(STATE / 'gpio' / pin / 'cfg', '0')
            put(STATE / 'gpio' / pin / 'data', '1')
        shutil.copyfile(str(HERE / 'miner.py'), '/opt/scripta/bin/sgminer')
        Path('/opt/scripta/bin/sgminer').chmod(0o755)
        put(ETC / 'scripta.conf', {'userDev': True, 'userDevLogTarget': 'RAM',
                                'userAdjustableClocks': False}, 33, 33, 0o664)
        put(ETC / 'miner.options.json', [
            {'key': 'api-port', 'value': '4028'},
            {'key': 'baikal-options', 'value': '400:40:55'},
            {'key': 'baikal-clock-mode', 'value': 'individual'},
            {'key': 'baikal-experimental-clock-index', 'value': True},
            {'key': 'baikal-board-clock-index', 'value': '32:32:32'}], 33, 33, 0o664)
        put(ETC / 'saved-pools.json', {'categories': [{'name': 'QA', 'pools': [
            {'id': 'fixture', 'url': 'stratum+tcp://127.0.0.1:3333', 'user': 'fixture',
             'pass': 'x', 'algo': 'blake256r8'}]}]}, 33, 33, 0o664)
        put(ETC / 'board-assignments.json', dict((str(i), 'fixture') for i in range(3)), 33, 33, 0o664)
        self.password = os.urandom(16).hex() if hasattr(bytes, 'hex') else 'container-only-password'
        hashed = subprocess.check_output(['php', '-r',
            'echo password_hash(stream_get_contents(STDIN), PASSWORD_BCRYPT, array("cost" => 10));'],
            input=self.password.encode()).decode()
        put(ETC / 'uipasswd', 'scripta:' + hashed, 33, 33, 0o600)
        command(['/opt/scripta/bin/prepare-runtime-dir.sh'])
        miner_runtime = Path('/run/blakestream-miner')
        self.assertEqual((os.stat(str(RUN)).st_uid, os.stat(str(RUN)).st_gid,
                          stat.S_IMODE(os.stat(str(RUN)).st_mode)), (0, 33, 0o750))
        for name in ('scripta-runtime-miner.conf', 'scripta-runtime-poolmap.json'):
            path = miner_runtime / name
            if path.exists():
                path.unlink()
        self.seed_firmware('stock')
        command([RESTART, '--preflight'])
        command(['visudo', '-c'])
        self.assertEqual(digest(FW / 'stock.bin'), STOCK)
        self.assertEqual(digest(FW / 'custom.bin'), CUSTOM)
        self.original_helpers = dict((path, Path(path).read_bytes()) for path in (
            WRAPPER, RESTART, '/opt/scripta/startup/miner-start.sh', str(FW / 'dfu_flash.py')))
        sessions = Path('/var/lib/php/sessions')
        sessions.mkdir(parents=True, exist_ok=True)
        sessions.chmod(0o1733)
        self.http_log = open('/tmp/qa-http.log', 'w+')
        self.http = subprocess.Popen(['runuser', '-u', 'www-data', '--', 'php',
            '-d', 'session.save_path=' + str(sessions), '-d', 'display_errors=0',
            '-S', '127.0.0.1:8080', '-t', '/var/www'], stdout=self.http_log, stderr=self.http_log)
        deadline = time.monotonic() + 5
        while True:
            try:
                self.request('firmware_status', authenticated=False)
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        self.login()
        self.pool_bytes = (ETC / 'saved-pools.json').read_bytes()
        self.pass_bytes = (ETC / 'uipasswd').read_bytes()
        timings = self.request('firmware_status')[1]['flash_runtimes']
        self.assertEqual(timings['revert']['seconds'], 164)
        self.assertEqual(timings['flash-custom']['seconds'], 164)
        self.assertFalse(timings['revert']['measured'])
        self.assertFalse(timings['flash-custom']['measured'])

    def cleanup(self):
        command(['pkill', '-f', '^/bin/sh /opt/scripta/bin/bkb-firmware.sh run-job '], check=False)
        command(['pkill', '-f', '^/usr/bin/python -u /opt/scripta/firmware/dfu_flash.py '], check=False)
        self.stop_miner()
        if self.http:
            self.http.terminate()
            try:
                self.http.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.http.kill()
                self.http.wait()
        if self.http_log:
            self.http_log.close()
        command(['pkill', '-f', '^php .*127.0.0.1:8080'], check=False)
        os.chown('/usr/bin/screen', self.screen_uid, self.screen_gid)
        os.chmod('/usr/bin/screen', self.screen_mode)

    def stop_miner(self):
        command(['screen', '-S', 'sgminer', '-X', 'quit'], check=False)
        command(['pkill', '-f', '^/usr/bin/python /opt/scripta/bin/sgminer '], check=False)
        command(['pkill', '-f', '^/usr/bin/python /opt/scripta/bin/capped-log-writer.py '], check=False)

    def firstboot(self):
        return command(['/usr/bin/python3', '/opt/scripta/bin/firmware-firstboot.py'],
                       check=False, timeout=140)

    def test_firstboot_custom_detection_never_writes_firmware(self):
        self.seed_firmware('custom')
        (FW / 'BRIDGE_GLOBAL_V1_READY').unlink()
        put(FIRSTBOOT_STATE / 'firmware-firstboot.pending', 'fresh image')
        put(ETC / 'scripta.conf', {'userAdjustableClocks': False,
                                 'userAutoFan': False, 'userDev': False}, 33, 33, 0o664)
        result = self.firstboot()
        self.assertEqual(result.returncode, 0, result.stdout)
        settings = json.loads((ETC / 'scripta.conf').read_text())
        self.assertTrue(settings['userAdjustableClocks'])
        self.assertFalse(settings['userAutoFan'])
        self.assertFalse(settings['userDev'])
        self.assertTrue((FW / 'BRIDGE_GLOBAL_V1_READY').exists())
        state = json.loads((STATE / 'state.json').read_text())
        self.assertEqual(state['reads'], 2)
        self.assertEqual(state['writes'], 0)
        self.assertEqual(digest(STATE / 'flash.bin'), CUSTOM)
        self.assertFalse((RUN / 'bkb-firmware.lock').exists())
        self.assertFalse((FIRSTBOOT_STATE / 'firmware-firstboot.pending').exists())
        self.assertNotEqual(command(['pgrep', '-f', '^/usr/bin/python /opt/scripta/bin/sgminer '], check=False).returncode, 0)
        settings['userAdjustableClocks'] = False
        settings['userAutoFan'] = True
        put(ETC / 'scripta.conf', settings, 33, 33, 0o664)
        saved_options = (ETC / 'miner.options.json').read_bytes()
        self.assertEqual(self.firstboot().returncode, 0)
        self.request('firmware_status')
        self.assertEqual(json.loads((ETC / 'scripta.conf').read_text()), settings)
        self.assertEqual((ETC / 'miner.options.json').read_bytes(), saved_options)
        self.assertEqual(json.loads((STATE / 'state.json').read_text())['reads'], 2)

    def test_firstboot_stock_detection_keeps_clocks_off(self):
        put(FIRSTBOOT_STATE / 'firmware-firstboot.pending', 'fresh image')
        result = self.firstboot()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(json.loads((ETC / 'scripta.conf').read_text())['userAdjustableClocks'])
        self.assertEqual(json.loads((FIRSTBOOT_STATE / 'result.json').read_text())['identity'], 'STOCK')
        state = json.loads((STATE / 'state.json').read_text())
        self.assertEqual(state['reads'], 2)
        self.assertEqual(state['writes'], 0)
        self.assertEqual(digest(STATE / 'flash.bin'), STOCK)

    def test_firstboot_failed_double_read_is_not_retried(self):
        put(FIRSTBOOT_STATE / 'firmware-firstboot.pending', 'fresh image')
        put(STATE / 'control.json', {'fault': 'backup-mismatch'})
        result = self.firstboot()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(json.loads((ETC / 'scripta.conf').read_text())['userAdjustableClocks'])
        self.assertFalse(json.loads((FIRSTBOOT_STATE / 'result.json').read_text())['ok'])
        self.assertFalse((RUN / 'bkb-firmware.lock').exists())
        self.assertFalse((FW / 'BRIDGE_GLOBAL_V1_READY').exists())
        self.assertEqual(self.firstboot().returncode, 0)
        state = json.loads((STATE / 'state.json').read_text())
        self.assertEqual(state['reads'], 2)
        self.assertEqual(state['writes'], 0)
        self.assertEqual(digest(STATE / 'flash.bin'), STOCK)

    def seed_firmware(self, kind):
        shutil.copyfile(str(FW / (kind + '.bin')), str(STATE / 'flash.bin'))
        md5 = STOCK if kind == 'stock' else CUSTOM
        record = {'identity': 'STOCK' if kind == 'stock' else 'CUSTOM V1', 'chip_md5': md5, 'at': int(time.time())}
        put(ETC / 'bkb-firmware-identity.json', record, 0, 33, 0o664)
        marker = FW / 'BRIDGE_GLOBAL_V1_READY'
        if kind == 'custom':
            put(marker, 'fixture verified V1')
        elif marker.exists():
            marker.unlink()

    def request(self, action, body=None, authenticated=True):
        headers = {'Content-Type': 'application/json'}
        if body is not None and authenticated:
            headers['X-BKB-CSRF'] = self.web_csrf
        request = urllib.request.Request('http://127.0.0.1:8080/f_dev.php?action=' + action,
            None if body is None else json.dumps(body).encode(), headers)
        client = self.client if authenticated else urllib.request.build_opener()
        try:
            response = client.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.loads(response.read().decode())

    def login(self):
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request = urllib.request.Request('http://127.0.0.1:8080/f_login.php',
            urllib.parse.urlencode({'userPassword': self.password}).encode())
        with self.client.open(request, timeout=5) as response:
            self.assertEqual(response.read().strip(), b'1')
        with self.client.open('http://127.0.0.1:8080/index.php', timeout=5) as response:
            page = response.read().decode()
        match = re.search(r'data-csrf="([0-9a-f]{64})"', page)
        self.assertIsNotNone(match, 'authenticated page must publish the request token')
        self.web_csrf = match.group(1)

    def test_password_free_audit_view_download_and_prune(self):
        path = Path('/opt/scripta/var/security.log')
        wrong = 'fixture-wrong-password-canary'
        request = urllib.request.Request('http://127.0.0.1:8080/f_login.php',
            urllib.parse.urlencode({'userPassword': wrong}).encode())
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.read().strip(), b'0')
        self.assertNotIn(wrong, path.read_text())
        self.assertIn('web login-FAIL', path.read_text())
        legacy = time.strftime('%Y-%m-%d %H:%M:%S') + ' web login-FAIL ip=127.0.0.1 pw="fixture-legacy-canary" ua="fixture"\n'
        for action in ('file_view', 'file_download', 'log_prune'):
            put(path, legacy, 33, 33, 0o660)
            if action == 'log_prune':
                code, body = self.request(action, {'path': str(path), 'days': 7})
                self.assertEqual(code, 200)
                self.assertTrue(body['ok'], body)
                self.assertEqual(body['kept'], 1)
            else:
                query = urllib.parse.urlencode({'action': action, 'path': str(path)})
                with self.client.open('http://127.0.0.1:8080/f_dev.php?' + query, timeout=10) as response:
                    text = response.read().decode()
                if action == 'file_view':
                    body = json.loads(text)
                    self.assertTrue(body['ok'], body)
                    text = body['text']
                self.assertNotIn('fixture-legacy-canary', text)
                self.assertIn('web login-FAIL', text)
            self.assertNotIn('fixture-legacy-canary', path.read_text())
        self.assertEqual((ETC / 'uipasswd').read_bytes(), self.pass_bytes)

    def start(self, kind='revert'):
        action, confirm = ('firmware_revert', 'REVERT') if kind == 'revert' else ('firmware_flash_custom', 'ENABLE')
        code, result = self.request(action, {'confirm': confirm})
        self.assertEqual(code, 200)
        self.assertTrue(result.get('started'), result)
        return result

    def finish(self):
        deadline = time.monotonic() + 120
        saw_progress = False
        while time.monotonic() < deadline:
            code, state = self.request('firmware_progress')
            self.assertEqual(code, 200)
            saw_progress = saw_progress or bool(state.get('progress'))
            if not state.get('running') and state.get('result'):
                self.assertTrue(saw_progress or not state['result']['ok'], state)
                result = dict(state['result'])
                result['_progress'] = state.get('progress', '')
                return result
            time.sleep(0.1)
        self.fail('Job timed out: ' + (RUN / 'bkb-firmware-progress.log').read_text(encoding='utf-8'))

    def fault(self, name):
        put(STATE / 'control.json', {'fault': name})

    def events(self):
        return [json.loads(line) for line in (STATE / 'events.jsonl').read_text().splitlines()]

    def miner_start_attempts(self):
        return [json.loads(line) for line in (STATE / 'miner-starts.jsonl').read_text().splitlines()]

    def assert_permissions(self, mining=True, cleared=True):
        command(['visudo', '-c'])
        self.assertEqual(os.stat('/usr/bin/sudo').st_uid, 0)
        self.assertTrue(os.stat('/usr/bin/sudo').st_mode & stat.S_ISUID)
        protected = list(self.original_helpers) + ['/opt/scripta', '/opt/scripta/bin',
            '/opt/scripta/startup', str(FW), str(FW / 'stock.bin'),
            str(FW / 'custom.bin'), '/etc/sudoers', '/etc/sudoers.d/blakestream-www']
        for path in protected:
            self.assertEqual(os.stat(path).st_uid, 0, path)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode & 0o002, 0, path)
            if mode & 0o020:
                self.assertEqual(os.stat(path).st_gid, 0, path)
            for user in ('www-data', 'baikal', 'qa-untrusted'):
                self.assertNotEqual(command(['test', '-w', path], user, check=False).returncode, 0, (user, path))
        for path, contents in self.original_helpers.items():
            self.assertEqual(Path(path).read_bytes(), contents, path)
        for user in ('www-data', 'baikal', 'qa-untrusted'):
            denied = command(['sudo', '-n', '/bin/sh', '-c', 'id -u'], user, check=False)
            self.assertNotEqual(denied.returncode, 0, user)
            self.assertRegex(denied.stdout, 'password is required|not allowed|not in the sudoers')
        for user in ('baikal', 'qa-untrusted'):
            self.assertNotEqual(command(['sudo', '-n', WRAPPER, 'start-job', 'verify'], user, check=False).returncode, 0, user)
        command(['test', '-r', str(RUN / 'bkb-firmware-progress.log')], 'www-data')
        self.assertEqual(stat.S_IMODE(os.stat(str(RUN)).st_mode), 0o750)
        self.assertEqual((os.stat(str(RUN)).st_uid, os.stat(str(RUN)).st_gid), (0, 33))
        for name in ('bkb-readback.bin', 'bkb-rollback-rb.bin', 'bkb-chip-readback.bin'):
            self.assertFalse((Path('/tmp') / name).exists(), name)
        command(['test', '-w', str(ETC / 'scripta.conf')], 'www-data')
        self.assertNotEqual(command(['test', '-w', str(ETC / 'scripta.conf')], 'qa-untrusted', check=False).returncode, 0)
        self.assertEqual((ETC / 'saved-pools.json').read_bytes(), self.pool_bytes)
        self.assertEqual((ETC / 'uipasswd').read_bytes(), self.pass_bytes)
        self.assertIn('devtools firmware-', Path('/opt/scripta/var/security.log').read_text())
        self.assertEqual(self.request('firmware_status', authenticated=False)[0], 403)
        self.assertTrue(self.request('firmware_status')[1]['ok'])
        processes = command(['ps', '-eo', 'uid,args']).stdout.splitlines()
        workers = [line.split()[0] for line in processes if 'php -d session.save_path=' in line and 'runuser' not in line]
        self.assertTrue(workers, processes)
        self.assertEqual(set(workers), {'33'})
        self.assertTrue(all(event['uid'] == 0 for event in self.events()))
        self.assertFalse((RUN / 'bkb-firmware.lock').exists())
        self.assertFalse((RUN / 'bkb-firmware.pid').exists())
        keys = [item['key'] for item in json.loads((ETC / 'miner.options.json').read_text())]
        if cleared:
            self.assertNotIn('baikal-experimental-clock-index', keys)
            self.assertNotIn('baikal-board-clock-index', keys)
        if mining:
            self.assertTrue(self.request('firmware_status')[1]['ready'])
            self.assertIn('sgminer', command(['screen', '-ls']).stdout)
            self.assertEqual(json.loads((STATE / 'miner.json').read_text())['uid'], 0)
            self.assertEqual(stat.S_IMODE(os.stat('/run/screen/S-root').st_mode), 0o700)
            self.assertNotEqual(command(['screen', '-S', 'sgminer', '-X', 'quit'], 'www-data', check=False).returncode, 0)
            self.assertIn('sgminer', command(['screen', '-ls']).stdout)
            self.assertTrue(self.request('firmware_status')[1]['ready'])
        else:
            checks = self.request('firmware_status')[1]['checks']
            self.assertFalse(next(row['ok'] for row in checks if row['name'] == 'Miner reachable (API)'))
            self.assertNotIn('sgminer', command(['screen', '-ls'], check=False).stdout)
            self.assertNotEqual(command(['pgrep', '-f', '^/usr/bin/python /opt/scripta/bin/sgminer '], check=False).returncode, 0)

    def assert_learned(self, action, result):
        record = self.request('firmware_status')[1]['flash_runtimes'][action]
        self.assertTrue(record['measured'], record)
        self.assertEqual(record['seconds'], int(result['elapsed']))

    def test_stock_revert_and_permissions(self):
        self.seed_firmware('custom')
        self.start()
        result = self.finish()
        self.assertTrue(result['ok'], result)
        self.assert_learned('revert', result)
        self.assertEqual(digest(STATE / 'flash.bin'), STOCK)
        self.assertEqual(digest('/opt/scripta/backups/bkb-preflash-backup.bin'), CUSTOM)
        self.assertFalse((FW / 'BRIDGE_GLOBAL_V1_READY').exists())
        self.assertFalse(json.loads((ETC / 'scripta.conf').read_text())['userAdjustableClocks'])
        self.assertEqual([e['kind'] for e in self.events()], ['read', 'read', 'write', 'read'])
        self.assert_permissions()

    def test_custom_flash_and_permissions(self):
        self.start('custom')
        result = self.finish()
        self.assertTrue(result['ok'], result)
        self.assert_learned('flash-custom', result)
        self.assertEqual(digest(STATE / 'flash.bin'), CUSTOM)
        self.assertEqual(digest('/opt/scripta/backups/bkb-preflash-backup.bin'), STOCK)
        self.assertTrue((FW / 'BRIDGE_GLOBAL_V1_READY').exists())
        self.assertTrue(json.loads((ETC / 'scripta.conf').read_text())['userAdjustableClocks'])
        self.assert_permissions()

    def test_first_flash_durably_creates_backup_parent_before_write_fault(self):
        backup_dir = Path('/opt/scripta/backups')
        backup = backup_dir / 'bkb-preflash-backup.bin'
        self.assertFalse(backup_dir.exists())
        self.fault('write-failure')
        self.start('custom')
        result = self.finish()
        self.assertEqual(int(result['exit_code']), 5, result)
        self.assertEqual((backup_dir.stat().st_uid, backup_dir.stat().st_gid,
                          stat.S_IMODE(backup_dir.stat().st_mode)), (0, 0, 0o700))
        self.assertEqual((backup.stat().st_uid, backup.stat().st_gid,
                          stat.S_IMODE(backup.stat().st_mode)), (0, 0, 0o600))
        self.assertEqual(digest(backup), STOCK)
        self.assertEqual(digest(STATE / 'flash.bin'), STOCK)
        kinds = [event['kind'] for event in self.events()]
        self.assertEqual(kinds[:3], ['read', 'read', 'write'])
        self.assertFalse((FIRSTBOOT_STATE / 'firmware-recovery-required').exists())
        self.assert_permissions()

    def test_read_only_verification(self):
        self.assertTrue(self.request('firmware_verify', {})[1]['started'])
        result = self.finish()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['chip_md5'], STOCK)
        self.assert_learned('verify', result)
        self.assertEqual([e['kind'] for e in self.events()], ['read', 'read'])
        self.assert_permissions()

    def test_read_cancel_preserves_settings(self):
        before = (ETC / 'miner.options.json').read_bytes()
        self.assertTrue(self.request('firmware_verify', {})[1]['started'])
        code, aborted = self.request('firmware_abort', {})
        self.assertEqual(code, 200)
        self.assertTrue(aborted['ok'], aborted)
        result = self.finish()
        self.assertFalse(result['ok'])
        self.assertEqual(int(result['exit_code']), 130)
        self.assertEqual((ETC / 'miner.options.json').read_bytes(), before)
        self.assertEqual(digest(STATE / 'flash.bin'), STOCK)
        self.assertFalse((ETC / 'bkb-firmware-runtime.json').exists())
        self.assertFalse(any(event['kind'] == 'write' for event in self.events()))
        self.assert_permissions(cleared=False)

    def test_gpio_model_requires_configured_reset_pulse(self):
        put(STATE / 'gpio.jsonl', '\n'.join(json.dumps(row) for row in (
            ['PA18', 'cfg', '1'], ['PA10', 'cfg', '1'], ['PA18', 'data', '0'])) + '\n')
        self.assertIn('5740', command(['lsusb']).stdout)
        with (STATE / 'gpio.jsonl').open('a') as trace:
            for row in (['PA10', 'data', '0'], ['PA10', 'data', '1']):
                trace.write(json.dumps(row) + '\n')
        self.assertIn('df11', command(['lsusb']).stdout)
        self.assertEqual(self.events(), [])

    def test_failure_matrix(self):
        for fault, expected_rc, reads, writes, message, mining in (
                ('backup-mismatch', 3, 2, 0, 'two reads disagree', True),
                ('read-failure', 3, 1, 0, 'first read failed', True),
                ('dfu-missing', 2, 0, 0, 'chip did not enter update mode', True),
                ('write-failure', 5, 3, 2, 'Write failed', True),
                ('readback-mismatch', 7, 4, 2, 'Verification mismatch', True),
                ('rollback-failure', 9, 5, 4, 'ROLLBACK UNVERIFIED', False)):
            with self.subTest(fault=fault):
                self.seed_firmware('stock')
                put(STATE / 'state.json', {'reads': 0, 'writes': 0, 'app_polls': 0})
                put(STATE / 'miner-starts.jsonl', '')
                self.fault(fault)
                self.start('custom')
                result = self.finish()
                self.assertFalse(result['ok'], result)
                self.assertEqual(int(result['exit_code']), expected_rc, result)
                self.assertEqual(json.loads((STATE / 'state.json').read_text())['writes'], writes)
                self.assertEqual(json.loads((STATE / 'state.json').read_text())['reads'], reads)
                self.assertIn(message, (RUN / 'bkb-firmware-progress.log').read_text(encoding='utf-8'))
                self.assertFalse((FW / 'BRIDGE_GLOBAL_V1_READY').exists())
                self.assertFalse(json.loads((ETC / 'scripta.conf').read_text())['userAdjustableClocks'])
                self.assertFalse((ETC / 'bkb-firmware-runtime.json').exists())
                if mining:
                    self.assertEqual(digest(STATE / 'flash.bin'), STOCK)
                    self.assertGreaterEqual(len(self.miner_start_attempts()), 1)
                else:
                    recovery = FIRSTBOOT_STATE / 'firmware-recovery-required'
                    self.assertTrue(recovery.is_file())
                    self.assertEqual(stat.S_IMODE(recovery.stat().st_mode), 0o600)
                    self.assertEqual(self.miner_start_attempts(), [])
                    self.assertIn('0483:df11', command(['lsusb']).stdout)
                    # Model a controller power cycle: GPIO history/defaults let
                    # the bridge attempt application boot until the early boot
                    # recovery service reasserts BOOT0 and reset. It must do so
                    # before either miner launcher gets a chance to run.
                    put(STATE / 'gpio.jsonl', '')
                    self.assertIn('0483:5740', command(['lsusb']).stdout)
                    command(['/opt/scripta/bin/enforce-firmware-recovery-hold.sh'])
                    self.assertEqual(self.miner_start_attempts(), [])
                    self.assertIn('0483:df11', command(['lsusb']).stdout)
                    put(FIRSTBOOT_STATE / 'firmware-firstboot.pending', 'fresh image')
                    firstboot = self.firstboot()
                    self.assertNotEqual(firstboot.returncode, 0, firstboot.stdout)
                    self.assertTrue((FIRSTBOOT_STATE / 'firmware-firstboot.pending').exists())
                    self.assertIn('0483:df11', command(['lsusb']).stdout)
                    self.assertEqual(self.miner_start_attempts(), [])
                    for launcher in ('/opt/scripta/startup/miner-start.sh', RESTART):
                        denied = command([launcher], check=False)
                        self.assertNotEqual(denied.returncode, 0, (launcher, denied.stdout))
                    self.assertEqual(self.miner_start_attempts(), [])
                    self.assertIn('0483:df11', command(['lsusb']).stdout)
                self.assert_permissions(mining=mining)
                self.stop_miner()

    def test_restart_failure_does_not_train_timing(self):
        self.fault('restart-failure')
        self.start('custom')
        result = self.finish()
        self.assertFalse(result['ok'])
        self.assertIn('restart failed', result['message'])
        self.assertEqual(digest(STATE / 'flash.bin'), CUSTOM)
        self.assertFalse((ETC / 'bkb-firmware-runtime.json').exists())
        self.assert_permissions(mining=False)

    def test_failed_safety_backup_preserves_prior_durable_backup(self):
        backup = Path('/opt/scripta/backups/bkb-preflash-backup.bin')
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(FW / 'custom.bin'), str(backup))
        backup.chmod(0o600)
        before = backup.read_bytes()
        self.fault('backup-mismatch')
        self.start('custom')
        result = self.finish()
        self.assertEqual(int(result['exit_code']), 3, result)
        self.assertEqual(backup.read_bytes(), before)
        state = json.loads((STATE / 'state.json').read_text())
        self.assertEqual((state['reads'], state['writes']), (2, 0))
        self.assertFalse((FIRSTBOOT_STATE / 'firmware-recovery-required').exists())
        self.assert_permissions()

    def test_unverified_rollback_requires_explicit_verified_recovery(self):
        put(STATE / 'miner-starts.jsonl', '')
        self.fault('rollback-failure')
        self.start('custom')
        failed = self.finish()
        self.assertEqual(int(failed['exit_code']), 9, failed)
        recovery = FIRSTBOOT_STATE / 'firmware-recovery-required'
        self.assertTrue(recovery.is_file())
        self.assertEqual(self.miner_start_attempts(), [])
        self.assertIn('0483:df11', command(['lsusb']).stdout)
        backup_before = digest('/opt/scripta/backups/bkb-preflash-backup.bin')
        reads_before = json.loads((STATE / 'state.json').read_text())['reads']

        # A newly confirmed flash is the only normal way out of the hold.  It
        # validates the retained rollback image without overwriting it from the
        # unverified chip, writes the pinned stock target, and clears the gate
        # only after target readback succeeds.
        self.fault('')
        self.start('revert')
        recovered = self.finish()
        self.assertTrue(recovered['ok'], recovered)
        self.assertFalse(recovery.exists())
        self.assertEqual(digest(STATE / 'flash.bin'), STOCK)
        self.assertEqual(digest('/opt/scripta/backups/bkb-preflash-backup.bin'), backup_before)
        # Recovery validates the retained backup in place; it must not replace
        # it by double-reading the known-unverified chip. The one new read is
        # target verification after the recovery write.
        self.assertEqual(json.loads((STATE / 'state.json').read_text())['reads'], reads_before + 1)
        self.assertIn('0483:5740', command(['lsusb']).stdout)
        self.assertGreaterEqual(len(self.miner_start_attempts()), 1)
        self.assert_permissions()

    def test_slow_usb_snapshot_allows_guarded_restart(self):
        self.fault('slow-usb')
        self.start()
        self.assertTrue(self.finish()['ok'])
        self.assertIn('Normal mode: False', (RUN / 'bkb-firmware-progress.log').read_text(encoding='utf-8'))
        self.assert_permissions()

    def test_auth_confirmation_and_invalid_image(self):
        self.assertEqual(self.request('firmware_revert', {'confirm': 'REVERT'}, authenticated=False)[0], 403)
        self.assertFalse(self.request('firmware_revert', {'confirm': 'WRONG'})[1]['ok'])
        self.assertFalse((RUN / 'bkb-firmware.lock').exists())
        self.assertFalse((RUN / 'bkb-firmware.pid').exists())
        (FW / 'custom.bin').write_bytes(b'bad image')
        self.start('custom')
        self.assertFalse(self.finish()['ok'])
        self.assertEqual(self.events(), [])
        self.assertFalse((RUN / 'bkb-firmware.lock').exists())
        self.assertFalse((ETC / 'bkb-firmware-runtime.json').exists())

    def test_reconnect_duplicate_and_unabortable_write(self):
        self.start('custom')
        self.login()
        self.assertFalse(self.request('firmware_flash_custom', {'confirm': 'ENABLE'})[1]['ok'])
        self.assertFalse(self.request('firmware_abort', {})[1]['ok'])
        self.assertTrue(self.finish()['ok'])
        first = (ETC / 'bkb-firmware-runtime.json').read_bytes()
        self.request('firmware_progress')
        self.assertEqual((ETC / 'bkb-firmware-runtime.json').read_bytes(), first)
        self.assertEqual(json.loads((STATE / 'state.json').read_text())['writes'], 1)
        self.assert_permissions()

    def test_permission_repair_and_stale_lock(self):
        with open('/etc/sudoers.d/blakestream-www', 'a') as policy:
            policy.write('\nwww-data ALL=(root) NOPASSWD: /bin/sh\n')
        for path in (WRAPPER, str(FW / 'stock.bin'), '/etc/sudoers.d/blakestream-www'):
            os.chown(path, 33, 33)
            os.chmod(path, 0o777)
        self.assertNotEqual(command(['sudo', '-n', RESTART, '--preflight'], 'www-data', check=False).returncode, 0)
        put(RUN / 'bkb-firmware.lock', '')
        put(RUN / 'bkb-firmware.pid', '99999999')
        os.utime(str(RUN / 'bkb-firmware.lock'), (time.time() - 1300,) * 2)
        command([RESTART, '--preflight'])
        self.assertEqual(Path('/etc/sudoers.d/blakestream-www').read_text(),
                         (REPO / 'overlay/etc/sudoers.d/blakestream-www').read_text())
        command(['sudo', '-n', RESTART, '--preflight'], 'www-data')
        self.assertFalse((RUN / 'bkb-firmware.lock').exists())
        put(RUN / 'bkb-firmware.lock', '')
        put(RUN / 'bkb-firmware.pid', str(os.getpid()))
        command([RESTART, '--preflight'])
        self.assertTrue((RUN / 'bkb-firmware.lock').exists())
        self.assertFalse(self.request('firmware_revert', {'confirm': 'REVERT'})[1]['ok'])
        (RUN / 'bkb-firmware.lock').unlink()
        (RUN / 'bkb-firmware.pid').unlink()
        self.start()
        self.assertTrue(self.finish()['ok'])
        self.assert_permissions()

    def test_privileged_hardlinks_fail_webroot_closed_and_are_not_reused_for_progress(self):
        protected = Path('/run/bkb-qa-hardlink-target')
        web_link = Path('/var/www/bkb-qa-hardlink.php')
        progress = RUN / 'bkb-firmware-progress.log'
        for path in (web_link, progress, protected):
            if path.exists() or path.is_symlink():
                path.unlink()
        self.addCleanup(lambda: protected.exists() and protected.unlink())
        put(protected, 'hardlink target must remain unchanged', 0, 0, 0o600)
        before = (protected.read_bytes(), protected.stat().st_uid,
                  protected.stat().st_gid, stat.S_IMODE(protected.stat().st_mode))
        os.link(str(protected), str(web_link))
        os.link(str(protected), str(progress))

        repair = command(['/opt/scripta/bin/repair-system-perms.sh'], check=False)
        self.assertNotEqual(repair.returncode, 0)
        self.assertTrue(web_link.exists())
        self.assertFalse(progress.exists())
        self.assertEqual((protected.read_bytes(), protected.stat().st_uid,
                          protected.stat().st_gid, stat.S_IMODE(protected.stat().st_mode)), before)
        self.assertEqual(protected.stat().st_nlink, 2)

        web_link.unlink()
        command(['/opt/scripta/bin/repair-system-perms.sh'])
        self.assertEqual(protected.stat().st_nlink, 1)

        started = json.loads(command([WRAPPER, 'start-job', 'verify'], timeout=60).stdout)
        self.assertTrue(started.get('started'), started)
        self.assertTrue(progress.is_file())
        self.assertEqual(progress.stat().st_nlink, 1)
        self.assertNotEqual(progress.stat().st_ino, protected.stat().st_ino)
        self.assertEqual((protected.read_bytes(), protected.stat().st_uid,
                          protected.stat().st_gid, stat.S_IMODE(protected.stat().st_mode)), before)
        result = self.finish()
        self.assertTrue(result['ok'], result)
        self.assertEqual((protected.read_bytes(), protected.stat().st_uid,
                          protected.stat().st_gid, stat.S_IMODE(protected.stat().st_mode)), before)

    def test_recovery_marker_tampering_is_never_repaired_as_absence(self):
        marker = FIRSTBOOT_STATE / 'firmware-recovery-required'
        protected = Path('/run/bkb-qa-recovery-marker-target')
        for path in (marker, protected):
            if path.exists() or path.is_symlink():
                path.unlink()
        self.addCleanup(lambda: marker.unlink() if marker.exists() or marker.is_symlink() else None)
        self.addCleanup(lambda: protected.exists() and protected.unlink())
        put(protected, 'preserve critical inode', 0, 0, 0o600)
        before = (protected.read_bytes(), protected.stat().st_uid,
                  protected.stat().st_gid, stat.S_IMODE(protected.stat().st_mode))

        os.link(str(protected), str(marker))
        rejected = command(['/opt/scripta/bin/prepare-runtime-dir.sh'], check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.stat().st_ino, protected.stat().st_ino)
        self.assertEqual((protected.read_bytes(), protected.stat().st_uid,
                          protected.stat().st_gid, stat.S_IMODE(protected.stat().st_mode)), before)
        marker.unlink()

        marker.symlink_to(protected)
        rejected = command(['/opt/scripta/bin/prepare-runtime-dir.sh'], check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertTrue(marker.is_symlink())
        marker.unlink()

        put(marker, 'valid recovery hold', 0, 33, 0o640)
        command(['/opt/scripta/bin/prepare-runtime-dir.sh'])
        self.assertEqual((marker.stat().st_uid, marker.stat().st_gid,
                          stat.S_IMODE(marker.stat().st_mode), marker.stat().st_nlink),
                         (0, 0, 0o600, 1))

    def test_screen_permissions_before_mining_and_live_session_preservation(self):
        repair = '/opt/scripta/bin/repair-system-perms.sh'
        put(ETC / 'board-assignments.json', dict((str(i), None) for i in range(3)), 33, 33, 0o664)
        for mode in (0o755, 0o2755, 0o4755):
            with self.subTest(mode=oct(mode)):
                self.stop_miner()
                os.chmod('/usr/bin/screen', mode)
                Path('/run/screen').mkdir(parents=True, exist_ok=True)
                os.chmod('/run/screen', 0o700)
                command([repair])
                expected = 0o755 if mode & stat.S_ISUID else (0o775 if mode & stat.S_ISGID else 0o1777)
                self.assertEqual(stat.S_IMODE(os.stat('/run/screen').st_mode), expected)
                for user in ('root', 'baikal'):
                    listing = command(['screen', '-ls'], user, check=False)
                    self.assertNotIn('must have mode', listing.stdout)
                    self.assertNotIn('sgminer', listing.stdout)
                command(['screen', '-dmS', 'qa-boot-root', 'sleep', '30'])
                command(['screen', '-dmS', 'qa-boot-user', 'sleep', '30'], 'baikal')
                try:
                    sockets = [p for name in ('S-root', 'S-baikal')
                               for p in (Path('/run/screen') / name).iterdir()]
                    before = {str(p): p.stat().st_ino for p in sockets}
                    command([repair])
                    self.assertEqual({str(p): p.stat().st_ino for p in sockets}, before)
                    self.assertIn('qa-boot-root', command(['screen', '-ls']).stdout)
                    self.assertIn('qa-boot-user', command(['screen', '-ls'], 'baikal').stdout)
                    self.assertEqual(stat.S_IMODE(os.stat('/run/screen/S-root').st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(os.stat('/run/screen/S-baikal').st_mode), 0o700)
                finally:
                    command(['screen', '-S', 'qa-boot-root', '-X', 'quit'], check=False)
                    command(['screen', '-S', 'qa-boot-user', '-X', 'quit'], 'baikal', check=False)

    def test_screen_modes_and_user_sessions(self):
        cleanup = Path('/etc/init.d/screen-cleanup')
        held = Path('/etc/init.d/screen-cleanup.qa-held')
        self.assertTrue(cleanup.is_file())
        for use_cleanup in (True, False):
            if not use_cleanup:
                cleanup.rename(held)
            try:
                self.check_screen_modes(use_cleanup)
            finally:
                if held.exists():
                    held.rename(cleanup)

    def check_screen_modes(self, use_cleanup):
        for mode in (0o755, 0o2755, 0o4755):
            with self.subTest(mode=oct(mode), cleanup=use_cleanup):
                self.stop_miner()
                os.chmod('/usr/bin/screen', mode)
                if Path('/run/screen').exists():
                    os.chmod('/run/screen', 0o700)
                self.start()
                self.assertTrue(self.finish()['ok'])
                expected = 0o755 if mode & stat.S_ISUID else (0o775 if mode & stat.S_ISGID else 0o1777)
                self.assertEqual(stat.S_IMODE(os.stat('/run/screen').st_mode), expected)
                self.assert_permissions()
                command(['screen', '-dmS', 'qa-user', 'sleep', '30'], 'baikal')
                try:
                    self.assertIn('qa-user', command(['screen', '-ls'], 'baikal').stdout)
                    self.assertEqual(os.stat('/run/screen/S-baikal').st_uid, 1000)
                    self.assertNotEqual(command(['screen', '-S', 'qa-user', '-X', 'quit'], 'qa-untrusted', check=False).returncode, 0)
                    self.assertIn('qa-user', command(['screen', '-ls'], 'baikal').stdout)
                finally:
                    command(['screen', '-S', 'qa-user', '-X', 'quit'], 'baikal', check=False)


if __name__ == '__main__':
    container_guard()
    unittest.main(verbosity=2)
