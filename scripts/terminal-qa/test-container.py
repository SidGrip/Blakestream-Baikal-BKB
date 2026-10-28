#!/usr/bin/env python3
"""Exercise the packaged HTTPS terminal in a disposable, networkless SD rootfs.

Run only in a Docker image imported from the staged ARM rootfs, with no devices
or host filesystem mounts. Test credentials exist only in that container.
"""
import binascii
import configparser
import json
import os
from pathlib import Path
import pwd
import shlex
import socket
import ssl
import subprocess
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_container():
    if (os.geteuid() != 0 or not Path('/.dockerenv').is_file() or
            os.environ.get('BKB_TERMINAL_QA') != '1' or
            sorted(os.listdir('/sys/class/net')) != ['lo']):
        raise RuntimeError('requires an isolated Docker container with --network none')


class Session:
    def __init__(self, opener):
        self.opener = opener
        self.session = ''
        self.output = ''
        self.poll()

    def request(self, keys=None):
        fields = {'width': '100', 'height': '30'}
        if self.session:
            fields['session'] = self.session
        else:
            fields['rooturl'] = 'https://127.0.0.1/terminal/'
        if keys is not None:
            fields['keys'] = binascii.hexlify(keys.encode('utf-8')).decode('ascii')
        req = Request('https://127.0.0.1/terminal/?',
                      data=urlencode(fields).encode('ascii'),
                      headers={'Content-Type': 'application/x-www-form-urlencoded',
                               'Origin': 'https://127.0.0.1'})
        with self.opener.open(req, timeout=15) as response:
            body = response.read()
        if keys is None:
            payload = json.loads(body.decode('utf-8'))
            self.session = payload.get('session', '')
            self.output += payload.get('data', '')

    def poll(self):
        self.request()

    def wait_for(self, needle):
        deadline = time.monotonic() + 30
        while needle.lower() not in self.output.lower():
            if time.monotonic() >= deadline:
                raise AssertionError('terminal did not reach expected login/session state')
            self.poll()

    def send(self, keys):
        self.output = ''
        self.request(keys)


