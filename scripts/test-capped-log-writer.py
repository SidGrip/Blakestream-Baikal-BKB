#!/usr/bin/env python
"""Focused Python 2.7/3 log-capture tests; fixtures only, never a live miner."""
from __future__ import print_function
import contextlib
import errno
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRITER = os.path.join(ROOT, 'overlay/opt/scripta/bin/capped-log-writer.py')
if sys.version_info[0] == 2:
    import imp
    L = imp.load_source('capped_log_writer', WRITER)
else:
    import importlib.util
    SPEC = importlib.util.spec_from_file_location('capped_log_writer', WRITER)
    L = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(L)
# Python 2 imp may expose a cached .pyc filename on repeat runs. Production
# executes the .py script directly; keep the legacy pipeline fixture faithful.
L.__file__ = WRITER

MISSING = object()


@contextlib.contextmanager
def patch(obj, name, value):
    original = getattr(obj, name, MISSING)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if original is MISSING:
            delattr(obj, name)
        else:
            setattr(obj, name, original)


def read(path):
    with open(path, 'rb') as handle:
        return handle.read()


def write(path, data):
    with open(path, 'wb') as handle:
        handle.write(data)


class FixtureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='capped-log-test-')
        self.path = os.path.join(self.tmp, 'sgminer.log')
        self.messages = []
        self.delays = []
        self.patches = [patch(L, 'report', self.messages.append),
                        patch(L, 'protect_log_writer', lambda: True)]
        for item in self.patches:
            item.__enter__()
        self.input_fd = os.open(os.devnull, os.O_RDONLY)

    def tearDown(self):
        os.close(self.input_fd)
        for item in reversed(self.patches):
            item.__exit__(None, None, None)
        shutil.rmtree(self.tmp)

    def capture(self, events, maximum=8192, keep=4096):
        def source(fd, size):
            self.assertEqual(fd, self.input_fd)
            self.assertLessEqual(size, L.CHUNK_BYTES)
            event = events.pop(0)
            if isinstance(event, Exception):
                raise event
            self.assertLessEqual(len(event), size)
            return event
        with patch(L.os, 'read', source), patch(L.time, 'sleep', self.delays.append):
            result = L.capture(self.path, maximum, keep, self.input_fd)
        self.assertEqual(events, [])
        return result


