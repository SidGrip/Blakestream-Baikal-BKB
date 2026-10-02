#!/usr/bin/env python
"""Bounded diagnostic event logs. Compatible with the controller's Python 2.7."""
from __future__ import print_function
import datetime
import errno
import fcntl
import grp
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

RAM = '/run/blakestream'
SD = '/opt/scripta/var'
CONFIG = '/opt/scripta/etc/scripta.conf'
STATE = RAM + '/diagnostic-log-state.json'
LOCK = RAM + '/diagnostic-logs.lock'
MAX_BYTES = 1048576
MAX_RECORDS = 2000
SPECS = {
    'network-events.log': ('userNetworkLogTarget', RAM + '/network-events.log', SD + '/network-events.log'),
    'system-events.log': ('userSystemLogTarget', RAM + '/system-events.log', SD + '/system-events.log'),
    'cooling-events.log': ('userCoolingLogTarget', RAM + '/cooling-events.log', SD + '/cooling-events.log'),
    'lighttpd-error.log': ('userWebErrorLogTarget', RAM + '/lighttpd-error.log', SD + '/lighttpd-error.log'),
}
SENSITIVE = re.compile(r'password|passphrase|passwd|\bpsk\b|wep[-_ ]?key|secret|credential|authorization|cookie|token|api[-_ ]?key|private[-_ ]?key|\bpass\s*[:=]|://[^\s/]+@', re.I)
NETWORK = re.compile(r'wlan|wi-?fi|xradio|80211|8189|realtek|\beth\d|\blink (?:is )?(?:up|down)|dhcp|carrier', re.I)
FAULT = re.compile(r'error|fail|panic|oops|oom|out of memory|killed process|segfault|timed? ?out|i/o|read-only|overheat', re.I)
LIFECYCLE = re.compile(r'start(?:ed|ing)|stop(?:ped|ping)|restart|shutdown|reboot|watchdog', re.I)
ROLLBACK_POLL = re.compile(r'^(?:Starting|Started|Stopping|Stopped) Roll back (?:a failed Blakestream Wi-Fi|an unconfirmed Blakestream network) change\.*$')
LOG_LINE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC) ([^:]+): (.*)$')
SOURCE_NAMES = {'NetworkManager': 'Network', 'wpa_supplicant': 'Wi-Fi', 'dhclient': 'DHCP',
                'kernel': 'Driver', 'systemd': 'System', 'scripta_watchdog': 'Miner',
                'bs-miner': 'Miner', 'bs-fan': 'Fan', 'bs-temp': 'Temperature',
                'fan': 'Fan', 'temperature': 'Temperature', 'lighttpd': 'Web server',
                'php-cgi': 'PHP', 'collector': 'Logging'}


def descriptor(source, message, key='', identity=None):
    result = {'source': source, 'message': sanitize(message), 'key': key}
    if key:
        encoded = json.dumps(identity if identity is not None else result['message'], sort_keys=True)
        result['signature'] = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return result


def helper_events(body):
    source = 'Wi-Fi' if 'adapter' in body else 'Network'
    pending = body.get('pending')
    last = body.get('last_result')
    events, reset = [], []
    if isinstance(pending, dict):
        state = pending.get('state', '')
        messages = {'pending': 'Settings change queued', 'applying': 'Applying settings',
                    'awaiting-confirmation': 'Waiting for confirmation of new settings',
                    'rollback-failed': 'Could not restore previous settings'}
        message = messages.get(state, 'Settings change: ' + sanitize(state))
        if pending.get('ssid'):
            message += ' for "' + sanitize(pending['ssid']) + '"'
        elif pending.get('address'):
            message += ' (' + sanitize(pending['address']) + ')'
        # Transaction deadlines tick continuously; they are not new events.
        identity = dict((key, pending.get(key)) for key in ('id', 'state', 'ssid', 'mode', 'address'))
        events.append(descriptor(source, message, source + '.pending', identity))
    else:
        reset.append(source + '.pending')
    if isinstance(last, dict):
        action = last.get('action', '')
        names = {'apply': 'Settings update', 'confirm': 'Settings confirmation check',
                 'rollback': 'Restore previous settings', 'activate': 'Connection recovery',
                 'tick': 'Automatic recovery', 'radio': 'Wi-Fi radio update', 'connect': 'Connection'}
        message = names.get(action, sanitize(action).replace('-', ' ').capitalize() or 'Settings operation')
        message += ' completed' if last.get('ok') is True else ' reported an error'
        if action == 'connect' and last.get('ok') is True:
            message = 'Connected'
        if last.get('error'):
            message += ': ' + sanitize(last['error'])
        events.append(descriptor(source, message, source + '.result', last))
    error = body.get('error')
    if error or body.get('ok') is False:
        # Do not repeat an error already explained by this operation's result.
        if not isinstance(last, dict) or error != last.get('error'):
            events.append(descriptor(source, 'Settings operation failed: ' + sanitize(error or 'no details reported'), source + '.error'))
    else:
        reset.append(source + '.error')
    return events, reset


