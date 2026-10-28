#!/usr/bin/env python3
"""Exercise the HTTPS identity helper in temporary directories, without root."""
import os
from pathlib import Path
import shlex
import shutil
import ssl
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / 'overlay/opt/scripta/bin/ensure-web-tls.sh'
OPENSSL = shutil.which('openssl')


def fixture(root):
    source = HELPER.read_text()
    replacements = {
        'cert_dir=/etc/lighttpd/certs': 'cert_dir=' + shlex.quote(str(root / 'certs')),
        'empty_root=/var/empty/blakestream-http': 'empty_root=' + shlex.quote(str(root / 'http')),
        'entropy_ready=/run/blakestream-identity-entropy.ready':
            'entropy_ready=' + shlex.quote(str(root / 'entropy-ready')),
        "'0:600'": "'%d:600'" % os.geteuid(),
    }
    for original, replacement in replacements.items():
        if source.count(original) != 1:
            raise AssertionError('TLS helper fixture anchor changed: ' + original)
        source = source.replace(original, replacement, 1)
    # Only fixture ownership calls are neutralized; the production helper keeps
    # its fixed root-owned paths and exposes no caller-controlled path overrides.
    source = source.replace('chown root:root', ':')
    source = source.replace('/usr/bin/openssl', shlex.quote(OPENSSL))
    helper = root / 'ensure-web-tls.sh'
    helper.write_text(source)
    marker = root / 'entropy-ready'
    marker.write_text('ready\n')
    marker.chmod(0o600)
    return helper


def invoke(helper):
    return subprocess.run(['sh', str(helper)], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=30,
                          env=dict(os.environ, LC_ALL='C'))


class WebTls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not OPENSSL:
            raise RuntimeError('OpenSSL is required for HTTPS identity tests')
        cls.seed = tempfile.TemporaryDirectory(prefix='bkb tls seed ')
        root = Path(cls.seed.name)
        result = invoke(fixture(root))
        if result.returncode:
            cls.seed.cleanup()
            raise RuntimeError(result.stderr.decode('utf-8', 'replace'))
        cls.good_pem = (root / 'certs/lighttpd.pem').read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.seed.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bkb tls test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.helper = fixture(self.root)
        self.pem = self.root / 'certs/lighttpd.pem'

    def install(self, content=None):
        self.pem.parent.mkdir(exist_ok=True)
        self.pem.write_bytes(self.good_pem if content is None else content)
        self.pem.chmod(0o600)

    def text(self):
        return subprocess.check_output([OPENSSL, 'x509', '-in', str(self.pem),
                                        '-noout', '-text'], env=dict(os.environ, LC_ALL='C')).decode()

    def assert_refused_unchanged(self, message):
        before = self.pem.read_bytes()
        result = invoke(self.helper)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr.decode())
        self.assertEqual(self.pem.read_bytes(), before)
        self.assertEqual(list(self.pem.parent.glob('.generate.*')), [])

    def test_fresh_certificate_is_v3_and_valid_for_tls_server(self):
        result = invoke(self.helper)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        text = self.text()
        for value in ('Version: 3 (0x2)', 'X509v3 Subject Alternative Name:',
                      'DNS:', 'CA:FALSE', 'Digital Signature, Key Encipherment',
                      'TLS Web Server Authentication'):
            self.assertIn(value, text)
        dates = subprocess.check_output([OPENSSL, 'x509', '-in', str(self.pem), '-noout', '-dates']).decode()
        self.assertIn('notBefore=Jan  1 00:00:00 2020 GMT', dates)
        self.assertIn('notAfter=Dec 31 23:59:59 2049 GMT', dates)
        subprocess.check_call([OPENSSL, 'verify', '-partial_chain', '-purpose', 'sslserver', '-CAfile',
                               str(self.pem), str(self.pem)], stdout=subprocess.DEVNULL)
        self.assertEqual(stat.S_IMODE(self.pem.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.pem.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / 'http').stat().st_mode), 0o755)
        self.assertEqual(list(self.pem.parent.glob('.generate.*')), [])
        self.assertNotEqual(self.pem.read_bytes(), self.good_pem)

    def test_valid_existing_identity_is_not_rotated_without_entropy_marker(self):
        self.install()
        before_mtime = self.pem.stat().st_mtime_ns
        (self.root / 'entropy-ready').unlink()
        result = invoke(self.helper)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.pem.read_bytes(), self.good_pem)
        self.assertEqual(self.pem.stat().st_mtime_ns, before_mtime)

    def test_v1_with_extensions_is_rejected_without_replacement(self):
        self.install()
        der = bytearray(subprocess.check_output([OPENSSL, 'x509', '-in', str(self.pem), '-outform', 'DER']))
        # Deliberately malformed fixture: keep extensions but change the v3 tag
        # to v1, which OpenSSL can decode but strict browser parsers reject.
        version = der.index(b'\xa0\x03\x02\x01\x02', 0, 20)
        der[version + 4] = 0
        key = subprocess.check_output([OPENSSL, 'rsa', '-in', str(self.pem)], stderr=subprocess.DEVNULL)
        self.pem.write_bytes(key + ssl.DER_cert_to_PEM_cert(bytes(der)).encode())
        self.assertIn('Version: 1 (0x0)', self.text())
        self.assertIn('X509v3 Subject Alternative Name:', self.text())
        self.assert_refused_unchanged('not X.509 v3')

    def test_malformed_pem_is_not_replaced(self):
        self.install(b'not a certificate\n')
        self.assert_refused_unchanged('PEM is invalid')

    def test_mismatched_key_is_not_replaced(self):
        self.install()
        cert = subprocess.check_output([OPENSSL, 'x509', '-in', str(self.pem)])
        key = subprocess.check_output([OPENSSL, 'genrsa', '2048'], stderr=subprocess.DEVNULL)
        self.pem.write_bytes(key + cert)
        self.assert_refused_unchanged('do not match')

    def test_missing_entropy_prevents_generation(self):
        (self.root / 'entropy-ready').unlink()
        result = invoke(self.helper)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'entropy is not ready', result.stderr)
        self.assertFalse(self.pem.exists())

    def test_writable_or_symlinked_entropy_marker_is_rejected(self):
        marker = self.root / 'entropy-ready'
        marker.chmod(0o644)
        self.assertNotEqual(invoke(self.helper).returncode, 0)
        target = self.root / 'outside-marker'
        marker.rename(target)
        target.chmod(0o600)
        marker.symlink_to(target)
        self.assertNotEqual(invoke(self.helper).returncode, 0)
        self.assertFalse(self.pem.exists())

    def test_symlinked_certificate_is_not_followed(self):
        self.pem.parent.mkdir()
        outside = self.root / 'outside.pem'
        outside.write_bytes(self.good_pem)
        self.pem.symlink_to(outside)
        self.assertNotEqual(invoke(self.helper).returncode, 0)
        self.assertEqual(outside.read_bytes(), self.good_pem)

    def test_symlinked_certificate_directory_is_not_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.pem.parent.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(invoke(self.helper).returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
