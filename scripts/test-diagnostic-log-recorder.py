#!/usr/bin/env python3
"""Offline event routing, retention and secret filtering checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('diagnostic_logs', ROOT / 'overlay/opt/scripta/bin/diagnostic-log-recorder.py')
logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logs)


def record(tag, message, **fields):
    return dict({'SYSLOG_IDENTIFIER': tag, 'MESSAGE': message, '__CURSOR': 'cursor-' + tag,
                 '__REALTIME_TIMESTAMP': '1700000000000000'}, **fields)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.specs = dict((name, (key, str(self.root/'ram'/name), str(self.root/'sd'/name)))
                          for name, (key, ram, sd) in logs.SPECS.items())
        self.patcher = patch.object(logs, 'SPECS', self.specs)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_routing(self):
        for tag in ('NetworkManager', 'wpa_supplicant', 'dhclient'):
            self.assertEqual(logs.classify(record(tag, 'link connected')), ['network-events.log'])
        self.assertEqual(logs.classify(record('kernel', 'xradio timeout', _TRANSPORT='kernel', PRIORITY='3')),
                         ['network-events.log', 'system-events.log'])
        for message in ('wdt_restart', 'wdt_restart: routine tick'):
            self.assertEqual(logs.classify(record('kernel', message, _TRANSPORT='kernel', PRIORITY='3')), [])
        for message in ('Out of memory: Killed process', 'mmc I/O error', 'Kernel panic'):
            self.assertEqual(logs.classify(record('kernel', message, _TRANSPORT='kernel')), ['system-events.log'])
        self.assertEqual(logs.classify(record('systemd', 'Started Network Manager.', UNIT='NetworkManager.service')), ['system-events.log'])
        self.assertEqual(logs.classify(record('systemd', 'Started Session 1', UNIT='session-1.scope')), [])
        self.assertEqual(logs.classify(record('sshd', 'session started')), [])
        self.assertEqual(logs.classify(record('bs-diagnostic-logs', 'collection failed')), [])
        self.assertEqual(logs.classify(record('bs-temp', 'board 0 DISABLE')), ['cooling-events.log'])
        self.assertEqual(logs.classify(record('lighttpd', 'PHP fatal error')), ['lighttpd-error.log'])
        self.assertEqual(logs.classify(record('sudo', 'session opened', _SYSTEMD_UNIT='lighttpd.service')), [])
        self.assertEqual(logs.classify(record('bs-fan', 'fan update', _SYSTEMD_UNIT='lighttpd.service')), ['cooling-events.log'])

    def test_secrets(self):
        for message in ('password=NOT-A-REAL-SECRET', 'password="spaces in value"',
                        'psk=12345678', '"userPassword": "value"', 'passphrase: abc',
                        'token=abc', 'Cookie: abc', 'Authorization: Basic abc',
                        'http://user:abc@host/path', 'secret:\n  value on next line',
                        'wep_key=1234', 'api-key: abc', 'pass=abc'):
            self.assertEqual(logs.sanitize(message), '[sensitive details omitted]')
        self.assertEqual(logs.sanitize('http://host/path?code=abc'), 'http://host/path?[query omitted]')
        self.assertNotIn('\n', logs.sanitize('one\nforged log entry'))
        self.assertLessEqual(len(logs.sanitize('x'*10000)), 2048)

    def test_cooling_records_changes_not_samples(self):
        fan = dict(enabled=True, status='ok', fan=45, max_temp=41, reason='target 41 C')
        temps = {'0': dict(disabled=False, last_temp=41)}
        state, lines = logs.cooling_changes({}, fan, temps)
        self.assertEqual(len(lines), 1)
        fan.update(max_temp=42, reason='target 42 C')
        self.assertEqual(logs.cooling_changes(state, fan, temps)[1], [])
        fan['fan'] = 50
        updated, lines = logs.cooling_changes(state, fan, temps)
        self.assertEqual(len(lines), 1)
        temps['0']['disabled'] = True
        updated, lines = logs.cooling_changes(updated, fan, temps)
        self.assertIn('cutoff', lines[0])
        temps['0']['disabled'] = False
        self.assertIn('recovered', logs.cooling_changes(updated, fan, temps)[1][0])
        fan.update(status='error', reason='failed to set fan')
        self.assertIn('failed to set fan', logs.cooling_changes(updated, fan, temps)[1][0])

    def test_destinations_cursor_and_quiet_run(self):
        entries = [record('NetworkManager', 'connected'), record('lighttpd', 'password=FAKE'), record('bs-temp', 'cutoff')]
        config = {'userDev': False, 'userWebErrorLogTarget': 'SD'}
        state = logs.collect(config, {}, 'boot-fixture', entries, {}, {})
        self.assertEqual(state['cursor'], entries[-1]['__CURSOR'])
        self.assertTrue(state['ok'])
        self.assertIn('connected', (self.root/'ram/network-events.log').read_text())
        self.assertIn('boot-fixture', (self.root/'ram/system-events.log').read_text())
        self.assertFalse((self.root/'ram/lighttpd-error.log').exists())
        self.assertNotIn('FAKE', (self.root/'sd/lighttpd-error.log').read_text())
        before = (self.root/'ram/system-events.log').stat().st_mtime_ns
        logs.collect(config, state, 'boot-fixture', [], {}, {})
        self.assertEqual(before, (self.root/'ram/system-events.log').stat().st_mtime_ns)
        self.assertNotIn('FAKE', json.dumps(state))
        logs.collect(config, state, 'another-boot', [], {}, {}, True)
        self.assertIn('input limit reached', (self.root/'ram/system-events.log').read_text())

    def test_cap_and_clear(self):
        path = self.root/'log'
        for i in range(100):
            logs.append_capped(str(path), ['%d %s' % (i, 'x'*80)], cap=1024)
            self.assertLessEqual(path.stat().st_size, 1024)
        self.assertIn('99 ', path.read_text())
        self.assertNotIn('0 ' + 'x'*80, path.read_text())
        path.write_text('')
        logs.append_capped(str(path), ['after clear'])
        self.assertEqual(path.read_text(), 'after clear\n')
        logs.append_capped(str(path), ['y'*4000], cap=1024)
        self.assertLessEqual(path.stat().st_size, 1024)

    def test_symlinks_and_write_failure(self):
        original = self.root/'keep'
        original.write_text('original')
        link = self.root/'link'
        link.symlink_to(original)
        with self.assertRaises(IOError):
            logs.append_capped(str(link), ['new'])
        self.assertEqual(original.read_text(), 'original')
        state = {'cursor': 'old-cursor'}
        with patch.object(logs, 'append_capped', side_effect=IOError('disk full')):
            with self.assertRaises(IOError):
                logs.collect({}, state, 'boot', [record('NetworkManager', 'up')], {}, {})
        self.assertEqual(state['cursor'], 'old-cursor', 'failed write must be retried')

    def test_journal_command_and_install_contract(self):
        self.assertIn('--after-cursor=fixture', logs.journal_command('fixture'))
        self.assertNotIn('--after-cursor=', logs.journal_command(''))
        cron = (ROOT/'overlay/etc/cron.d/blakestream-diagnostic-logs').read_text()
        self.assertEqual(cron.count('/usr/bin/python /opt/scripta/bin/diagnostic-log-recorder.py'), 2)
        self.assertIn('www-data sleep 10;', cron)
        self.assertIn('www-data sleep 40;', cron)
        self.assertIn('server.errorlog-use-syslog  = "enable"', (ROOT/'overlay/etc/lighttpd/lighttpd.conf').read_text())
        self.assertNotIn('server.errorlog             =', (ROOT/'overlay/etc/lighttpd/lighttpd.conf').read_text())

    def test_existing_web_log_is_scrubbed_once(self):
        path = self.root/'ram/lighttpd-error.log'
        path.parent.mkdir()
        path.write_text('old password=FAKE\nold harmless error\n')
        state = logs.collect({}, {}, 'boot', [], {}, {})
        self.assertNotIn('FAKE', path.read_text())
        self.assertIn('old harmless error', path.read_text())
        before = path.stat().st_mtime_ns
        logs.collect({}, state, 'boot', [], {}, {})
        self.assertEqual(before, path.stat().st_mtime_ns)

    def helper(self, wifi=False, **fields):
        body = dict(ok=True, error=None, pending=None, last_result=None, interfaces=[])
        if wifi:
            body.update(adapter={'present': False}, radio={'enabled': False}, ethernet_connected=False)
        body.update(fields)
        return record('python3', json.dumps(body), _SYSTEMD_UNIT='blakestream-%s-rollback.service' % ('wifi' if wifi else 'network'))

    def test_idle_checks_are_not_adapter_status_events(self):
        entries = [self.helper(), self.helper(True)] * 5
        state = logs.collect({}, {}, 'boot', entries, {}, {})
        self.assertEqual(state['cursor'], entries[-1]['__CURSOR'])
        self.assertFalse((self.root/'ram/network-events.log').exists())
        self.assertEqual(state['events_seen'], {})
        for wording in ('Starting', 'Started', 'Stopping', 'Stopped'):
            routine = record('systemd', wording+' Roll back a failed Blakestream Wi-Fi change.', UNIT='blakestream-wifi-rollback.service')
            self.assertEqual(logs.classify(routine), [])
        failure = record('systemd', 'Failed to start Roll back a failed Blakestream Wi-Fi change.', PRIORITY='3')
        self.assertEqual(logs.classify(failure), ['system-events.log'])

    def test_helper_changes_are_readable_and_not_repeated(self):
        pending = dict(id='one', state='applying', ssid='Home network', security='wpa-psk', seconds_remaining=60)
        entry = self.helper(True, pending=pending, password='PRIVATE-FIXTURE')
        state = logs.collect({}, {}, 'boot', [entry], {}, {})
        path = self.root/'ram/network-events.log'
        self.assertIn('Wi-Fi: Applying settings for "Home network"', path.read_text())
        self.assertNotIn('PRIVATE-FIXTURE', path.read_text())
        self.assertNotIn('PRIVATE-FIXTURE', json.dumps(logs.prepare_record(entry)))
        before = path.read_text()
        pending['seconds_remaining'] = 30
        state = logs.collect({}, state, 'boot', [self.helper(True, pending=pending)], {}, {})
        self.assertEqual(path.read_text(), before)
        result = dict(action='connect', ok=True, error=None, at=1234)
        state = logs.collect({}, state, 'boot', [self.helper(True, last_result=result)], {}, {})
        self.assertIn('Wi-Fi: Connected', path.read_text())
        before = path.read_text()
        logs.collect({}, state, 'boot', [self.helper(True, last_result=result)]*3, {}, {})
        self.assertEqual(path.read_text(), before)

    def test_errors_repeat_only_after_recovery(self):
        failure = self.helper(ok=False, error='NetworkManager not responding')
        state = logs.collect({}, {}, 'boot', [failure, failure], {}, {})
        path = self.root/'ram/network-events.log'
        self.assertEqual(path.read_text().count('not responding'), 1)
        state = logs.collect({}, state, 'boot', [self.helper(), failure], {}, {})
        self.assertEqual(path.read_text().count('not responding'), 2)
        result = dict(action='connect', ok=False, error='connection timed out', at=1234)
        logs.collect({}, state, 'boot', [self.helper(True, ok=False, error='connection timed out', last_result=result)], {}, {})
        self.assertEqual(path.read_text().count('connection timed out'), 1)

    def test_network_and_cooling_event_language(self):
        def description(tag, message):
            events, reset = logs.describe_event(tag, message)
            self.assertEqual(len(events), 1)
            return events[0]['source'] + ': ' + events[0]['message']
        self.assertEqual(description('NetworkManager', "<info> [1790000000.1234] device (eth0): state change: activated -> unavailable (reason 'carrier-changed', sys-iface-state: 'managed')"),
                         'Ethernet (eth0): Connected -> Unavailable; network link changed')
        self.assertEqual(description('NetworkManager', "device (wlan0): Activation: starting connection 'Home' (1234-5678)"),
                         'Wi-Fi (wlan0): Connecting to "Home"')
        self.assertIn('Wi-Fi (wlan0): Disconnected (reason 3; initiated locally)',
                      description('wpa_supplicant', 'wlan0: CTRL-EVENT-DISCONNECTED bssid=01:02:03:04:05:06 reason=3 locally_generated=1'))
        self.assertEqual(description('dhclient', 'bound to 192.0.2.26 -- renewal in 100 seconds.'),
                         'DHCP: Address assigned: 192.0.2.26; lease renewal in 100s')
        self.assertEqual(description('fan', 'auto=True status=ok fan=70% curve 36.0 C between target 20.0 and mid 36.0 C'),
                         'Fan: Automatic fan: 70%; hottest board 36.0 C')
        self.assertEqual(description('scripta_watchdog', 'starting sgminer with runtime config: {"boards_assigned":3,"runtime_pool_count":4,"pools":[{"password":"PRIVATE"}]}'),
                         'Miner: Starting miner: 3 boards assigned; 4 pool connections')
        self.assertIn('Driver: xradio timeout code -5', description('kernel', 'xradio timeout code -5'))

    def test_existing_ram_and_sd_logs_are_converted_once(self):
        for side in ('ram', 'sd'):
            path = self.root/side/'network-events.log'
            path.parent.mkdir()
            stamp = '2026-10-01 18:46:38 UTC '
            pending = dict(id='one', state='applying', ssid='Home', security='wpa-psk')
            path.write_text(stamp+'python3: '+self.helper(True)['MESSAGE']+'\n'+
                            (stamp+'python3: '+self.helper(True, pending=pending)['MESSAGE']+'\n')*3+
                            stamp+'kernel: xradio timeout code -5\n')
        original = {'version': 1, 'cursor': 'existing', 'boot_id': 'boot'}
        state = logs.collect({}, original, 'boot', [], {}, {})
        self.assertEqual(state['cursor'], 'existing')
        for side in ('ram', 'sd'):
            path = self.root/side/'network-events.log'
            content = path.read_text()
            self.assertNotIn('"adapter"', content)
            self.assertNotIn('python3:', content)
            self.assertEqual(content.count('Applying settings'), 1)
            self.assertIn('2026-10-01 18:46:38 UTC Driver: xradio timeout code -5', content)
            before = path.stat().st_mtime_ns
            logs.collect({}, state, 'boot', [], {}, {})
            self.assertEqual(before, path.stat().st_mtime_ns)

    def test_redaction_and_failure_retention_survive_formatting(self):
        failure = self.helper(ok=False, error='password=PRIVATE')
        state = logs.collect({}, {}, 'boot', [failure], {}, {})
        self.assertNotIn('PRIVATE', (self.root/'ram/network-events.log').read_text())
        self.assertNotIn('PRIVATE', json.dumps(state))
        before = json.dumps(state, sort_keys=True)
        with patch.object(logs, 'append_capped', side_effect=IOError('disk full')):
            with self.assertRaises(IOError):
                logs.collect({}, state, 'boot', [self.helper(ok=False, error='different failure')], {}, {})
        self.assertEqual(before, json.dumps(state, sort_keys=True), 'failed write must not suppress retry')


if __name__ == '__main__':
    unittest.main()