def describe_event(tag, message):
    source = SOURCE_NAMES.get(tag, tag)
    message = text(message).strip()
    if tag in ('systemd', 'System') and ROLLBACK_POLL.match(message):
        return [], []
    if tag in ('python', 'python3', 'Network', 'Wi-Fi') and message.startswith('{'):
        try:
            body = json.loads(message)
        except ValueError:
            body = None
        if isinstance(body, dict) and 'ok' in body and 'pending' in body and 'last_result' in body:
            return helper_events(body)
    if tag in ('scripta_watchdog', 'Miner') and message.startswith('starting sgminer with runtime config: '):
        try:
            config = json.loads(message.split(': ', 1)[1])
            boards, pools = config['boards_assigned'], config['runtime_pool_count']
            if isinstance(boards, int) and isinstance(pools, int):
                message = 'Starting miner: %d boards assigned; %d pool connections' % (boards, pools)
        except (ValueError, KeyError, TypeError):
            pass
    message = re.sub(r'^<(?:info|warn|error|debug)>\s*\[[0-9.]+\]\s*', '', message)
    if tag in ('NetworkManager', 'Network'):
        match = re.match(r'device \(([^)]+)\): (.*)', message)
        if match:
            iface, message = match.groups()
            source = ('Wi-Fi' if iface.startswith('wl') else 'Ethernet' if iface.startswith(('eth', 'en')) else 'Network') + ' (' + sanitize(iface) + ')'
            transition = re.match(r"state change: ([\w-]+) -> ([\w-]+)(?: \(reason '([^']+)'.*)?", message)
            if transition:
                old, new, reason = transition.groups()
                states = {'activated': 'Connected', 'disconnected': 'Disconnected', 'unavailable': 'Unavailable',
                          'unmanaged': 'Not managed', 'prepare': 'Preparing connection', 'config': 'Configuring',
                          'need-auth': 'Authentication required', 'ip-config': 'Requesting IP address',
                          'ip-check': 'Checking IP address', 'secondaries': 'Finishing connection',
                          'deactivating': 'Disconnecting', 'failed': 'Connection failed'}
                message = states.get(old, old) + ' -> ' + states.get(new, new)
                if reason and reason != 'none':
                    message += '; ' + {'carrier-changed': 'network link changed', 'user-requested': 'requested by user'}.get(reason, reason.replace('-', ' '))
            elif message.startswith('Activation: successful'):
                message = 'Connected'
            else:
                start = re.match(r"Activation: starting connection '(.+)' \([\w-]+\)", message)
                if start:
                    message = 'Connecting to "' + sanitize(start.group(1)) + '"'
    if tag in ('wpa_supplicant', 'Wi-Fi'):
        match = re.match(r'(\w+): CTRL-EVENT-DISCONNECTED .*?reason=(\d+)(?:.*?locally_generated=(\d))?', message)
        if match:
            iface, reason, local = match.groups()
            source = 'Wi-Fi (' + iface + ')'
            message = 'Disconnected (reason ' + reason + ('; initiated locally' if local == '1' else '') + ')'
        else:
            match = re.match(r'(\w+): CTRL-EVENT-CONNECTED - Connection to ([\w:]+) completed', message)
            if match:
                source = 'Wi-Fi (' + match.group(1) + ')'
                message = 'Connected to access point ' + match.group(2)
    if tag in ('dhclient', 'DHCP'):
        match = re.match(r'bound to ([0-9.]+) -- renewal in (\d+) seconds', message)
        if match:
            message = 'Address assigned: ' + match.group(1) + '; lease renewal in ' + match.group(2) + 's'
    if tag in ('lighttpd', 'Web server'):
        message = re.sub(r'^\([\w.-]+\.\d+\)\s*', '', message)
        message = message.replace('FastCGI-stderr: ', '')
    if tag in ('fan', 'Fan'):
        match = re.match(r'auto=(True|False) status=(\S+) fan=(\d+)%\s*(.*)', message)
        if match:
            automatic, status, fan, reason = match.groups()
            message = ('Automatic' if automatic == 'True' else 'Manual') + ' fan: ' + fan + '%'
            labels = {'error': 'update failed', 'queued': 'waiting for miner', 'emergency': 'emergency cooling', 'disabled': 'automatic control off'}
            if status in labels:
                message += '; ' + labels[status]
            if reason:
                curve = re.match(r'curve ([0-9.]+) C between ', reason)
                if curve:
                    reason = 'hottest board ' + curve.group(1) + ' C'
                message += '; ' + reason
    return [descriptor(source, message)], []


