#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Provision a strong per-device seed on Linux 3.4/systemd 229."""
from __future__ import print_function

import errno
import fcntl
import os
import select
import stat
import struct
import subprocess
import sys
import tempfile

RNDADDENTROPY = 0x40085203
BYTE_COUNT = 32
SEED = '/var/lib/systemd/random-seed'
STATE_DIR = '/var/lib/blakestream-identity'
MARKER = STATE_DIR + '/entropy-provisioned'


def fail(message):
    raise RuntimeError(message)


def write_all(fd, data):
    offset = 0
    while offset < len(data):
        try:
            written = os.write(fd, data[offset:])
        except OSError as error:
            if error.errno == errno.EINTR:
                continue
            raise
        if written <= 0:
            fail('zero-byte entropy write')
        offset += written


def entropy_available():
    try:
        with open('/proc/sys/kernel/random/entropy_avail', 'r') as handle:
            return handle.read(32).strip()
    except IOError:
        return 'unknown'


def collect_exact_random():
    fd = os.open('/dev/random', os.O_RDWR)
    pieces = []
    total = 0
    try:
        while total < BYTE_COUNT:
            ready, _, _ = select.select([fd], [], [], 10.0)
            if not ready:
                sys.stderr.write('device identity: waiting for entropy (%s bits available)\n' %
                                 entropy_available())
                sys.stderr.flush()
                continue
            try:
                chunk = os.read(fd, BYTE_COUNT - total)
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                raise
            if not chunk:
                fail('unexpected EOF from /dev/random')
            pieces.append(chunk)
            total += len(chunk)
        data = b''.join(pieces)
        if len(data) != BYTE_COUNT:
            fail('short entropy accumulation')
        # Linux 3.4 keeps separate input and nonblocking pools. Credit the
        # accounted bytes back to the input pool and explicitly mix them into
        # the pool consumed by OpenSSH/OpenSSL.
        payload = struct.pack('ii', BYTE_COUNT * 8, BYTE_COUNT) + data
        fcntl.ioctl(fd, RNDADDENTROPY, payload)
    finally:
        os.close(fd)

    urandom = os.open('/dev/urandom', os.O_WRONLY)
    try:
        write_all(urandom, data)
    finally:
        os.close(urandom)


def fsync_path(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_size < BYTE_COUNT:
            fail('unsafe or incomplete random seed')
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def write_marker():
    if os.path.islink(STATE_DIR) or not os.path.isdir(STATE_DIR):
        fail('unsafe identity state directory')
    info = os.stat(STATE_DIR)
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
        fail('identity state directory is not root-only')
    fd, temporary = tempfile.mkstemp(prefix='.entropy-provisioned.', dir=STATE_DIR)
    try:
        write_all(fd, b'256 bits collected from blocking /dev/random\n')
        os.fchmod(fd, 0o600)
        os.fchown(fd, 0, 0)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.rename(temporary, MARKER)
        temporary = None
        parent = os.open(STATE_DIR, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        fail('must run as root without arguments')
    collect_exact_random()
    subprocess.check_call(['/lib/systemd/systemd-random-seed', 'save'])
    os.chown(SEED, 0, 0)
    os.chmod(SEED, 0o600)
    fsync_path(SEED)
    write_marker()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        sys.stderr.write('device identity entropy: %s\n' % error)
        sys.exit(1)
