#!/usr/bin/env python3
"""Offline API outage reporting and temperature cutoff regressions."""
import contextlib
import errno
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    'temp_watchdog', ROOT / 'overlay/opt/scripta/bin/temp-watchdog.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
query_devs = watchdog.query_sgminer_devs


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.clock = 1000
        self.output = io.StringIO()
        for target, value in (('API_STATE', str(self.root / 'api.json')),
                              ('STATE', str(self.root / 'boards.json')),
                              ('CONFIG', str(self.root / 'config.json'))):
            patcher = patch.object(watchdog, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(watchdog.time, 'time', side_effect=lambda: self.clock),
                        patch.object(watchdog.grp, 'getgrnam', side_effect=KeyError),
                        contextlib.redirect_stderr(self.output)):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def unavailable(self):
        watchdog.report_api_status(4028, OSError(errno.ECONNREFUSED, 'Connection refused'))

    def test_restart_wait_is_quiet_until_recovery(self):
        self.unavailable()
        for seconds in (30, 60, 90):
            self.clock = 1000 + seconds
            self.unavailable()
        self.assertEqual(self.output.getvalue().count('waiting for sgminer API'), 1)
        self.assertNotIn('WARNING', self.output.getvalue())
        self.clock = 1095
        watchdog.report_api_status(4028)
        self.assertIn('monitoring resumed on port 4028 after 95s', self.output.getvalue())
        self.assertEqual(json.loads(Path(watchdog.API_STATE).read_text()), {})
        self.unavailable()
        self.assertEqual(self.output.getvalue().count('waiting for sgminer API'), 2)

    def test_persistent_outage_warns_and_repeats_at_bounded_intervals(self):
        self.unavailable()
        for seconds in (119, 120, 150, 419, 420):
            self.clock = 1000 + seconds
            self.unavailable()
        text = self.output.getvalue()
        self.assertEqual(text.count('WARNING'), 2)
        self.assertIn('for 120s', text)
        self.assertIn('for 420s', text)
        self.assertIn('Connection refused', text)

    def test_healthy_polls_do_not_write_availability_state(self):
        watchdog.report_api_status(4028)
        watchdog.report_api_status(4028)
        self.assertFalse(Path(watchdog.API_STATE).exists())
        self.assertEqual(self.output.getvalue(), '')

    def test_clock_rollback_and_port_change_restart_wait(self):
        self.unavailable()
        self.clock = 999
        self.unavailable()
        watchdog.report_api_status(4029, 'connection refused')
        state = json.loads(Path(watchdog.API_STATE).read_text())
        self.assertEqual(state['port'], 4029)
        self.assertEqual(state['unavailable_since'], 999)
        self.assertNotIn('WARNING', self.output.getvalue())

    def query(self, reply=None, failure=None):
        class FakeSocket:
            def settimeout(self, timeout):
                pass

            def connect(self, address):
                if failure:
                    raise failure

            def sendall(self, data):
                pass

            def shutdown(self, direction):
                pass

            def recv(self, size):
                result, self.reply = self.reply, b''
                return result

            def close(self):
                self.closed = True

        sock = FakeSocket()
        sock.reply, sock.closed = reply, False
        with patch.object(watchdog.socket, 'socket', return_value=sock):
            result = query_devs(4028)
        self.assertTrue(sock.closed)
        return result

    def test_query_tracks_refused_empty_and_recovered_temperatures(self):
        self.assertEqual(self.query(failure=OSError(errno.ECONNREFUSED, 'Connection refused')), {})
        self.clock = 1120
        self.assertEqual(self.query(b'{"DEVS":[]}'), {})
        self.assertIn('no board temperatures returned', self.output.getvalue())
        self.assertEqual(self.query(b'{"DEVS":[{"ASC":0,"Temperature":85.0}]}'), {'0': 85.0})
        self.assertIn('temperature monitoring resumed', self.output.getvalue())

    def test_status_write_failure_does_not_block_thermal_cutoff_or_recovery(self):
        original_writer = watchdog.write_json_atomic

        def write(path, value):
            if path == watchdog.API_STATE:
                raise IOError('fixture availability write failed')
            original_writer(path, value)

        # Force an outage/recovery report to fail, then feed a genuinely hot board.
        self.unavailable()
        with patch.object(watchdog, 'write_json_atomic', side_effect=write), \
                patch.object(watchdog, 'get_api_port', return_value=4028), \
                patch.object(watchdog, 'trigger_restart', return_value=True) as restart:
            with patch.object(watchdog, 'query_sgminer_devs', side_effect=lambda port:
                              self.query(b'{"DEVS":[{"ASC":0,"Temperature":85.0}]}')):
                self.assertEqual(watchdog.main(), 0)
            self.assertTrue(json.loads(Path(watchdog.STATE).read_text())['0']['disabled'])
            self.assertEqual(restart.call_count, 1)
            with patch.object(watchdog, 'query_sgminer_devs', side_effect=lambda port:
                              self.query(b'{"DEVS":[{"ASC":0,"Temperature":60.0}]}')):
                self.assertEqual(watchdog.main(), 0)
            self.assertFalse(json.loads(Path(watchdog.STATE).read_text())['0']['disabled'])
            self.assertEqual(restart.call_count, 2)
        self.assertIn('cannot record API availability', self.output.getvalue())
        self.assertIn('board 0 DISABLE', self.output.getvalue())
        self.assertIn('board 0 RECOVER', self.output.getvalue())


if __name__ == '__main__':
    unittest.main()
