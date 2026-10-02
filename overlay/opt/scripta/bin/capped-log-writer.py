#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Write stdin to a log file while keeping the file below a fixed size.

Used for RAM- and SD-backed sgminer logs. When the log crosses max_bytes, the
file is rewritten with a trim marker plus the last keep_bytes of content.
"""

from __future__ import print_function
import os
import grp
import sys
import time


# Leave headroom for clock/diagnostic histories on the 193 MiB controller.
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_KEEP_BYTES = 2 * 1024 * 1024


def to_bytes(s):
    if isinstance(s, bytes):
        return s
    return s.encode('utf-8', 'replace')


def as_int(value, default):
    try:
        value = int(value)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def ensure_parent(path):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if parent:
        try:
            gid = grp.getgrnam('www-data').gr_gid
            os.chown(parent, -1, gid)
        except Exception:
            pass
        try:
            os.chmod(parent, 0o775)
        except Exception:
            pass


def set_log_perms(path):
    try:
        gid = grp.getgrnam('www-data').gr_gid
        os.chown(path, -1, gid)
    except Exception:
        pass
    try:
        os.chmod(path, 0o664)
    except Exception:
        pass


def normalize_chunk(chunk):
    # Sgminer uses carriage returns to redraw status in a terminal. Logs should
    # keep each redraw as its own readable line.
    return chunk.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def trim_log(path, max_bytes, keep_bytes):
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    if size <= max_bytes:
        return size

    keep_bytes = min(keep_bytes, max_bytes)
    try:
        with open(path, 'rb') as src:
            if size > keep_bytes:
                src.seek(-keep_bytes, os.SEEK_END)
            data = src.read()

        marker = '===== %s log trimmed; keeping last %d bytes =====\n' % (
            time.strftime('%Y-%m-%d %H:%M:%S'), keep_bytes)
        with open(path, 'wb') as dst:
            dst.write(to_bytes(marker))
            dst.write(data)
        set_log_perms(path)
        return os.path.getsize(path)
    except Exception:
        return size


def main():
    if len(sys.argv) < 2:
        sys.stderr.write('usage: capped-log-writer.py PATH [MAX_BYTES] [KEEP_BYTES]\n')
        return 2

    path = sys.argv[1]
    max_bytes = as_int(sys.argv[2] if len(sys.argv) > 2 else None, DEFAULT_MAX_BYTES)
    keep_bytes = as_int(sys.argv[3] if len(sys.argv) > 3 else None, DEFAULT_KEEP_BYTES)
    if keep_bytes >= max_bytes:
        keep_bytes = max_bytes // 2

    ensure_parent(path)
    size = trim_log(path, max_bytes, keep_bytes)
    out = open(path, 'ab', 0)
    set_log_perms(path)

    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 8192)
            if not chunk:
                break
            chunk = normalize_chunk(chunk)
            out.write(chunk)
            size += len(chunk)
            if size > max_bytes:
                out.close()
                size = trim_log(path, max_bytes, keep_bytes)
                out = open(path, 'ab', 0)
    finally:
        out.close()

    return 0


if __name__ == '__main__':
    sys.exit(main())
