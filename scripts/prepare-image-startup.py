#!/usr/bin/env python3
"""Configure supported startup services in a staged Scripta rootfs."""
import os
from pathlib import Path
import shlex
import sys


def prepare(root):
    root = Path(root).resolve(strict=True)
    if root == Path('/') or not (root / 'etc').is_dir() or not (root / 'opt/scripta').is_dir():
        raise ValueError('refusing startup changes outside a staged Scripta rootfs')

    def path(relative):
        target = root / relative
        for parent in target.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError('refusing startup changes through a symlink: ' + relative)
        return target

    rc = path('etc/rc.local')
    if rc.is_symlink():
        raise ValueError('refusing a symlinked rc.local')
    updated = None
    if rc.exists():
        lines = rc.read_text().splitlines(keepends=True)
        retained = []
        for line in lines:
            if 'ntpdate' in line and not line.lstrip().startswith('#'):
                tokens = shlex.split(line, comments=True)
                if tokens != ['sudo', '/usr/sbin/ntpdate', '-b', '-u', '0.pool.ntp.org']:
                    raise ValueError('unexpected ntpdate command in rc.local')
                continue
            retained.append(line)
        updated = ''.join(retained)

    links = {
        'etc/systemd/system/ntp.service': '/dev/null',
        'etc/systemd/system/sendmail.service': '/dev/null',
        'etc/systemd/system/multi-user.target.wants/blakestream-system-perms.service':
            '../blakestream-system-perms.service',
        'etc/systemd/system/multi-user.target.wants/blakestream-firmware-firstboot.service':
            '../blakestream-firmware-firstboot.service',
        'etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service':
            '/lib/systemd/system/systemd-timesyncd.service',
    }
    targets = [(path(relative), value) for relative, value in links.items()]
    for target, value in targets:
        if target.exists() and not target.is_symlink():
            raise ValueError('refusing to replace a service override: ' + str(target))

    pending = path('opt/scripta/etc/bkb-firmware-firstboot.pending')
    if pending.is_symlink():
        raise ValueError('refusing a symlinked first-boot marker')

    if updated is not None and updated != rc.read_text():
        rc.write_text(updated)
    for target, value in targets:
        if target.is_symlink() and os.readlink(str(target)) == value:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        target.symlink_to(value)
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text('Verify installed bridge firmware once; never flash automatically.\n')
    pending.chmod(0o644)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: prepare-image-startup.py ROOTFS')
    prepare(sys.argv[1])