class TerminalTests(unittest.TestCase):
    processes = []
    logs = []

    @classmethod
    def start(cls, command, name, preexec_fn=None):
        log = open('/tmp/terminal-qa-' + name + '.log', 'wb')
        cls.logs.append(log)
        proc = subprocess.Popen(command, stdout=log, stderr=log, preexec_fn=preexec_fn)
        cls.processes.append(proc)
        return proc

    @classmethod
    def wait_port(cls, port):
        for unused in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('test service failed to listen on port ' + str(port))

    @classmethod
    def setUpClass(cls):
        check_container()
        cls.root_password = binascii.hexlify(os.urandom(18)).decode('ascii')
        proc = subprocess.Popen(['chpasswd'], stdin=subprocess.PIPE)
        proc.communicate(('root:' + cls.root_password + '\n').encode('ascii'))
        if proc.returncode:
            raise RuntimeError('failed to prepare isolated test account')
        subprocess.check_call(['useradd', '-m', '-s', '/bin/sh', 'terminal-qa-denied'])
        proc = subprocess.Popen(['chpasswd'], stdin=subprocess.PIPE)
        proc.communicate(('terminal-qa-denied:' + cls.root_password + '\n').encode('ascii'))
        if proc.returncode:
            raise RuntimeError('failed to prepare denied test account')
        Path('/run/sshd').mkdir(parents=True, exist_ok=True)
        subprocess.check_call(['ssh-keygen', '-A'], stdout=subprocess.DEVNULL)
        entropy = Path('/run/blakestream-identity-entropy.ready')
        entropy.write_text('isolated test kernel entropy is ready\n')
        entropy.chmod(0o600)
        subprocess.check_call(['/bin/sh', '/opt/scripta/bin/ensure-web-tls.sh'])
        subprocess.check_call(['/bin/sh', '/opt/scripta/bin/prepare-runtime-dir.sh'])
        subprocess.check_call(['/bin/sh', '/opt/scripta/bin/prepare-web-runtime.sh'])
        cls.start(['/usr/sbin/sshd', '-D', '-e'], 'ssh')
        cls.wait_port(22)
        unit = configparser.ConfigParser(interpolation=None)
        unit.read('/etc/systemd/system/blakestream-terminal.service')
        account = pwd.getpwnam(unit.get('Service', 'User'))
        if account.pw_uid == 0:
            raise RuntimeError('terminal daemon must be unprivileged')

        def unprivileged():
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)

        cls.start(shlex.split(unit.get('Service', 'ExecStart')), 'backend', unprivileged)
        cls.wait_port(4200)
        subprocess.check_call(['/usr/sbin/lighttpd', '-tt', '-f', '/etc/lighttpd/lighttpd.conf'])
        cls.start(['/usr/sbin/lighttpd', '-D', '-f', '/etc/lighttpd/lighttpd.conf'], 'https')
        try:
            cls.wait_port(443)
        except RuntimeError:
            raise RuntimeError('HTTPS startup failed: ' +
                               Path('/tmp/terminal-qa-https.log').read_text()[-2000:])
        cls.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl._create_unverified_context()))

    @classmethod
    def tearDownClass(cls):
        for proc in reversed(cls.processes):
            proc.terminate()
        for proc in reversed(cls.processes):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for log in cls.logs:
            log.close()

    def test_backend_is_loopback_only(self):
        listeners = []
        for table in ('/proc/net/tcp', '/proc/net/tcp6'):
            for row in Path(table).read_text().splitlines()[1:]:
                fields = row.split()
                if fields[3] == '0A' and fields[1].endswith(':1068'):
                    listeners.append(fields[1])
        self.assertEqual(listeners, ['0100007F:1068'])

    def test_https_terminal_and_assets_are_served_without_downgrade(self):
        with self.opener.open('https://127.0.0.1/terminal/', timeout=10) as response:
            self.assertEqual(response.getcode(), 200)
            self.assertEqual(response.headers.get('X-Frame-Options'), 'SAMEORIGIN')
            self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
            html = response.read()
        self.assertIn(b'ShellInABox', html)
        with self.opener.open('https://127.0.0.1/terminal/ShellInABox.js', timeout=10) as response:
            self.assertEqual(response.getcode(), 200)
            self.assertIn(b'XMLHttpRequest', response.read())

    def test_http_redirects_terminal_to_https(self):
        with self.assertRaises(HTTPError) as caught:
            self.opener.open('http://127.0.0.1/terminal/', timeout=10)
        self.assertIn(caught.exception.code, (301, 302, 307, 308))
        self.assertEqual(caught.exception.headers['Location'], 'https://127.0.0.1/terminal/')

    def test_terminal_default_theme_is_white_on_black(self):
        with self.opener.open('https://127.0.0.1/terminal/styles.css', timeout=10) as response:
            css = response.read()
        self.assertIn(b'Blakestream terminal: white on black', css)
        self.assertIn(b'#vt100 .ansiDef { color: #ffffff; }', css)
        self.assertIn(b'#vt100 .bgAnsiDef { background-color: #000000; }', css)

    def test_ssh_login_authenticates_and_shell_executes_only_after_login(self):
        session = Session(self.opener)
        session.wait_for('login:')
        session.send('root\r')
        session.wait_for('password:')
        session.send(self.root_password + '\r')
        session.wait_for('#')
        session.send("printf 'BKB_TERMINAL_%s\\n' OK\r")
        session.wait_for('BKB_TERMINAL_OK')
        session.send('exit\r')

    def test_non_allowlisted_account_cannot_obtain_shell(self):
        session = Session(self.opener)
        session.wait_for('login:')
        session.send('terminal-qa-denied\r')
        session.wait_for('password:')
        session.send(self.root_password + '\r')
        deadline = time.monotonic() + 15
        while True:
            log = Path('/tmp/terminal-qa-ssh.log').read_text()
            if 'Failed password for invalid user terminal-qa-denied' in log:
                break
            self.assertLess(time.monotonic(), deadline, 'SSH did not reject the excluded account')
            time.sleep(0.1)
        self.assertIn('not allowed because not listed in AllowUsers', log)
        self.assertNotIn('Accepted password for terminal-qa-denied', log)


if __name__ == '__main__':
    check_container()
    unittest.main(verbosity=2)