class LoggingTests(FixtureBase):
    def test_read_transients_and_memory_error_recover(self):
        events = [OSError(errno.EINTR, 'injected'), OSError(errno.ENOMEM, 'injected'),
                  MemoryError(), OSError(errno.EAGAIN, 'injected'), b'kept\r\nline\r', b'']
        self.assertEqual(self.capture(events), 0)
        self.assertEqual(read(self.path), b'kept\nline\n')
        self.assertEqual(len(self.delays), 3)
        self.assertTrue(any('read recovered' in message for message in self.messages))

    def test_normalization_retry_keeps_raw_chunk(self):
        original = L.normalize_chunk
        calls = []
        def normalize(data):
            calls.append(data)
            if len(calls) == 1:
                raise MemoryError()
            return original(data)
        with patch(L, 'normalize_chunk', normalize):
            self.capture([b'one\rtwo', b''])
        self.assertEqual(calls, [b'one\rtwo', b'one\rtwo'])
        self.assertEqual(read(self.path), b'one\ntwo')

    def test_open_retry_preserves_pending_input(self):
        original = L.os.open
        failures = [errno.ENOMEM, errno.EMFILE, errno.ENOSPC]
        def open_log(path, flags, mode=0o777):
            if path == self.path and failures:
                raise OSError(failures.pop(0), 'injected')
            return original(path, flags, mode)
        with patch(L.os, 'open', open_log):
            self.capture([b'pending bytes', b''])
        self.assertEqual(read(self.path), b'pending bytes')
        self.assertEqual(len(self.delays), 3)

    def test_partial_zero_and_failed_writes_never_duplicate_or_drop(self):
        original = L.os.write
        events = [3, OSError(errno.EINTR, 'injected'), MemoryError(),
                  OSError(errno.ENOSPC, 'injected'), 0, 2]
        def output(fd, data):
            event = events.pop(0) if events else len(data)
            if isinstance(event, Exception):
                raise event
            return original(fd, data[:event]) if event else 0
        with patch(L.os, 'write', output):
            self.capture([b'abcdefghijk', b''])
        self.assertEqual(read(self.path), b'abcdefghijk')
        self.assertEqual(len(self.delays), 3)

    def test_eintr_is_immediate_and_silent_including_once_mode(self):
        for enabled in (True, False):
            remaining = [3]
            def interrupted():
                if remaining[0]:
                    remaining[0] -= 1
                    raise OSError(errno.EINTR, 'injected')
                return 7
            retry = L.Retry(enabled, L.OUTPUT_FAILURE_LIMIT)
            with patch(L.time, 'sleep', self.delays.append):
                self.assertEqual(retry.call('fixture', interrupted), 7)
            self.assertEqual(retry.failures, 0)
        self.assertEqual(self.delays, [])
        self.assertEqual(self.messages, [])

    def test_output_failure_budget_is_shared_across_operations(self):
        retry = L.Retry(True, L.OUTPUT_FAILURE_LIMIT)
        failures = [0]
        def intermittent():
            failures[0] += 1
            if failures[0] % 2:
                raise OSError(errno.EIO, 'injected')
        with patch(L.time, 'sleep', self.delays.append):
            for unused in range(3):
                retry.call('fixture', intermittent)
            with self.assertRaises(OSError):
                retry.call('fixture', intermittent)
        self.assertEqual(retry.failures, L.OUTPUT_FAILURE_LIMIT)
        self.assertAlmostEqual(sum(self.delays), 0.07)
        self.assertEqual(self.messages, [])

    def test_retry_delay_is_bounded_and_reports_are_rate_limited(self):
        clock = [0.0]
        calls = [0]
        def wait(delay):
            clock[0] += delay
            self.delays.append(delay)
        def operation():
            calls[0] += 1
            if calls[0] < 40:
                raise OSError(errno.ENOSPC, 'injected')
            return 7
        with patch(L.time, 'sleep', wait), patch(L.time, 'time', lambda: clock[0]):
            self.assertEqual(L.Retry().call('fixture', operation), 7)
        self.assertLessEqual(max(self.delays), 5)
        self.assertLess(len(self.messages), 6)
        self.assertIn('input paused', self.messages[0])
        self.assertEqual(self.messages[-1], 'fixture recovered')

    def test_bad_descriptor_is_not_retried_forever(self):
        with self.assertRaises(OSError):
            self.capture([OSError(errno.EBADF, 'injected')])
        self.assertEqual(self.delays, [])

    def test_one_shot_startup_write_fails_promptly_and_reports_location(self):
        def unavailable(*args):
            raise OSError(errno.ENOSPC, 'private exception payload')
        with patch(L.os, 'open', unavailable), patch(L.time, 'sleep', self.delays.append):
            try:
                L.capture(self.path, 8192, 4096, self.input_fd, retry_enabled=False)
                self.fail('one-shot failure was suppressed')
            except OSError as error:
                L.report_failure('fixture stopped', error)
        self.assertEqual(self.delays, [])
        self.assertIn('test-capped-log-writer.py:', self.messages[-1])
        self.assertIn('errno=28', self.messages[-1])
        self.assertNotIn('private exception payload', self.messages[-1])

    def test_cli_once_mode_and_invalid_recovery_arguments(self):
        loader = ("import imp\nL=imp.load_source('writer',%r)\n" % WRITER if sys.version_info[0] == 2 else
                  "import importlib.util\ns=importlib.util.spec_from_file_location('writer',%r)\nL=importlib.util.module_from_spec(s)\ns.loader.exec_module(L)\n" % WRITER)
        harness = (loader +
                   "L.LOG_PATHS=(%r,)\nL.protect_log_writer=lambda: True\n"
                   "import sys\nsys.argv=[%r,'--once',%r,'8192','4096']\nsys.exit(L.main())\n" %
                   (self.path, WRITER, self.path))
        process = subprocess.Popen([sys.executable, '-c', harness],
                                   stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        unused, errors = process.communicate(b'one-shot bytes\n')
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(read(self.path), b'one-shot bytes\n')
        process = subprocess.Popen([sys.executable, WRITER, '--recover'], stderr=subprocess.PIPE)
        unused, errors = process.communicate()
        self.assertEqual(process.returncode, 2)
        self.assertIn(b'usage:', errors)

    def test_failed_trim_preserves_original_at_every_io_stage(self):
        original = b'old\n' * 4096 + b'last evidence\n'
        for name in ('open', 'write', 'fsync', 'rename'):
            write(self.path, original)
            function = getattr(L.os, name)
            def fail(*args):
                raise OSError(errno.ENOSPC, 'injected')
            with patch(L.os, name, fail):
                with self.assertRaises(OSError):
                    L.trim_log(self.path, 8192, 4096)
            self.assertEqual(read(self.path), original, name)
            self.assertEqual(os.listdir(self.tmp), ['sgminer.log'])
            self.assertIs(getattr(L.os, name), function)

    def test_trim_source_open_and_read_allocation_failure_preserve_evidence(self):
        original = b'evidence\n' * 3000
        write(self.path, original)
        def no_memory(*args):
            raise MemoryError()
        with patch(L, 'open', no_memory):
            with self.assertRaises(MemoryError):
                L.trim_log(self.path, 8192, 4096)
        self.assertEqual(read(self.path), original)
        class BadRead(object):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def seek(self, *args):
                pass
            def read(self, *args):
                raise MemoryError()
        with patch(L, 'open', lambda *args: BadRead()):
            with self.assertRaises(MemoryError):
                L.trim_log(self.path, 8192, 4096)
        self.assertEqual(read(self.path), original)
        self.assertEqual(os.listdir(self.tmp), ['sgminer.log'])

    def test_transient_trim_failure_preserves_pending_chunk_and_cap(self):
        write(self.path, b'x' * 8100)
        rename = L.os.rename
        failures = [errno.ENOSPC, errno.EIO, errno.ENOMEM]
        calls = []
        def replace(source, target):
            calls.append(os.path.getsize(target))
            if failures:
                raise OSError(failures.pop(0), 'injected')
            return rename(source, target)
        with patch(L.os, 'rename', replace):
            self.capture([b'new evidence\n' * 20, b''])
        self.assertEqual(calls, [8100] * 4)
        self.assertTrue(read(self.path).endswith(b'new evidence\n' * 20))
        self.assertLessEqual(os.path.getsize(self.path), 8192)

    def test_cap_holds_after_every_write_and_trim_uses_small_reads(self):
        original_write = L.os.write
        observed = []
        def output(fd, data):
            count = original_write(fd, data)
            observed.append(os.fstat(fd).st_size)
            self.assertLessEqual(os.fstat(fd).st_size, 8192)
            return count
        with patch(L.os, 'write', output):
            self.capture([b'x' * 4096 for unused in range(30)] + [b'latest\n', b''])
        self.assertTrue(read(self.path).endswith(b'latest\n'))
        self.assertTrue(observed)
        self.assertIn(b'log trimmed;', read(self.path))

    def test_four_mib_defaults_stream_tail_in_eight_kib_blocks(self):
        self.assertEqual((L.DEFAULT_MAX_BYTES, L.DEFAULT_KEEP_BYTES), (4194304, 2097152))
        write(self.path, b'older' * 900000 + b'last evidence')
        real_open = open
        sizes = []
        class Source(object):
            def __enter__(self):
                self.file = real_open(self_path, 'rb')
                return self
            def __exit__(self, *args):
                self.file.close()
            def seek(self, *args):
                return self.file.seek(*args)
            def read(self, size):
                sizes.append(size)
                if size > 8192:
                    raise AssertionError('unbounded allocation')
                return self.file.read(size)
        self_path = self.path
        with patch(L, 'open', lambda *args: Source()):
            size = L.trim_log(self.path, L.DEFAULT_MAX_BYTES, L.DEFAULT_KEEP_BYTES)
        self.assertLessEqual(size, L.DEFAULT_MAX_BYTES)
        self.assertTrue(read(self.path).endswith(b'last evidence'))
        self.assertEqual(sum(sizes), L.DEFAULT_KEEP_BYTES)

    def test_crash_scratch_is_reused_and_small_caps_include_marker(self):
        scratch = os.path.join(self.tmp, '.sgminer.log.trim')
        write(scratch, b'incomplete prior trim')
        write(self.path, b'x' * 1000 + b'end')
        self.assertLessEqual(L.trim_log(self.path, 100, 100), 100)
        self.assertFalse(os.path.exists(scratch))
        self.assertTrue(read(self.path).endswith(b'end'))

    def test_persistent_trim_failure_drains_counts_and_preserves_original(self):
        original = b'evidence' * 1024
        write(self.path, original)
        def failure(*args):
            raise OSError(errno.ENOSPC, 'injected')
        with patch(L.os, 'rename', failure), patch(L, 'capture_time', lambda: 0):
            self.assertEqual(self.capture([b'pending' * 100] * 100 + [b'']), 0)
        self.assertEqual(read(self.path), original)
        self.assertLessEqual(sum(self.delays), 0.15)
        self.assertIn('total_dropped_log_bytes=70000', self.messages[-1])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, '.sgminer.log.trim')))

    def test_partial_write_drop_count_and_next_chunk_survive_recovery(self):
        output = L.LogOutput(self.path, 8192, 4096)
        original = L.os.write
        mode = ['prefix']
        now = [0.0]
        def writer(fd, data):
            if mode[0] == 'prefix':
                mode[0] = 'failed'
                return original(fd, data[:3])
            if mode[0] == 'failed':
                raise OSError(errno.ENOSPC, 'injected')
            return original(fd, data)
        try:
            with patch(L.os, 'write', writer), patch(L, 'capture_time', lambda: now[0]), patch(L.time, 'sleep', self.delays.append):
                output.start()
                output.offer(b'abcDEFG')
                output.offer(b'12\n')
                self.assertEqual(output.total_dropped, 7)
                self.assertEqual(read(self.path), b'abc')
                mode[0] = 'healthy'
                now[0] = 1.1
                output.offer(b'next chunk\n')
                self.assertEqual(output.dropped, 0)
                self.assertEqual(output.total_dropped, 7)
            result = read(self.path)
            self.assertTrue(result.startswith(b'abc\n====='))
            self.assertIn(b'dropped_log_bytes=7 total_dropped_log_bytes=7', result)
            self.assertTrue(result.endswith(b'next chunk\n'))
            self.assertTrue(any('output recovered; dropped_log_bytes=7' in row for row in self.messages))
        finally:
            output.close()

    def test_partial_loss_marker_does_not_count_marker_bytes_or_reset_losses(self):
        write(self.path, b'x' * L.DEFAULT_MAX_BYTES)
        output = L.LogOutput(self.path, L.DEFAULT_MAX_BYTES, L.DEFAULT_KEEP_BYTES)
        original = L.os.write
        mode = ['failed']
        now = [0.0]
        def bounded_write(fd, data):
            written = original(fd, data)
            self.assertLessEqual(os.fstat(fd).st_size, L.DEFAULT_MAX_BYTES)
            return written
        def writer(fd, data):
            # Allow the atomic trim; inject failure only into the actual loss
            # marker, not into the scratch file's unrelated trim marker.
            if os.readlink('/proc/self/fd/%d' % fd) != self.path:
                return bounded_write(fd, data)
            if mode[0] == 'partial marker':
                mode[0] = 'failed'
                return bounded_write(fd, data[:5])
            if mode[0] == 'failed':
                raise OSError(errno.ENOSPC, 'injected')
            return bounded_write(fd, data)
        try:
            with patch(L.os, 'write', writer), patch(L, 'capture_time', lambda: now[0]), patch(L.time, 'sleep', self.delays.append):
                output.start()
                output.offer(b'first')
                self.assertEqual(output.dropped, 5)
                now[0] = 1.1
                mode[0] = 'partial marker'
                output.offer(b'next')
                self.assertEqual(output.dropped, 9)
                self.assertEqual(output.total_dropped, 9)
                self.assertTrue(output.failed)
                self.assertFalse(any('output recovered' in row for row in self.messages))
                self.assertLessEqual(os.path.getsize(self.path), L.DEFAULT_MAX_BYTES)
                now[0] = 2.2
                mode[0] = 'healthy'
                output.offer(b'survives\n')
            result = read(self.path)
            self.assertIn(b'dropped_log_bytes=9 total_dropped_log_bytes=9', result)
            self.assertTrue(result.endswith(b'survives\n'))
            self.assertEqual(output.total_dropped, 9)
            self.assertEqual(output.dropped, 0)
            self.assertLessEqual(len(result), L.DEFAULT_MAX_BYTES)
        finally:
            output.close()

    def test_directory_and_lock_failures_recover_without_losing_next_chunk(self):
        original = L.os.open
        for stage in ('directory', 'lock'):
            output = L.LogOutput(self.path, 8192, 4096)
            failed = [True]
            now = [0.0]
            def directory(path):
                if failed[0] and stage == 'directory':
                    raise OSError(errno.EACCES, 'injected')
            def open_log(path, flags, mode=0o777):
                if failed[0] and stage == 'lock' and path.endswith('.lock'):
                    raise OSError(errno.ENOSPC, 'injected')
                return original(path, flags, mode)
            try:
                with patch(L, 'ensure_parent', directory), patch(L.os, 'open', open_log), patch(L, 'capture_time', lambda: now[0]), patch(L.time, 'sleep', self.delays.append):
                    output.start()
                    output.offer(b'lost')
                    self.assertEqual(output.total_dropped, 4)
                    failed[0] = False
                    now[0] = 1.1
                    output.offer(b'next chunk\n')
                    self.assertEqual(output.dropped, 0)
                self.assertIn(b'dropped_log_bytes=4 total_dropped_log_bytes=4', read(self.path))
                self.assertTrue(read(self.path).endswith(b'next chunk\n'))
            finally:
                output.close()

    def test_warning_and_probe_rate_limits_and_backward_clock(self):
        output = L.LogOutput(self.path, 8192, 4096)
        now = [100.0]
        attempts = [0]
        def unavailable(fd, data):
            attempts[0] += 1
            raise OSError(errno.ENOSPC, 'injected')
        try:
            with patch(L.os, 'write', unavailable), patch(L, 'capture_time', lambda: now[0]), patch(L.time, 'sleep', self.delays.append):
                output.start()
                for unused in range(1000):
                    output.offer(b'x')
                self.assertEqual(attempts[0], L.OUTPUT_FAILURE_LIMIT)
                self.assertEqual(output.total_dropped, 1000)
                self.assertEqual(len(self.messages), 1)
                now[0] = 160
                output.offer(b'x')
                self.assertEqual(len(self.messages), 2)
                self.assertIn('total_dropped_log_bytes=1001', self.messages[-1])
                now[0] = 1  # Python 2 fallback clock moved backwards.
                output.offer(b'x')
                self.assertEqual(attempts[0], 3 * L.OUTPUT_FAILURE_LIMIT)
        finally:
            output.close()

    def test_recovery_marker_and_chunk_together_respect_four_mib_cap(self):
        write(self.path, b'x' * L.DEFAULT_MAX_BYTES)
        output = L.LogOutput(self.path, L.DEFAULT_MAX_BYTES, L.DEFAULT_KEEP_BYTES)
        original = L.os.write
        def bounded(fd, data):
            written = original(fd, data)
            self.assertLessEqual(os.fstat(fd).st_size, L.DEFAULT_MAX_BYTES)
            return written
        try:
            output.start()
            output.failed = True
            output.discard(12345)
            with patch(L.os, 'write', bounded):
                output.offer(b'next chunk\n', force=True)
            self.assertLessEqual(os.path.getsize(self.path), L.DEFAULT_MAX_BYTES)
            self.assertIn(b'dropped_log_bytes=12345', read(self.path))
            self.assertTrue(read(self.path).endswith(b'next chunk\n'))
            self.assertEqual(output.dropped, 0)
        finally:
            output.close()

    def test_normalization_failure_counts_normalized_bytes_and_continues(self):
        original = L.normalize_chunk
        def unavailable(raw):
            if raw.startswith(b'lost'):
                raise MemoryError()
            return original(raw)
        with patch(L, 'normalize_chunk', unavailable), patch(L, 'OUTPUT_PROBE_SECONDS', 0):
            self.capture([b'lost\r\n', b'recovered\n', b''])
        self.assertIn(b'dropped_log_bytes=5 total_dropped_log_bytes=5', read(self.path))
        self.assertTrue(read(self.path).endswith(b'recovered\n'))

    def test_eof_reports_loss_marker_after_output_recovers(self):
        original = L.os.open
        calls = [0]
        def unavailable(path, flags, mode=0o777):
            if path == self.path:
                calls[0] += 1
                if calls[0] <= L.OUTPUT_FAILURE_LIMIT:
                    raise OSError(errno.EROFS, 'injected')
            return original(path, flags, mode)
        with patch(L.os, 'open', unavailable):
            self.capture([b'lost\r\n', b''])
        self.assertIn(b'dropped_log_bytes=5 total_dropped_log_bytes=5', read(self.path))
        self.assertIn('unreported_dropped_log_bytes=0 total_dropped_log_bytes=5', self.messages[-1])

    def test_unlinked_or_replaced_log_is_reopened(self):
        events = [b'first', b'second', b'third', b'']
        def source(fd, size):
            if len(events) == 3:
                os.unlink(self.path)
            elif len(events) == 2:
                write(self.path + '.replacement', b'replacement:')
                os.rename(self.path + '.replacement', self.path)
            return events.pop(0)
        with patch(L.os, 'read', source):
            self.assertEqual(L.capture(self.path, 8192, 4096, self.input_fd), 0)
        self.assertEqual(read(self.path), b'replacement:third')

    def test_destination_lock_refuses_to_consume(self):
        lock = os.open(self.path + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def forbidden(*args):
                self.fail('duplicate writer consumed input')
            with patch(L.os, 'read', forbidden):
                self.assertEqual(L.capture(self.path, 8192, 4096, self.input_fd), 1)
        finally:
            os.close(lock)

    def test_pipe_lock_prevents_consumers_with_different_destinations(self):
        reader, writer = os.pipe()
        competing = os.open('/proc/self/fd/%d' % reader, os.O_RDONLY | os.O_NONBLOCK)
        try:
            self.assertTrue(L.lock_fd(reader))
            def forbidden(*args):
                self.fail('duplicate pipe consumer read input')
            with patch(L.os, 'read', forbidden):
                self.assertEqual(L.capture(self.path, 8192, 4096, competing), 1)
        finally:
            os.close(competing)
            os.close(reader)
            os.close(writer)


def fixture_peak_kib():
    # Use this process's mm high-water RSS, not inherited ru_maxrss from exec.
    with open('/proc/self/status') as handle:
        for line in handle:
            if line.startswith('VmHWM:'):
                return int(line.split()[1])
    raise AssertionError('VmHWM unavailable')


def drain_fixture(kind, path, result_path):
    baseline = fixture_peak_kib()
    messages = []
    state = {'pending_max': 0, 'output': None, 'prefix': True}
    base_output = L.LogOutput
    real_open, real_write, real_rename = os.open, os.write, os.rename
    class ObservedOutput(base_output):
        def __init__(self, *args):
            base_output.__init__(self, *args)
            state['output'] = self
        def offer(self, chunk, force=False):
            state['pending_max'] = max(state['pending_max'], len(chunk))
            return base_output.offer(self, chunk, force)
    def no_parent(*args):
        raise OSError(errno.EACCES, 'injected')
    def open_log(target, flags, mode=0o777):
        if target == path:
            if kind == 'full':
                return real_open('/dev/full', os.O_WRONLY)
            if kind == 'open':
                raise OSError(errno.EROFS, 'injected')
        if target == path + '.lock' and kind == 'lock':
            raise OSError(errno.ENOSPC, 'injected')
        return real_open(target, flags, mode)
    def write_log(fd, data):
        if kind == 'write':
            if state['prefix']:
                state['prefix'] = False
                return real_write(fd, data[:3])
            raise OSError(errno.ENOSPC, 'injected')
        return real_write(fd, data)
    def replace_log(source, target):
        if kind == 'trim':
            raise OSError(errno.ENOSPC, 'injected')
        return real_rename(source, target)
    if kind != 'parent':
        os.makedirs(os.path.dirname(path))
    if kind == 'trim':
        write(path, b'x' * 8192)
    patches = [patch(L, 'LogOutput', ObservedOutput), patch(L, 'protect_log_writer', lambda: True),
               patch(L, 'report', messages.append), patch(L, 'capture_time', lambda: 0),
               patch(L.os, 'open', open_log), patch(L.os, 'write', write_log), patch(L.os, 'rename', replace_log)]
    if kind == 'parent':
        patches.append(patch(L, 'ensure_parent', no_parent))
    for item in patches:
        item.__enter__()
    try:
        rc = L.capture(path, 8192, 4096)
    finally:
        for item in reversed(patches):
            item.__exit__(None, None, None)
    result = {'rc': rc, 'dropped': state['output'].total_dropped,
              'pending_max': state['pending_max'], 'peak_growth_kib': fixture_peak_kib() - baseline,
              'messages': messages}
    with open(result_path, 'w') as handle:
        json.dump(result, handle)
    return rc


class ProducerProgressTests(FixtureBase):
    def test_persistent_failures_drain_far_beyond_pipe_capacity_with_bounded_memory(self):
        total = 16 * 1024 * 1024
        code = ('import os\nblock=b"x"*8192\nfor unused in range(2048):\n'
                ' offset=0\n while offset<len(block):\n  offset+=os.write(1,block[offset:])\n')
        for kind in ('parent', 'lock', 'open', 'full', 'write', 'trim'):
            path = os.path.join(self.tmp, kind, 'sgminer.log')
            result_path = os.path.join(self.tmp, kind + '.json')
            producer = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger = subprocess.Popen([sys.executable, os.path.abspath(__file__), '--drain-fixture', kind, path, result_path],
                                      stdin=producer.stdout, stderr=subprocess.PIPE)
            producer.stdout.close()
            try:
                deadline = time.time() + 10
                while (producer.poll() is None or logger.poll() is None) and time.time() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(producer.poll(), kind + ': producer blocked on full pipe')
                self.assertIsNotNone(logger.poll(), kind + ': logger did not drain to EOF')
                self.assertEqual(producer.returncode, 0, producer.stderr.read())
                self.assertEqual(logger.returncode, 0, logger.stderr.read())
                with open(result_path) as handle:
                    result = json.load(handle)
                expected = total - (3 if kind == 'write' else 0)
                self.assertEqual(result['dropped'], expected, kind)
                self.assertLessEqual(result['pending_max'], L.CHUNK_BYTES)
                self.assertLessEqual(result['peak_growth_kib'], 4096, result)
                self.assertLessEqual(len(result['messages']), 3, result)
                self.assertIn('total_dropped_log_bytes=%d' % expected, result['messages'][-1])
                if kind == 'trim':
                    self.assertEqual(read(path), b'x' * 8192)
                elif kind == 'write':
                    self.assertEqual(read(path), b'xxx')
                print('drain fixture %s: producer_bytes=%d dropped=%d pending_max=%d peak_growth_kib=%d' %
                      (kind, total, result['dropped'], result['pending_max'], result['peak_growth_kib']))
            finally:
                for child in (producer, logger):
                    if child.poll() is None:
                        child.kill()
                    child.wait()
                    child.stderr.close()


class RecoveryTests(FixtureBase):
    # Synthetic proc entries point only to our real fixture subprocesses. No
    # test inspects or changes services, miners, or unrelated host processes.
    def setUp(self):
        FixtureBase.setUp(self)
        self.proc = os.path.join(self.tmp, 'proc')
        os.mkdir(self.proc)
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()
        FixtureBase.tearDown(self)

    def producer(self, parent_reader=False):
        reader, writer = os.pipe()
        env = dict(os.environ, BS_SGMINER_LOG=self.path,
                   BS_SGMINER_LOG_MAX='8192', BS_SGMINER_LOG_KEEP='4096')
        child = subprocess.Popen([sys.executable, '-c',
            'import os,sys\nfor line in sys.stdin:\n os.write(1, line.encode("ascii"))'],
            stdin=subprocess.PIPE, stdout=writer, stderr=writer, env=env, close_fds=True)
        self.children.append(child)
        os.close(writer)
        os.symlink('/proc/%d' % child.pid, os.path.join(self.proc, str(child.pid)))
        if not parent_reader:
            os.close(reader)
            reader = None
        return child, reader

    def test_recover_dead_reader_without_restarting_producer(self):
        child, unused = self.producer()
        identity = L.process_identity(child.pid, self.proc)
        # Child sleeps waiting for input; recovery must attach before output.
        harness = ("import os,sys\n" +
                   ("import imp\nL=imp.load_source('writer',%r)\n" % WRITER if sys.version_info[0] == 2 else
                    "import importlib.util\ns=importlib.util.spec_from_file_location('writer',%r)\nL=importlib.util.module_from_spec(s)\ns.loader.exec_module(L)\n" % WRITER) +
                   "L.LOG_PATHS=(%r,)\nL.protect_log_writer=lambda: True\n" % self.path +
                   "sys.exit(L.recover(%d,%r,proc_root=%r,daemon=False))\n" %
                   (child.pid, sys.executable, self.proc))
        recovery = subprocess.Popen([sys.executable, '-c', harness], stderr=subprocess.PIPE)
        self.children.append(recovery)
        deadline = time.time() + 5
        while not os.path.exists(self.path + '.lock') and recovery.poll() is None and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(os.path.exists(self.path + '.lock'))
        self.assertEqual(L.process_identity(child.pid, self.proc), identity)
        child.stdin.write(b'recovered without restart\n')
        child.stdin.close()
        child.stdin = None
        self.assertEqual(child.wait(), 0)
        self.assertEqual(recovery.wait(), 0)
        self.assertEqual(read(self.path), b'recovered without restart\n')

    def test_legacy_nonstdin_reader_blocks_recovery(self):
        child, reader = self.producer(parent_reader=True)
        # An unrelated process holding the read end on a non-stdin descriptor
        # must block recovery, even if it never adopted the writer's flock.
        fcntl.fcntl(reader, fcntl.F_SETFD, 0)
        holder = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], close_fds=False)
        self.children.append(holder)
        os.symlink('/proc/%d' % holder.pid, os.path.join(self.proc, str(holder.pid)))
        try:
            with patch(L, 'LOG_PATHS', (self.path,)):
                self.assertEqual(L.recover(child.pid, sys.executable, self.proc, daemon=False), 0)
            self.assertFalse(os.path.exists(self.path))
        finally:
            os.close(reader)

    def test_real_pipe_competing_recoveries_and_daemon_release_launcher_lock(self):
        child, unused = self.producer()
        state = os.path.join(self.tmp, 'recovery.state')
        lock_path = os.path.join(self.tmp, 'launcher.lock')
        loader = ("import imp\nL=imp.load_source('writer',%r)\n" % WRITER if sys.version_info[0] == 2 else
                  "import importlib.util\ns=importlib.util.spec_from_file_location('writer',%r)\nL=importlib.util.module_from_spec(s)\ns.loader.exec_module(L)\n" % WRITER)
        harness = ("import os,sys,fcntl\n" + loader +
                   "L.LOG_PATHS=(%r,)\nL.protect_log_writer=lambda: True\n" % self.path +
                   "lock=os.open(%r,os.O_CREAT|os.O_RDWR,0o600)\nfcntl.flock(lock,fcntl.LOCK_EX)\n" % lock_path +
                   "rc=L.recover(%d,%r,proc_root=%r)\nos.close(lock)\n" % (child.pid, sys.executable, self.proc) +
                   "open(%r,'w').write(str(rc))\n_,status=os.waitpid(-1,0)\nsys.exit(os.WEXITSTATUS(status))\n" % state)
        recovery = subprocess.Popen([sys.executable, '-c', harness], stderr=subprocess.PIPE)
        self.children.append(recovery)
        # The helper returns before input arrives; only its reader child stays.
        deadline = time.time() + 5
        while (not os.path.exists(state) or not os.path.exists(self.path + '.lock')) and recovery.poll() is None and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(os.path.exists(state))
        self.assertEqual(read(state), b'0')
        self.assertTrue(os.path.exists(self.path + '.lock'))
        lock = os.open(lock_path, os.O_RDWR)
        try:
            self.assertTrue(L.lock_fd(lock), 'daemon inherited the launcher lock')
        finally:
            os.close(lock)
        # Deliberately hide the actual reader from the proc listing: the real
        # pipe-inode flock must still prevent a second recovery consumer.
        with patch(L, 'LOG_PATHS', (self.path,)):
            self.assertEqual(L.recover(child.pid, sys.executable, self.proc, daemon=False), 0)
        child.stdin.write(b'one reader only\n')
        child.stdin.close()
        child.stdin = None
        self.assertEqual(child.wait(), 0)
        self.assertEqual(recovery.wait(), 0)
        self.assertEqual(read(self.path), b'one reader only\n')

    def test_proc_inspection_failure_fails_closed(self):
        child, unused = self.producer()
        original = L.os.listdir
        def denied(path):
            if path.endswith('/fd'):
                raise OSError(errno.EACCES, 'injected')
            return original(path)
        with patch(L.os, 'listdir', denied):
            with self.assertRaises(OSError):
                L.recover(child.pid, sys.executable, self.proc, daemon=False)
        self.assertFalse(os.path.exists(self.path))

    def test_racing_reader_and_pid_reuse_are_rejected(self):
        child, unused = self.producer()
        checks = [False, True]
        with patch(L, 'LOG_PATHS', (self.path,)), patch(L, 'pipe_readers', lambda *args: checks.pop(0)):
            self.assertEqual(L.recover(child.pid, sys.executable, self.proc, daemon=False), 0)
        self.assertEqual(checks, [])
        original = L.process_identity
        identities = [original(child.pid, self.proc), ('1', 'changed', os.path.realpath(sys.executable))]
        with patch(L, 'LOG_PATHS', (self.path,)), patch(L, 'process_identity', lambda *args: identities.pop(0)):
            with self.assertRaises(ValueError):
                L.recover(child.pid, sys.executable, self.proc, daemon=False)
        self.assertFalse(os.path.exists(self.path))

    def test_destination_comes_from_running_process_not_current_settings(self):
        child, unused = self.producer()
        with patch(L, 'LOG_PATHS', (self.path,)):
            self.assertEqual(L.active_log_config(child.pid, L.process_identity(child.pid, self.proc), self.proc),
                             (self.path, 8192, 4096))

    def test_legacy_parent_pipeline_and_unknown_destination(self):
        child, unused = self.producer()
        identity = L.process_identity(child.pid, self.proc)
        original = L.read_proc
        command = "'/fixture/miner' --text-only 2>&1 | /usr/bin/python '%s' '%s' '4194304' '2097152'" % (WRITER, self.path)
        def metadata(path):
            if path.endswith('/environ'):
                return ''
            if path.endswith('/cmdline'):
                return 'bash\0-c\0' + command + '\0'
            return original(path)
        with patch(L, 'LOG_PATHS', (self.path,)), patch(L, 'read_proc', metadata):
            self.assertEqual(L.active_log_config(child.pid, identity, self.proc), (self.path, 4194304, 2097152))
            command = '/unknown/pipeline'
            with self.assertRaises(ValueError):
                L.active_log_config(child.pid, identity, self.proc)

    def test_wrong_executable_and_nonpipe_output_are_rejected(self):
        child, unused = self.producer()
        with self.assertRaises(ValueError):
            L.recover(child.pid, '/nonexistent/executable', self.proc, daemon=False)
        original = L.os.readlink
        def nonpipe(path):
            return '/some/log/file' if path.endswith('/fd/1') else original(path)
        with patch(L.os, 'readlink', nonpipe):
            with self.assertRaises(ValueError):
                L.recover(child.pid, sys.executable, self.proc, daemon=False)