def prepare_record(row):
    destinations = row.get('destinations', classify(row))
    events, reset = describe_event(row.get('SYSLOG_IDENTIFIER', 'kernel'), row.get('MESSAGE', '')) if destinations else ([], [])
    return {'__CURSOR': row.get('__CURSOR'), '__REALTIME_TIMESTAMP': row.get('__REALTIME_TIMESTAMP'),
            'destinations': destinations, 'events': events, 'reset': reset}


def changed_events(events, reset, seen):
    for key in reset:
        seen.pop(key, None)
    for event in events:
        key = event['key']
        if key:
            if seen.get(key) == event['signature']:
                continue
            seen[key] = event['signature']
        yield event


def readable_existing(data):
    lines, seen = [], {}
    for line in text(data).splitlines():
        match = LOG_LINE.match(line)
        if not match:
            lines.append(sanitize(line))
            continue
        stamp, tag, message = match.groups()
        events, reset = describe_event(tag, message)
        for event in changed_events(events, reset, seen):
            lines.append('%s %s: %s' % (stamp, event['source'], event['message']))
    return ('\n'.join(lines) + ('\n' if lines else '')).encode('utf-8')


def rewrite_existing():
    for key, ram, sd in SPECS.values():
        for path in (ram, sd):
            if not os.path.exists(path):
                continue
            if os.path.islink(path):
                raise IOError('refusing symlinked diagnostic log')
            with open(path, 'rb') as src:
                size = os.path.getsize(path)
                src.seek(max(0, size - MAX_BYTES))
                data = src.read(MAX_BYTES)
            if size > MAX_BYTES:
                data = data.partition(b'\n')[2]
            readable = readable_existing(data)
            if len(readable) > MAX_BYTES:
                readable = readable[-MAX_BYTES:].partition(b'\n')[2]
            if readable != data or size > MAX_BYTES:
                atomic_write(path, readable)


def text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    try:
        return unicode(value)
    except NameError:
        return str(value)


def sanitize(value):
    value = text(value)
    if SENSITIVE.search(value):
        return '[sensitive details omitted]'
    value = re.sub(r'(https?://[^\s?]+)\?\S+', r'\1?[query omitted]', value)
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', value)[:2048]


def read_json(path, default):
    try:
        with open(path) as src:
            value = json.load(src)
        return value if isinstance(value, dict) else default
    except (ValueError, IOError):
        return default


