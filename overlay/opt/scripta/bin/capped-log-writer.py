#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Write stdin to a log file while keeping the file below a fixed size.

Used for RAM- and SD-backed sgminer logs. Trim before appending past the cap.
Production capture retries briefly, then drains/drops on output failure so the
miner can keep running. Counts are normalized log bytes not accepted by write,
excluding successfully written prefixes and the normal capped-tail eviction.
At most one input chunk and a <=256-byte loss marker are pending; no spool.
The published log stays <=4 MiB by default. Atomic trimming additionally needs
one <=2 MiB tail plus a short marker on the SAME filesystem (about 6 MiB peak
total); Python buffers remain <=8 KiB. Scratch is reused after process death.
"""

from __future__ import print_function
import os
import grp
import sys
import time
import errno
import fcntl
import shlex
import signal
import stat
import syslog


# Leave headroom for clock/diagnostic histories on the 193 MiB controller.
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_KEEP_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 8192
OUTPUT_FAILURE_LIMIT = 4
OUTPUT_PROBE_SECONDS = 1.0
WARNING_SECONDS = 60.0
LOSS_MARKER_BYTES = 256
LOG_PATHS = ('/run/blakestream/sgminer.log', '/var/log/sgminer.log')
IO_ERRORS = (IOError, OSError, MemoryError)
# Python 2.7 does not export O_ACCMODE on all firmware/build hosts.
ACCESS_MODE = os.O_WRONLY | os.O_RDWR


def report(message):
    # stderr may belong to a detached screen. Keep failures in the system log
    # as well, without spawning logger or including captured miner output.
    try:
        syslog.openlog('blakestream-miner')
        syslog.syslog(syslog.LOG_WARNING, 'log capture: ' + message)
    except Exception:
        pass
    try:
        sys.stderr.write('log capture: ' + message + '\n')
        sys.stderr.flush()
    except Exception:
        pass


class Retry(object):
    def __init__(self, enabled=True, failure_limit=None):
        self.enabled = enabled
        self.failure_limit = failure_limit
        self.failures = 0
        self.operation = 'output'

    def call(self, operation, function, *args):
        self.operation = operation
        delay = 0.1
        last_report = None
        while True:
            try:
                result = function(*args)
                if last_report is not None:
                    report(operation + ' recovered')
                return result
            except IO_ERRORS as error:
                if getattr(error, 'errno', None) == errno.EINTR:
                    continue
                # Bad descriptors/programming errors need a visible failure,
                # not an immortal reader which cannot make progress.
                if not self.enabled or getattr(error, 'errno', None) in (errno.EBADF, errno.EINVAL):
                    raise
                if self.failure_limit is not None:
                    self.failures += 1
                    if self.failures >= self.failure_limit:
                        raise
                    time.sleep(0.01 * (2 ** (self.failures - 1)))
                    continue
                now = time.time()
                if last_report is None or now - last_report >= 60:
                    report('%s failed (%s errno=%s); retrying, input paused' %
                           (operation, type(error).__name__, getattr(error, 'errno', None)))
                    last_report = now
                time.sleep(delay)
                delay = min(delay * 2, 5.0)


def protect_log_writer(proc_dir='/proc/self'):
    # Log capture is part of the miner pipeline, not a disposable collector.
    for name, score in (('oom_score_adj', '-900'), ('oom_adj', '-16')):
        path = os.path.join(proc_dir, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'w') as handle:
                handle.write(score)
            return True
        except IO_ERRORS:
            pass
    return False


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
    except OSError as error:
        if error.errno == errno.ENOENT:
            return 0
        raise
    if size <= max_bytes:
        return size

    marker = to_bytes('===== %s log trimmed; keeping last %d bytes =====\n' % (
        time.strftime('%Y-%m-%d %H:%M:%S'), keep_bytes))[:max_bytes]
    keep_bytes = min(keep_bytes, max(0, max_bytes - len(marker)))
    # One reserved scratch file bounds disk use even across process death.
    # The destination lock held by capture serializes trims and appends.
    temporary = os.path.join(os.path.dirname(path), '.' + os.path.basename(path) + '.trim')
    created = False
    try:
        with open(path, 'rb') as src:
            src.seek(size - keep_bytes)
            fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            created = True
            try:
                write_all(fd, marker)
                remaining = keep_bytes
                while remaining:
                    data = src.read(min(CHUNK_BYTES, remaining))
                    if not data:
                        raise IOError(errno.EIO, 'log changed during trim')
                    write_all(fd, data)
                    remaining -= len(data)
                os.fsync(fd)
                set_log_perms(temporary)
            finally:
                os.close(fd)
        # Only a complete, synced tail replaces the original. No multi-MiB
        # Python allocation, and a failed trim cannot truncate the evidence.
        os.rename(temporary, path)
        created = False
        return len(marker) + keep_bytes
    finally:
        if created:
            os.unlink(temporary)


def write_all(fd, data, retry=None):
    offset = 0
    while offset < len(data):
        if retry is None:
            written = write_piece(fd, data, offset)
        else:
            written = retry.call('write', write_piece, fd, data, offset)
        offset += written


def write_piece(fd, data, offset):
    written = os.write(fd, data[offset:])
    if written == 0:
        raise IOError(errno.EIO, 'zero-byte log write')
    return written


def lock_fd(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except IOError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def capture_time():
    return getattr(time, 'monotonic', time.time)()


class DestinationBusy(Exception):
    pass


class LogOutput(object):
    def __init__(self, path, max_bytes, keep_bytes, retry_enabled=True):
        self.path = path
        self.maximum = max_bytes
        self.keep = keep_bytes
        self.retry_enabled = retry_enabled
        self.lock = None
        self.failed = False
        self.dropped = 0
        self.total_dropped = 0
        self.last_probe = 0
        self.next_probe = 0
        self.last_warning = None
        self.failure = 'output unavailable'

    def retry(self):
        return Retry(self.retry_enabled, OUTPUT_FAILURE_LIMIT)

    def prepare(self, retry):
        if self.lock is not None:
            return
        retry.call('create log directory', ensure_parent, self.path)
        lock = retry.call('open log lock', os.open, self.path + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if not retry.call('lock log', lock_fd, lock):
                raise DestinationBusy()
        except Exception:
            os.close(lock)
            raise
        self.lock = lock

    def discard(self, count):
        self.dropped += count
        self.total_dropped += count
        now = capture_time()
        if self.last_warning is None or now < self.last_warning or now - self.last_warning >= WARNING_SECONDS:
            report('%s; draining; dropped_log_bytes=%d total_dropped_log_bytes=%d' %
                   (self.failure, self.dropped, self.total_dropped))
            self.last_warning = now

    def failed_output(self, retry, error, unwritten):
        if not self.retry_enabled:
            raise
        self.failed = True
        self.last_probe = capture_time()
        self.next_probe = self.last_probe + OUTPUT_PROBE_SECONDS
        self.failure = '%s failed (%s errno=%s)' % (retry.operation, type(error).__name__, getattr(error, 'errno', None))
        self.discard(unwritten)

    def start(self):
        retry = self.retry()
        try:
            self.prepare(retry)
            retry.call('initial trim', trim_log, self.path, self.maximum, self.keep)
        except IO_ERRORS as error:
            self.failed_output(retry, error, 0)

    def offer(self, chunk, force=False):
        now = capture_time()
        if self.failed and not force and self.last_probe <= now < self.next_probe:
            self.discard(len(chunk))
            return
        retry = self.retry()
        out = None
        written = 0
        try:
            self.prepare(retry)
            marker = b''
            if self.dropped:
                marker = to_bytes('\n===== log capture gap: dropped_log_bytes=%d total_dropped_log_bytes=%d =====\n' %
                                  (self.dropped, self.total_dropped))[:min(LOSS_MARKER_BYTES, self.maximum - len(chunk))]
            # Reserve space for BOTH marker and this chunk. A marker must not
            # consume the next chunk or cause an append beyond the cap.
            retry.call('trim', trim_log, self.path, self.maximum - len(marker) - len(chunk), self.keep)
            out = retry.call('open log', os.open, self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o664)
            set_log_perms(self.path)
            if marker:
                write_all(out, marker, retry)
            if self.failed or self.dropped:
                report('output recovered; dropped_log_bytes=%d total_dropped_log_bytes=%d' %
                       (self.dropped, self.total_dropped))
                self.dropped = 0
                self.failed = False
            while written < len(chunk):
                written += retry.call('write', write_piece, out, chunk, written)
        except IO_ERRORS as error:
            self.failed_output(retry, error, len(chunk) - written)
        finally:
            if out is not None:
                try:
                    os.close(out)
                except IO_ERRORS as error:
                    # Linux releases the fd even on close(EINTR). Do not
                    # retry a close or miscount an acknowledged write as lost.
                    if getattr(error, 'errno', None) != errno.EINTR:
                        retry.operation = 'close log (durability uncertain)'
                        self.failed_output(retry, error, 0)

    def finish(self):
        if self.failed or self.dropped:
            self.offer(b'', force=True)
        if self.total_dropped:
            report('input ended; unreported_dropped_log_bytes=%d total_dropped_log_bytes=%d' %
                   (self.dropped, self.total_dropped))

    def close(self):
        if self.lock is not None:
            os.close(self.lock)


def capture(path, max_bytes, keep_bytes, input_fd=0, retry_enabled=True):
    retry = Retry(retry_enabled)
    protect_log_writer()
    if stat.S_ISFIFO(os.fstat(input_fd).st_mode) and not retry.call('lock pipe', lock_fd, input_fd):
        report('pipe already owned; refusing a second consumer')
        return 1
    output = LogOutput(path, max_bytes, keep_bytes, retry_enabled)
    try:
        output.start()
        read_size = min(CHUNK_BYTES, max(1, max_bytes // 2))
        while True:
            raw = retry.call('read', os.read, input_fd, read_size)
            if not raw:
                output.finish()
                return 0
            budget = output.retry()
            try:
                chunk = budget.call('normalize', normalize_chunk, raw)
            except IO_ERRORS as error:
                output.failed_output(budget, error, len(raw) - raw.count(b'\r\n'))
                continue
            output.offer(chunk)
    except DestinationBusy:
        report('destination already owned; refusing a second writer')
        return 1
    finally:
        output.close()


def read_proc(path):
    with open(path, 'rb') as handle:
        data = handle.read(65537)
    if len(data) > 65536:
        raise ValueError('oversized proc metadata')
    return data.decode('utf-8', 'replace')


def process_identity(pid, proc_root):
    base = os.path.join(proc_root, str(pid))
    fields = read_proc(base + '/stat').rsplit(') ', 1)[1].split()
    return fields[1], fields[19], os.readlink(base + '/exe')


def active_log_config(pid, identity, proc_root):
    base = os.path.join(proc_root, str(pid))
    env = dict(item.split('=', 1) for item in read_proc(base + '/environ').split('\0') if '=' in item)
    if 'BS_SGMINER_LOG' in env:
        values = [env['BS_SGMINER_LOG'], env.get('BS_SGMINER_LOG_MAX', ''),
                  env.get('BS_SGMINER_LOG_KEEP', '')]
    else:
        # Legacy pipelines have no environment marker. Accept only the exact
        # known writer invocation in the still-running parent bash pipeline.
        command = read_proc(os.path.join(proc_root, identity[0], 'cmdline')).split('\0')
        if len(command) < 3 or command[1] != '-c':
            raise ValueError('cannot establish active log destination')
        words = shlex.split(command[2])
        tail = words[words.index('|') + 1:]
        if len(tail) != 5 or tail[:2] != ['/usr/bin/python', os.path.abspath(__file__)]:
            raise ValueError('unrecognized log pipeline')
        values = tail[2:]
    path, maximum, keep = values[0], int(values[1]), int(values[2])
    if path not in LOG_PATHS or maximum <= 0 or not 0 < keep < maximum:
        raise ValueError('invalid active log destination or limits')
    return path, maximum, keep


def pipe_readers(pipe, proc_root='/proc'):
    # Check every descriptor, not just stdin or a process-name match: older
    # recovery services do not participate in our flock protocol. Any unknown
    # reader or incomplete proc inspection makes recovery fail closed.
    for pid in os.listdir(proc_root):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        base = os.path.join(proc_root, pid)
        try:
            descriptors = os.listdir(base + '/fd')
        except OSError as error:
            if error.errno == errno.ENOENT:
                continue
            raise
        for descriptor in descriptors:
            try:
                if os.readlink(base + '/fd/' + descriptor) != pipe:
                    continue
                fields = dict(line.split(':', 1) for line in
                              read_proc(base + '/fdinfo/' + descriptor).splitlines() if ':' in line)
                flags = int(fields['flags'].strip(), 8)
                if flags & ACCESS_MODE != os.O_WRONLY:
                    return True
            except (IOError, OSError) as error:
                if error.errno != errno.ENOENT:
                    raise
    return False


def recover(pid, executable, proc_root='/proc', daemon=True):
    identity = process_identity(pid, proc_root)
    if identity[2] != os.path.realpath(executable):
        raise ValueError('miner executable changed')
    base = os.path.join(proc_root, str(pid), 'fd')
    pipe = os.readlink(base + '/1')
    if not pipe.startswith('pipe:[') or os.readlink(base + '/2') != pipe:
        raise ValueError('miner stdout/stderr are not the same pipe')
    if pipe_readers(pipe, proc_root):
        return 0
    path, maximum, keep = active_log_config(pid, identity, proc_root)
    fd = os.open(base + '/1', os.O_RDONLY | os.O_NONBLOCK)
    try:
        if ('pipe:[%d]' % os.fstat(fd).st_ino != pipe or
                process_identity(pid, proc_root) != identity or
                os.readlink(base + '/1') != pipe or os.readlink(base + '/2') != pipe):
            raise ValueError('miner or pipe changed during recovery')
        if not lock_fd(fd) or pipe_readers(pipe, proc_root):
            return 0
        if process_identity(pid, proc_root) != identity:
            raise ValueError('miner changed during reader inspection')
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        if daemon:
            child = os.fork()
            if child:
                return 0
            try:
                os.setsid()
                signal.signal(signal.SIGHUP, signal.SIG_IGN)
                os.dup2(fd, 0)
                null = os.open(os.devnull, os.O_RDWR)
                os.dup2(null, 1)
                os.dup2(null, 2)
                # Do not keep the launcher's restart flock or cron pipes alive.
                for descriptor in os.listdir('/proc/self/fd'):
                    number = int(descriptor)
                    if number > 2:
                        try:
                            os.close(number)
                        except OSError:
                            pass
                report('reattached to miner pid=%s %s; prior in-flight bytes may be lost' % (pid, pipe))
                result = capture(path, maximum, keep)
            except Exception as error:
                report_failure('recovery stopped', error)
                result = 1
            os._exit(result)
        return capture(path, maximum, keep, fd)
    finally:
        os.close(fd)


def report_failure(operation, error):
    # Retain the failing code location in syslog even if screen/journal later
    # loses stderr. Do not include exception payloads or captured log data.
    trace = sys.exc_info()[2]
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    location = ('%s:%d' % (os.path.basename(trace.tb_frame.f_code.co_filename), trace.tb_lineno)
                if trace is not None else 'unknown')
    report('%s: %s errno=%s at %s; pending bytes may be lost' %
           (operation, type(error).__name__, getattr(error, 'errno', None), location))


def main():
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == '--recover':
        return recover(int(args[1]), args[2])
    retry_enabled = True
    if args and args[0] == '--once':
        # Synchronous startup transfers must return failure to their caller,
        # which retains the source log, rather than hold its restart lock.
        retry_enabled = False
        args = args[1:]
    if not args or args[0].startswith('--'):
        sys.stderr.write('usage: capped-log-writer.py PATH [MAX_BYTES] [KEEP_BYTES]\n'
                         '       capped-log-writer.py --once PATH [MAX_BYTES] [KEEP_BYTES]\n'
                         '       capped-log-writer.py --recover PID MINER_EXECUTABLE\n')
        return 2

    path = args[0]
    max_bytes = as_int(args[1] if len(args) > 1 else None, DEFAULT_MAX_BYTES)
    keep_bytes = as_int(args[2] if len(args) > 2 else None, DEFAULT_KEEP_BYTES)
    if keep_bytes >= max_bytes:
        keep_bytes = max_bytes // 2

    return capture(path, max_bytes, keep_bytes, sys.stdin.fileno(), retry_enabled)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        report_failure('capture stopped', error)
        raise
