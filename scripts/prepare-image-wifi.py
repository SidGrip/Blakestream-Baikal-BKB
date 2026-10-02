#!/usr/bin/env python3
"""Enable onboard Wi-Fi discovery in a staged boot config; the radio defaults off."""
import configparser
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def read_fex(path):
    config = configparser.RawConfigParser(strict=True)
    config.optionxform = str
    with Path(path).open() as source:
        config.read_file(source)
    return config


def values(config):
    return {section: dict(config[section]) for section in config.sections()}


def prepare_modules(root):
    root = Path(root).resolve(strict=True)
    if root == Path('/') or not (root / 'etc').is_dir() or not (root / 'opt/scripta').is_dir():
        raise ValueError('refusing module changes outside a staged Scripta rootfs')
    path = root / 'etc/modules'
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('refusing a symlinked modules file')
    if not path.exists():
        return False
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('expected a regular staged modules file')
    original = path.read_text()
    retained = []
    for line in original.splitlines(keepends=True):
        tokens = shlex.split(line, comments=True)
        if tokens and tokens[0] == '8189es':
            # The unused factory driver changes the XR819's shared power and SDIO bus.
            continue
        retained.append(line)
    updated = ''.join(retained)
    if updated == original:
        return False
    path.write_text(updated)
    return True


def prepare(path, converter='sunxi-fexc'):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('expected a regular staged script.bin')
    converter = shutil.which(converter)
    if not converter:
        raise RuntimeError('sunxi-fexc from sunxi-tools 1.4.2 or newer is required')
    original = path.read_bytes()
    metadata = path.stat()
    with tempfile.TemporaryDirectory(prefix='bkb-wifi-boot-') as tmp:
        tmp = Path(tmp)
        source, compiled, verified = tmp / 'script.fex', tmp / 'script.bin', tmp / 'verified.fex'
        subprocess.run([converter, '-I', 'bin', '-O', 'fex', str(path), str(source)], check=True)
        config = read_fex(source)
        enabled = config.get('wifi_para', 'wifi_used')
        if enabled not in ('0', '1'):
            raise ValueError('unexpected wifi_used value')
        if enabled == '1':
            return False
        config.set('wifi_para', 'wifi_used', '1')
        with source.open('w') as output:
            config.write(output)
        subprocess.run([converter, '-I', 'fex', '-O', 'bin', str(source), str(compiled)], check=True)
        subprocess.run([converter, '-I', 'bin', '-O', 'fex', str(compiled), str(verified)], check=True)
        if values(read_fex(verified)) != values(config):
            raise ValueError('boot config round-trip changed unrelated settings')
        if path.read_bytes() != original:
            raise ValueError('staged boot config changed during preparation')
        fd, replacement = tempfile.mkstemp(prefix='.wifi-boot-', dir=str(path.parent))
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(compiled.read_bytes())
                output.flush()
                os.fsync(output.fileno())
            os.chmod(replacement, metadata.st_mode & 0o777)
            os.replace(replacement, str(path))
        finally:
            if os.path.exists(replacement):
                os.unlink(replacement)
    return True


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--rootfs':
        prepare_modules(sys.argv[2])
    elif len(sys.argv) == 2:
        prepare(sys.argv[1])
    else:
        raise SystemExit('usage: prepare-image-wifi.py STAGED_SCRIPT_BIN | --rootfs ROOTFS')