def permissions(path):
    try:
        os.chown(path, -1, grp.getgrnam('www-data').gr_gid)
    except OSError:
        if os.geteuid() == 0:
            raise
    os.chmod(path, 0o664)


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.diagnostic-', suffix='.tmp', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as dst:
            dst.write(data)
            dst.flush()
        permissions(tmp)
        os.rename(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_capped(path, lines, cap=MAX_BYTES):
    if not lines:
        return
    if os.path.islink(path):
        raise IOError('refusing symlinked diagnostic log')
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o775)
    payload = ''.join(line + '\n' for line in lines).encode('utf-8')
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size + len(payload) > cap:
        keep = cap // 2
        old = b''
        if len(payload) < keep and size:
            with open(path, 'rb') as src:
                src.seek(max(0, size - keep))
                old = src.read(keep)
        tail = (old + payload)[-keep:]
        tail = tail.partition(b'\n')[2]
        atomic_write(path, b'===== older diagnostic events trimmed =====\n' + tail)
    else:
        with open(path, 'ab') as dst:
            dst.write(payload)
        permissions(path)


def scrub_existing():
    for key, ram, sd in SPECS.values():
        for path in (ram, sd):
            if not os.path.exists(path):
                continue
            if os.path.islink(path):
                raise IOError('refusing symlinked diagnostic log')
            with open(path, 'rb') as src:
                size = os.path.getsize(path)
                src.seek(max(0, size - MAX_BYTES // 2))
                data = src.read(MAX_BYTES // 2)
            if size > MAX_BYTES // 2:
                data = data.partition(b'\n')[2]
            cleaned = '\n'.join(sanitize(line) for line in text(data).splitlines())
            atomic_write(path, (cleaned + ('\n' if cleaned else '')).encode('utf-8'))


def classify(record):
    message = record.get('MESSAGE', '')
    if not isinstance(message, (str, type(u''))):
        return []
    tag = record.get('SYSLOG_IDENTIFIER', '')
    unit = record.get('_SYSTEMD_UNIT', '')
    try:
        priority = int(record.get('PRIORITY', 6))
    except (ValueError, TypeError):
        priority = 6
    if tag == 'bs-diagnostic-logs':
        return []
    if tag == 'systemd' and ROLLBACK_POLL.match(message):
        return []
    if tag in ('lighttpd', 'php-cgi') or (unit == 'lighttpd.service' and record.get('_COMM') in ('lighttpd', 'php-cgi')):
        return ['lighttpd-error.log']
    if tag in ('bs-fan', 'bs-temp'):
        return ['cooling-events.log']
    result = []
    kernel = record.get('_TRANSPORT') == 'kernel'
    if kernel and 'wdt_restart' in message and not FAULT.search(message):
        return []
    if tag in ('NetworkManager', 'wpa_supplicant', 'dhclient') or unit.startswith('blakestream-network-') or unit.startswith('blakestream-wifi-') or (kernel and NETWORK.search(message)):
        result.append('network-events.log')
    if (kernel and (priority <= 4 or FAULT.search(message))) or (tag == 'systemd' and (priority <= 4 or (record.get('UNIT', '').endswith('.service') and LIFECYCLE.search(message)))) or tag in ('scripta_watchdog', 'bs-miner'):
        result.append('system-events.log')
    return result


def journal_command(cursor):
    command = ['/usr/bin/timeout', '15', '/bin/journalctl', '-b', '--no-pager', '-q', '-o', 'json', '-n', str(MAX_RECORDS)]
    if cursor:
        command += ['--after-cursor=' + cursor]
    return command


def read_journal(cursor):
    # Stream bounded JSON records rather than materializing the whole journal.
    proc = subprocess.Popen(journal_command(cursor), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    records = []
    oversized = False
    try:
        while True:
            line = proc.stdout.readline(65537)
            if not line:
                break
            if len(line) > 65536 or not line.endswith(b'\n'):
                oversized = True
                while line and not line.endswith(b'\n'):
                    line = proc.stdout.readline(65537)
                continue
            try:
                row = json.loads(line.decode('utf-8'))
            except (ValueError, UnicodeError):
                oversized = True
                continue
            if isinstance(row, dict):
                records.append(prepare_record(row))
        proc.stderr.read(4096)
        if proc.wait() != 0:
            raise IOError('journal read failed')
    finally:
        proc.stdout.close()
        proc.stderr.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
    return records, oversized


def event_line(tag, message, timestamp=None):
    stamp = datetime.datetime.utcfromtimestamp(time.time() if timestamp is None else timestamp)
    return '%s UTC %s: %s' % (stamp.strftime('%Y-%m-%d %H:%M:%S'), sanitize(tag), sanitize(message))


def cooling_changes(previous, fan, temperature):
    # Temperature values and timestamps are deliberately excluded from identity.
    fan_key = [fan.get(k) for k in ('enabled', 'status', 'fan')]
    if fan.get('status') == 'error':
        fan_key.append(sanitize(fan.get('reason', '')))
    boards = dict((key, bool(value.get('disabled'))) for key, value in temperature.items() if isinstance(value, dict))
    current = {'fan': fan_key if fan else [], 'boards': boards}
    lines = []
    if fan and current['fan'] != previous.get('fan'):
        message = 'auto=%s status=%s fan=%s%% %s' % (
            bool(fan.get('enabled')), fan.get('status', 'unknown'), fan.get('fan', '?'), fan.get('reason', ''))
        event = describe_event('fan', message)[0][0]
        lines.append(event_line(event['source'], event['message']))
    for board, disabled in sorted(boards.items()):
        old = previous.get('boards', {}).get(board)
        if disabled != old and (disabled or old is True):
            lines.append(event_line('Temperature', 'Board %s %s' % (board, 'reached temperature cutoff' if disabled else 'recovered below the temperature limit')))
    return current, lines


def collect(config, state, boot_id, records, fan, temperature, oversized=False):
    if state.get('version') != 1:
        scrub_existing()
    if state.get('readable_version') != 1:
        rewrite_existing()
    seen = dict(state.get('events_seen', {}))
    buckets = dict((key, []) for key in SPECS)
    if state.get('boot_id') != boot_id:
        buckets['system-events.log'].append(event_line('collector', 'boot ' + boot_id))
    if len(records) >= MAX_RECORDS or oversized:
        buckets['system-events.log'].append(event_line('collector', 'journal input limit reached; some events may be omitted'))
    cursor = state.get('cursor', '')
    for record in records:
        record = record if 'events' in record else prepare_record(record)
        cursor = record.get('__CURSOR') or cursor
        try:
            stamp = int(record['__REALTIME_TIMESTAMP']) / 1000000.0
        except (KeyError, ValueError, TypeError):
            stamp = time.time()
        for event in changed_events(record['events'], record['reset'], seen):
            line = event_line(event['source'], event['message'], stamp)
            for name in record['destinations']:
                buckets[name].append(line)
    cooling, lines = cooling_changes(state.get('cooling', {}), fan, temperature)
    buckets['cooling-events.log'].extend(lines)
    for name, (key, ram, sd) in SPECS.items():
        path = sd if config.get(key) == 'SD' else ram
        append_capped(path, buckets[name])
    return {'version': 1, 'readable_version': 1, 'events_seen': seen, 'boot_id': boot_id, 'cursor': cursor,
            'cooling': cooling, 'updated_at': int(time.time()), 'ok': True}


def main():
    if not os.path.isdir(RAM):
        os.makedirs(RAM, 0o775)
    lock = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o664)
    try:
        permissions(LOCK)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return 0
            raise
        with open('/proc/sys/kernel/random/boot_id') as src:
            boot_id = src.read().strip()
        state = read_json(STATE, {})
        cursor = state.get('cursor', '') if state.get('boot_id') == boot_id else ''
        try:
            try:
                records, oversized = read_journal(cursor)
            except IOError:
                if not cursor:
                    raise
                records, oversized = read_journal('')
                oversized = True
            config = read_json(CONFIG, None)
            if config is None:
                raise IOError('settings unavailable')
            result = collect(config, state, boot_id, records,
                             read_json(RAM + '/fan-control-state.json', {}),
                             read_json(RAM + '/temp-state.json', {}), oversized)
            atomic_write(STATE, json.dumps(result).encode('utf-8'))
            return 0
        except (IOError, OSError, ValueError):
            state['ok'] = False
            atomic_write(STATE, json.dumps(state).encode('utf-8'))
            sys.stderr.write('diagnostic log collection failed\n')
            return 1
    finally:
        os.close(lock)


if __name__ == '__main__':
    sys.exit(main())