class StartupTests(unittest.TestCase):
    def test_firmware_guard_still_precedes_recovery_and_miner_lifecycle(self):
        with open(os.path.join(ROOT, 'overlay/opt/scripta/startup/miner-start.sh')) as handle:
            script = handle.read()
        guard = script.index('if [[ "${BS_FIRMWARE_RESTART:-}"')
        self.assertLess(guard, script.index('"$LOG_WRITER" --recover'))
        self.assertIn('    exit 0', script[guard:script.index('\nfi', guard)])

    def test_recovery_precedes_young_miner_and_api_success_exits(self):
        with open(os.path.join(ROOT, 'overlay/opt/scripta/startup/miner-start.sh')) as handle:
            script = handle.read()
        start = script.index('if pgrep -f "$SGMINER "')
        recovery = script.index('"$LOG_WRITER" --recover "$SG_PID" "$SGMINER"', start)
        self.assertLess(recovery, script.index('"$SG_UP" -lt', start))
        self.assertLess(recovery, script.index('if api_command_ok "$API_PORT"', start))
        block = script[recovery:script.index('    SG_UP=', recovery)]
        self.assertNotIn('stop_sgminer', block)
        self.assertNotIn('kill ', block)
        self.assertIn("BS_SGMINER_LOG='$SGMINER_LOG'", script)

    def test_healthy_capture_fast_path_skips_python_and_uses_pipe_identity(self):
        with open(os.path.join(ROOT, 'overlay/opt/scripta/startup/miner-start.sh')) as handle:
            script = handle.read()
        function = script[script.index('log_capture_present()'):script.index('api_command_ok()')]
        start = script.index('    if ! log_capture_present "$SG_PID"')
        invocation = script[start:script.index('    SG_UP=', start)]
        for same_pipe, expected in [(True, b''), (False, b'recovery-called\n')]:
            prelude = ('SG_PID=42\nLOG_WRITER=/fixture/writer.py\nSGMINER=/fixture/miner\n'
                       'pgrep() { echo 43; }\n'
                       'readlink() { case "$1" in */42/fd/1) echo "pipe:[123]";; '
                       '*/43/fd/0) echo "pipe:[%s]";; esac; }\n'
                       'log() { :; }\n') % ('123' if same_pipe else '456')
            command = prelude + function + invocation.replace('/usr/bin/python', 'fixture_python')
            command = 'fixture_python() { echo recovery-called; }\n' + command
            self.assertEqual(subprocess.check_output(['bash', '-c', command]), expected)


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--drain-fixture':
        sys.exit(drain_fixture(sys.argv[2], sys.argv[3], sys.argv[4]))
    unittest.main(verbosity=2)
