#!/usr/bin/env python3
"""Set release-image radio defaults before NetworkManager's first boot."""
import configparser
import os
from pathlib import Path
import sys


def checked_path(root, relative):
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('refusing network state through a symlink')
    return current


def prepare(root):
    root = Path(root).resolve(strict=True)
    if root == Path('/') or not (root / 'etc').is_dir() or not (root / 'opt/scripta').is_dir():
        raise ValueError('expected a staged Scripta rootfs')
    state = checked_path(root, 'var/lib/NetworkManager/NetworkManager.state')
    profiles = checked_path(root, 'etc/NetworkManager/system-connections')
    if profiles.is_dir():
        for path in profiles.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError('unexpected NetworkManager profile entry')
            profile = configparser.ConfigParser(interpolation=None, strict=False)
            with path.open() as handle:
                profile.read_file(handle)
            if profile.get('connection', 'type', fallback='') in ('802-11-wireless', 'wifi'):
                path.unlink()
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if state.exists():
        with state.open() as handle:
            config.read_file(handle)
    if not config.has_section('main'):
        config.add_section('main')
    config.set('main', 'NetworkingEnabled', 'true')
    config.set('main', 'WirelessEnabled', 'false')
    state.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(state), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        os.fchmod(handle.fileno(), 0o600)
        config.write(handle)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: prepare-image-network.py STAGED_ROOTFS')
    prepare(sys.argv[1])
