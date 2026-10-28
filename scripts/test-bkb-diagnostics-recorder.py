#!/usr/bin/env python
from __future__ import print_function, division

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin')
RECORDER_PATH = os.path.join(BIN, 'bkb-diagnostics-recorder.py')
REPORT_PATH = os.path.join(BIN, 'bkb-diagnostics-report.py')
COMBINED_PATH = os.path.join(BIN, 'combined-history-recorder.py')


class ModuleView(object):
    """Attribute and item access for a loaded module.

    Tests index the module as a dict (mod['record_once']) and also replace
    attributes (mod['sha256_file'] = ...). A raw module is not subscriptable.
    """

    def __init__(self, module):
        object.__setattr__(self, '_module', module)

    def __getitem__(self, key):
        return getattr(self._module, key)

    def __setitem__(self, key, value):
        setattr(self._module, key, value)

    def __getattr__(self, key):
        return getattr(self._module, key)


def load(name, path):
    try:
        import importlib.util
    except ImportError:
        import imp
        return ModuleView(imp.load_source(name, path))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return ModuleView(module)


mod = load('bkb_diagnostics_recorder_test', RECORDER_PATH)
rep = load('bkb_diagnostics_report_test', REPORT_PATH)


def fail(message):
    sys.stderr.write('FAIL: %s\n' % message)
    return 1


def paths_for(directory):
    host = os.path.join(directory, 'sgminer')
    if not os.path.exists(host):
        with open(host, 'wb') as handle:
            handle.write(b'host-binary-v1')
    return {
        'runtime': directory,
        'history': os.path.join(directory, 'bkb-diagnostics-history.json'),
        'latest': os.path.join(directory, 'bkb-diagnostics-latest.json'),
        'contrib': os.path.join(directory, 'contrib'),
        'hash_cache': os.path.join(directory, 'hash-cache.json'),
        'host_path': host,
        'firmware_dir': os.path.join(directory, 'fw'),
    }


def grid(index=None, special=0):
    values = [0] * 64
    if index is not None:
        values[index] = special
    return ','.join(str(value) for value in values)


def stat_row(**over):
    row = {
        'ID': 'BKLU0',
        'ASC': 0,
        'Chip Count': 64,
        'ClockMode': 'global',
        'RequestedClock': 400,
        'ClockGeneration': 4,
        'VerifiedClockGeneration': 4,
        'CommandVerified': True,
        'GlobalClockPending': False,
        'ClockRetryPending': False,
        'ClockReapplyPending': False,
        'GlobalClockInflight': False,
        'SentClockIndex': 2,
        'FanPercent': 80,
        'Effective Pool': 0,
        'Failover Active': False,
        'FWV': 12,
        'HWV': 1,
        'AsicVersion': 3,
        'LocalNoncesRaw': grid(3, 42424242),
        'LocalErrorsRaw': grid(),
        'DiagInvalidId': 5,
        'DiagUsbTxErrors': 2,
        'DiagUsbRxErrors': 1,
        'DiagReplyMismatch': 0,
        'AttemptedClockCode': 9,
        'DiagStatEpoch': 7,
        'DiagRouteEpoch': 4,
        'DiagEnableEpoch': 1,
        'DiagSnapMonoUs': 5000000,
        'DiagPid': 9,
        'DiagStartUnix': 100,
        'DiagPollGapUsLast': 20,
    }
    row.update(over)
    return row


def dev_row(**over):
    row = {
        'ASC': 0,
        'Name': 'BKLU',
        'Enabled': 'Y',
        'Status': 'Alive',
        'Accepted': 100,
        'Rejected': 1,
        'Hardware Errors': 2,
        'Difficulty Accepted': 3200.0,
        'Device Elapsed': 5000,
        'Temperature': 40.5,
        'Effective Pool': 0,
        'Failover Active': False,
    }
    row.update(over)
    return row


def pool_rows(url, user='wallet-user'):
    return [{'POOL': 0, 'URL': url, 'User': user, 'Password': 'SuperSecret9'}]


def snapshot(stat, dev, url, user='wallet-user'):
    return {
        'stats': {'STATS': [stat]},
        'devs': {'DEVS': [dev, {'ASC': 8, 'Name': 'OTHER', 'Enabled': 'Y', 'Status': 'Alive'}]},
        'pools': {'POOLS': pool_rows(url, user)},
    }


def tree_text(directory):
    chunks = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            with open(os.path.join(root, name), 'rb') as handle:
                chunks.append(handle.read().decode('utf-8', 'replace'))
    return '\n'.join(chunks)


def proc_for():
    return {'epoch': '9:100', 'epoch_after': '9:100', 'source': 'proc', 'exe': '/tmp/replaced-host'}


def test_endpoints():
    url = 'http://quota:2;stratum+tcp://wallet-user:SuperSecret9@eu3.example:3334/path?x=1#frag'
    if mod['endpoint_from_url'](url) != 'eu3.example:3334':
        return fail('legacy quota wrapper was not reduced to host:port')
    if mod['endpoint_from_url']('stratum+tcp://user:password@host:3334/x?y=1#z') != 'host:3334':
        return fail('userinfo URL was not reduced to host:port')
    if mod['userinfo_secrets'](url) != ['SuperSecret9']:
        return fail('password was not harvested from the wrapped URL')
    if rep['endpoint_from_url'](url) != mod['endpoint_from_url'](url):
        return fail('report endpoint parser disagrees with the recorder')
    if rep['DIAG_MONOTONIC'] != mod['DIAG_MONOTONIC']:
        return fail('report monotonic counters drifted from the recorder')
    return 0


def test_bounds_and_registration():
    source = open(RECORDER_PATH, 'rb').read().decode('utf-8')
    for banned in ('userAdjustableClocks', 'saved-pools', 'miner.options', 'flashed'):
        if banned in source:
            return fail('recorder source contains %s' % banned)
    combined = open(COMBINED_PATH, 'rb').read().decode('utf-8')
    if "('bs-diagnostics', 'bkb-diagnostics-recorder.py')" not in combined:
        return fail('combined recorder is not registered')
    if mod['RETENTION_SECONDS'] < 12 * 3600 or mod['RETENTION_SECONDS'] > 24 * 3600:
        return fail('retention is outside 12h..24h')
    if mod['MAX_SAMPLES_PER_BOARD'] < 12 * 60:
        return fail('sample cap cannot hold a 12h leg')
    if mod['MAX_API_BYTES'] > 256 * 1024 or mod['MAX_API_SECONDS'] <= 0:
        return fail('API reply bounds are missing')

    class Clock(object):
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    class Sock(object):
        def __init__(self, pieces, clock, step):
            self.pieces = list(pieces)
            self.clock = clock
            self.step = step

        def settimeout(self, value):
            self.timeout = value

        def recv(self, _size):
            self.clock.now += self.step
            if not self.pieces:
                return b''
            return self.pieces.pop(0)

    clock = Clock()
    oversized = mod['read_bounded'](Sock([b'x' * 1000] * 10, clock, 0), max_bytes=2500, max_seconds=30, clock=clock)
    if oversized is not None:
        return fail('API byte cap was not enforced')
    clock = Clock()
    timed = mod['read_bounded'](Sock([b'abc'] * 10, clock, 3), max_bytes=100000, max_seconds=4, clock=clock)
    if timed is not None:
        return fail('API total deadline was not enforced')
    original = mod['socket'].socket

    def explode(*_args, **_kwargs):
        raise AssertionError('socket opened')

    mod['socket'].socket = explode
    try:
        try:
            mod['call_api']('ascdisable')
        except ValueError:
            pass
        else:
            return fail('non-read API command was not refused')
    finally:
        mod['socket'].socket = original
    return 0


def record(directory, stat, dev, url, wall, user='wallet-user'):
    return mod['record_once'](
        snapshot(stat, dev, url, user), paths_for(directory), wall, float(wall), proc=proc_for())


def test_capture_and_window():
    directory = tempfile.mkdtemp(prefix='bkb-cap-')
    secret = 'SuperSecret9'
    url = 'http://quota:2;stratum+tcp://wallet-user:%s@eu3.example:3334/path?x=1#frag' % secret
    try:
        first = stat_row(Note=secret, DiagTrace='TRACEPAYLOAD9', FirmwareLabel='v1-' + secret)
        second = stat_row(
            Note=secret, DiagTrace='TRACEPAYLOAD9', FirmwareLabel='v1-' + secret,
            DiagSnapMonoUs=65000000)
        dev1 = dev_row()
        dev2 = dev_row(Accepted=120, **{'Difficulty Accepted': 3840.0, 'Device Elapsed': 5060})
        if record(directory, first, dev1, url, 1000) is None:
            return fail('first capture did not write')
        if record(directory, second, dev2, url, 1060) is None:
            return fail('second capture did not write')
        text = tree_text(directory)
        if secret in text or 'TRACEPAYLOAD9' in text:
            return fail('secret or trace payload leaked into diagnostic files')
        history = mod['read_json'](paths_for(directory)['history'])
        latest = mod['read_json'](paths_for(directory)['latest'])
        sample = history['boards'][0]['samples'][-1]
        if sample.get('ss') is not None or sample.get('ac') != 64:
            return fail('chip count was copied into startup success or lost')
        if 'startup_success' not in latest['boards'][0]['missing']:
            return fail('absent startup success was not listed as missing')
        missing = latest['boards'][0]['missing']
        if 'share_snap_valid' not in missing or 'device_elapsed_valid' not in missing or sample.get('sync') != 'unsynchronized':
            return fail('devs/stats reads without DiagShareSnapValid were labeled as a co-snapshot')
        if sample.get('se') != 7 or sample.get('ses') != 'd':
            return fail('DiagStatEpoch was not consumed')
        if sample.get('re') != 4 or sample.get('rs') != 'd' or sample.get('es') != 'd':
            return fail('driver route/enable epochs were not consumed')
        if sample.get('pool') != 'eu3.example:3334' or sample.get('wk') != 'wallet-user':
            return fail('pool endpoint or worker was not stored')
        if sample.get('diag', {}).get('usb_tx_error') != 2 or sample.get('diag', {}).get('attempted_clock_code') != 9:
            return fail('DiagUsbTxErrors or AttemptedClockCode was missed')
        if sample.get('diag', {}).get('reply_mismatch') != 0 or sample.get('pend') != 0:
            return fail('reply mismatch or settled pending flags were wrong')
        if latest['boards'][0]['grid']['nonce'][3] != 42424242:
            return fail('LocalNoncesRaw did not reach the latest snapshot')
        history_text = open(paths_for(directory)['history'], 'rb').read().decode('utf-8')
        if '42424242' in history_text:
            return fail('raw grid was copied into compact history')
        contrib = open(os.path.join(paths_for(directory)['contrib'], mod['segment_name'](1060)), 'rb').read().decode('utf-8')
        if '42424242' not in contrib:
            return fail('raw grid was not appended to contribution history')
        if len(history['boards'][0]['samples']) != 2:
            return fail('non-BKLU dev was recorded')
        window = rep['exact_window'](history, 1000, 1060, boards=(0,))
        expected = (640.0 * 4294967296.0) / 60.0 / 1000000000.0
        if not window['valid'] or abs(window['boards']['0']['ghs'] - expected) > 0.000001:
            return fail('captured window did not match measured GH/s: %s' % window['reasons'])
        if 'unsynchronized-api-reads' not in window['notes']:
            return fail('unsynchronized reads were not noted')
        if latest['host']['provenance'] != mod['PROVENANCE_HOST_CONFIGURED']:
            return fail('configured host path was claimed as the running binary')
    finally:
        shutil.rmtree(directory)
    return 0


def test_pending_and_cosnapshot():
    directory = tempfile.mkdtemp(prefix='bkb-pend-')
    try:
        stat = stat_row()
        del stat['ClockRetryPending']
        result = record(directory, stat, dev_row(), 'stratum+tcp://eu3.example:3334', 2000)
        sample = result['latest']['boards'][0]['sample']
        if sample.get('pend') is not None or 'clock_pending' not in result['latest']['boards'][0]['missing']:
            return fail('absent pending flag was treated as settled')
        guessed = stat_row(
            DiagCoSnapshot=True,
            **{
                'Difficulty Accepted': 3200.0,
                'Accepted': 100,
                'Device Elapsed': 5000,
            })
        guessed_sample = record(directory, guessed, dev_row(), 'stratum+tcp://eu3.example:3334', 2060)
        if guessed_sample['latest']['boards'][0]['sample'].get('sync') != 'unsynchronized':
            return fail('DiagCoSnapshot was treated as a locked share snapshot')
        stat = stat_row(
            DiagShareSnapValid=1,
            DiagDeviceElapsedValid=1,
            DiagAccepted=111,
            DiagRejected=4,
            DiagHwErrors=5,
            DiagDiffAccepted=3552.0,
            DiagDiffRejected=8.0,
            DiagDeviceElapsed=5000.5,
        )
        result = record(directory, stat, dev_row(), 'stratum+tcp://eu3.example:3334', 2120)
        synced = result['latest']['boards'][0]['sample']
        if synced.get('sync') != 'co-snapshot' or synced.get('a') != 111 or synced.get('dr') != 8.0:
            return fail('DiagShareSnapValid share set was not consumed: %s' % synced.get('sync'))
        if synced.get('de') != 5000.5 or synced.get('da') != 3552.0 or synced.get('hw') != 5:
            return fail('locked share fields fell back to the devs row')
        partial = stat_row(DiagShareSnapValid=1, DiagAccepted=111, DiagDeviceElapsed=10)
        partial_sample = record(directory, partial, dev_row(), 'stratum+tcp://eu3.example:3334', 2180)
        if partial_sample['latest']['boards'][0]['sample'].get('sync') != 'unsynchronized':
            return fail('incomplete share snapshot was labeled co-snapshot')
        if partial_sample['latest']['boards'][0]['sample'].get('a') != 100:
            return fail('invalid share snapshot replaced the legacy accepted count')
    finally:
        shutil.rmtree(directory)
    return 0


def test_epoch_fallback_and_routes():
    directory = tempfile.mkdtemp(prefix='bkb-epoch-')
    url = 'stratum+tcp://eu3.example:3334'
    try:
        record(directory, stat_row(), dev_row(), url, 3000)
        stat = stat_row()
        for key in ('DiagStatEpoch', 'DiagRouteEpoch', 'DiagEnableEpoch'):
            del stat[key]
        result = record(directory, stat, dev_row(Accepted=110, **{'Difficulty Accepted': 3520.0, 'Device Elapsed': 5060}), url, 3060)
        sample = result['history']['boards'][0]['samples'][-1]
        if sample.get('se') is not None or sample.get('re') is not None or sample.get('ee') is not None:
            return fail('missing driver epoch fell back to a collector epoch')
        directory2 = tempfile.mkdtemp(prefix='bkb-route-')
        try:
            pools = [
                'stratum+tcp://alpha.example:3334',
                'stratum+tcp://beta.example:3334',
                'stratum+tcp://alpha.example:3334',
                'stratum+tcp://alpha.example:3334',
            ]
            for index, pool in enumerate(pools):
                stat = stat_row(DiagSnapMonoUs=None)
                for key in ('DiagStatEpoch', 'DiagRouteEpoch', 'DiagEnableEpoch'):
                    del stat[key]
                dev = dev_row(
                    Accepted=100 + index * 10,
                    **{'Difficulty Accepted': 3200.0 + index * 320, 'Device Elapsed': 5000 + index * 60})
                record(directory2, stat, dev, pool, 4000 + index * 60)
            history = mod['read_json'](paths_for(directory2)['history'])
            epochs = [sample['re'] for sample in history['boards'][0]['samples']]
            if epochs != [0, 1, 2, 2]:
                return fail('collector route epochs did not record away-and-back: %s' % epochs)
            full = rep['exact_window'](history, 4000, 4120, boards=(0,))
            if full['valid'] or 'route-epoch' not in full['reasons']:
                return fail('away-and-back window was accepted')
            back = rep['exact_window'](history, 4120, 4180, boards=(0,))
            if not back['valid'] or 'collector-epoch' not in back['notes']:
                return fail('return stretch was not measurable, or its weaker epoch was hidden')
        finally:
            shutil.rmtree(directory2)
        directory3 = tempfile.mkdtemp(prefix='bkb-en-')
        try:
            enabled = ['Y', 'Y', 'N', 'Y', 'Y']
            for index, flag in enumerate(enabled):
                stat = stat_row(DiagSnapMonoUs=None)
                for key in ('DiagStatEpoch', 'DiagRouteEpoch', 'DiagEnableEpoch'):
                    del stat[key]
                dev = dev_row(
                    Enabled=flag,
                    Accepted=100 + index * 10,
                    **{'Difficulty Accepted': 3200.0 + index * 320, 'Device Elapsed': 5000 + index * 60})
                record(directory3, stat, dev, url, 5000 + index * 60)
            history = mod['read_json'](paths_for(directory3)['history'])
            span = rep['exact_window'](history, 5000, 5240, boards=(0,))
            if span['valid'] or 'disabled' not in span['reasons']:
                return fail('disabled sample did not invalidate the span')
            first = rep['exact_window'](history, 5000, 5060, boards=(0,))
            last = rep['exact_window'](history, 5180, 5240, boards=(0,))
            if not first['valid'] or not last['valid']:
                return fail('enabled stretches around a disable were rejected')
        finally:
            shutil.rmtree(directory3)
        directory4 = tempfile.mkdtemp(prefix='bkb-drv-')
        try:
            for index, epoch in enumerate((4, 5)):
                dev = dev_row(
                    Accepted=100 + index * 10,
                    **{'Difficulty Accepted': 3200.0 + index * 320, 'Device Elapsed': 5000 + index * 60})
                record(directory4, stat_row(DiagRouteEpoch=epoch, DiagSnapMonoUs=5000000 + index * 60000000), dev, url, 6000 + index * 60)
            history = mod['read_json'](paths_for(directory4)['history'])
            window = rep['exact_window'](history, 6000, 6060, boards=(0,))
            if window['valid'] or 'route-epoch' not in window['reasons'] or 'route-change' in window['reasons']:
                return fail('driver route epoch change with a stable endpoint was not consumed')
        finally:
            shutil.rmtree(directory4)
    finally:
        shutil.rmtree(directory)
    return 0


def test_process_epoch_units():
    proc = {
        'pid': 123,
        'epoch': '123:7654321',
        'epoch_after': '123:7654321',
        'source': 'proc',
    }
    stat = stat_row(DiagPid=123, DiagStartUnix=1790640000)
    sample, _extra = mod['build_one'](2, stat, {}, {}, proc, 1790640060.0, 60.0)
    if sample.get('pe') != '123:1790640000' or sample.get('pt') != '123:7654321':
        return fail('unix start and proc ticks were compared or dropped: pe=%s pt=%s' % (sample.get('pe'), sample.get('pt')))
    if sample.get('ps') != 'api' or 'process_epoch' in (sample.get('bad') or []):
        return fail('matched pid was not recorded as the API unix epoch')
    proc['pid'] = 99
    proc['epoch'] = '99:7654321'
    proc['epoch_after'] = '99:7654321'
    mismatched, _extra = mod['build_one'](2, stat, {}, {}, proc, 1790640060.0, 60.0)
    if mismatched.get('pe') is not None or mismatched.get('pt') != '99:7654321':
        return fail('a different proc pid was merged into the API epoch')
    return 0


def test_hash_cache_and_running_inode():
    directory = tempfile.mkdtemp(prefix='bkb-hash-')
    calls = {'n': 0}
    original = mod['sha256_file']

    def counted(path):
        calls['n'] += 1
        return original(path)

    mod['sha256_file'] = counted
    try:
        record(directory, stat_row(), dev_row(), 'stratum+tcp://eu3.example:3334', 7000)
        record(directory, stat_row(DiagSnapMonoUs=5060000), dev_row(Accepted=110, **{
            'Difficulty Accepted': 3520.0, 'Device Elapsed': 5060}), 'stratum+tcp://eu3.example:3334', 7060)
        if calls['n'] != 1:
            return fail('unchanged host file was hashed %d times' % calls['n'])
    finally:
        mod['sha256_file'] = original
        shutil.rmtree(directory)

    sleep_src = None
    for candidate in ('/bin/sleep', '/usr/bin/sleep'):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            sleep_src = candidate
            break
    if sleep_src is None:
        return fail('sleep binary is required for the running-inode test')
    directory = tempfile.mkdtemp(prefix='bkb-exe-')
    path = os.path.join(directory, 'sleep')
    shutil.copy(sleep_src, path)
    os.chmod(path, 0o755)
    original_sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    proc = subprocess.Popen([path, '60'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        epoch = None
        for _ignored in range(20):
            epoch = mod['read_proc_epoch'](proc.pid)
            if epoch and proc.poll() is None:
                break
            time.sleep(0.05)
        if not epoch or proc.poll() is not None:
            return fail('sleep process did not stay up for the inode test')
        first = mod['hash_proc_exe'](proc.pid, {'v': 1, 'files': {}}, epoch)
        if first is None or first['sha256'] != original_sha or first['provenance'] != mod['PROVENANCE_HOST_RUNNING']:
            return fail('open /proc/pid/exe did not hash the running inode')
        replacement = path + '.new'
        with open(replacement, 'wb') as handle:
            handle.write(b'not-the-running-inode\n')
        os.rename(replacement, path)
        replaced_sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()

        def boom(_path):
            raise AssertionError('hashed a pathname instead of /proc/pid/exe')

        mod['sha256_file'] = boom
        try:
            second = mod['hash_proc_exe'](proc.pid, {'v': 1, 'files': {}}, epoch)
            host_paths = paths_for(directory)
            host_paths['host_path'] = path
            resolved = mod['resolve_host'](host_paths, {
                'pid': proc.pid,
                'epoch': epoch,
                'epoch_after': epoch,
                'exe': path,
                'source': 'proc',
            }, {'v': 1, 'files': {}})
        finally:
            mod['sha256_file'] = original
        if second is None or second['sha256'] != original_sha or second['sha256'] == replaced_sha:
            return fail('replaced pathname changed the running-binary hash')
        if second['path'] != '/proc/%d/exe' % proc.pid:
            return fail('running hash recorded the replaced pathname')
        if resolved is None or resolved['sha256'] != original_sha or resolved['provenance'] != mod['PROVENANCE_HOST_RUNNING']:
            return fail('resolve_host followed proc exe pathname')
        unstable = mod['resolve_host'](host_paths, {
            'pid': proc.pid,
            'epoch': epoch,
            'epoch_after': epoch + 'x',
            'unstable': True,
            'exe': path,
            'source': 'proc',
        }, {'v': 1, 'files': {}})
        if unstable is None or unstable['provenance'] != mod['PROVENANCE_HOST_CONFIGURED']:
            return fail('unstable process still claimed a running-binary hash')
        lone = tempfile.mkdtemp(prefix='bkb-unstable-')
        try:
            result = mod['record_once'](snapshot(
                stat_row(), dev_row(), 'stratum+tcp://eu3.example:3334'), paths_for(lone), 8000, 8000.0, proc={
                    'epoch': '9:100', 'epoch_after': '9:999', 'source': 'proc'})
            if result['history']['boards'][0]['samples'][-1].get('pe') is not None:
                return fail('process epoch that changed around the snapshot was still recorded')
        finally:
            shutil.rmtree(lone)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        mod['sha256_file'] = original
        shutil.rmtree(directory)
    return 0


def test_driver_host_sha():
    digest = 'ab' * 32
    other = 'cd' * 32
    sleep_src = None
    for candidate in ('/bin/sleep', '/usr/bin/sleep'):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            sleep_src = candidate
            break
    if sleep_src is None:
        return fail('sleep binary is required for the driver-hash test')
    directory = tempfile.mkdtemp(prefix='bkb-driver-sha-')
    path = os.path.join(directory, 'sleep')
    shutil.copy(sleep_src, path)
    os.chmod(path, 0o755)
    paths = paths_for(directory)
    inode_sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    file_sha = hashlib.sha256(open(paths['host_path'], 'rb').read()).hexdigest()
    if inode_sha == digest or file_sha == digest or inode_sha == file_sha:
        shutil.rmtree(directory)
        return fail('fixture hashes collided')
    process = subprocess.Popen([path, '60'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    real_open = mod['os'].open
    try:
        epoch = None
        for _ignored in range(20):
            epoch = mod['read_proc_epoch'](process.pid)
            if epoch and process.poll() is None:
                break
            time.sleep(0.05)
        if not epoch or process.poll() is not None:
            return fail('sleep process did not stay up for the driver-hash test')
        base_proc = {
            'pid': process.pid,
            'epoch': epoch,
            'epoch_after': epoch,
            'source': 'proc',
            'exe': path,
        }

        def rows(sha=digest, pid=None, start=1790640000, count=3):
            use_pid = process.pid if pid is None else pid
            built = []
            for board in range(count):
                built.append(stat_row(
                    ID='BKLU%d' % board, ASC=board, DiagPid=use_pid,
                    DiagStartUnix=start, DiagHostSha256=sha))
            return built

        def resolved(stat_rows, process_info=None):
            return mod['resolve_host'](
                paths, base_proc if process_info is None else process_info,
                {'v': 1, 'files': {}}, {'STATS': stat_rows})

        preferred = resolved(rows(sha=other))
        if (preferred is None or preferred.get('sha256') != inode_sha or
                preferred.get('provenance') != mod['PROVENANCE_HOST_RUNNING'] or
                preferred.get('source') or not str(preferred.get('path', '')).endswith('/exe')):
            return fail('open inode hash lost to DiagHostSha256 or the configured file')

        def deny_exe(target, flags, mode=0o777):
            if str(target).endswith('/exe'):
                raise OSError(13, 'Permission denied')
            return real_open(target, flags, mode)

        mod['os'].open = deny_exe
        try:
            accepted = resolved(rows())
            if (accepted is None or accepted.get('sha256') != digest or
                    accepted.get('provenance') != mod['PROVENANCE_HOST_RUNNING'] or
                    accepted.get('source') != mod['DRIVER_HOST_SHA'] or
                    accepted.get('path') is not None):
                return fail('stable DiagHostSha256 was not running provenance: %s' % accepted)
            if resolved(rows(count=1)) is None or resolved(rows(count=1)).get('sha256') != digest:
                return fail('one agreeing board was rejected')
            summary = rows()
            summary.append({
                'ID': 'SUMMARY', 'DiagHostSha256': other,
                'DiagPid': 1, 'DiagStartUnix': 1,
            })
            if resolved(summary) is None or resolved(summary).get('sha256') != digest:
                return fail('a non-board stats row changed the running hash')

            devs = []
            for board in range(3):
                devs.append(dev_row(ASC=board))
            captured = mod['record_once']({
                'stats': {'STATS': rows()},
                'devs': {'DEVS': devs},
                'pools': {'POOLS': pool_rows('stratum+tcp://eu3.example:3334')},
            }, paths, 3000, 3000.0, proc=base_proc)
            host = captured['history']['host'][0]
            if (host.get('sha256') != digest or host.get('source') != mod['DRIVER_HOST_SHA'] or
                    host.get('provenance') != mod['PROVENANCE_HOST_RUNNING'] or
                    host.get('path') is not None):
                return fail('history did not keep the driver running hash')
            if captured['latest']['host']['sha256'] != digest:
                return fail('latest host fell back to the configured file')
            for board in captured['latest']['boards']:
                sample = board['sample']
                if sample.get('hp') != mod['PROVENANCE_HOST_RUNNING']:
                    return fail('sample provenance was not the running image')
                if mod['DRIVER_HOST_SHA'] in (board.get('ignored_stats_keys') or []):
                    return fail('DiagHostSha256 was ignored instead of consumed')
                if digest in json.dumps(sample):
                    return fail('driver digest was copied onto the sample')
            cache = mod['read_json'](paths['hash_cache'])
            cached = (cache.get('files') or {}).get(paths['host_path'])
            if cached and cached.get('sha256') == digest:
                return fail('driver digest was cached as the configured file')

            rejects = []
            missing = rows()
            del missing[1]['DiagHostSha256']
            rejects.append(('missing hash', missing, None))
            empty = rows(sha='')
            rejects.append(('empty hash', empty, None))
            rejects.append(('uppercase hash', rows(sha='AB' * 32), None))
            rejects.append(('short hash', rows(sha='abcd'), None))
            spaced = rows()
            spaced[0]['DiagHostSha256'] = ' ' + ('ab' * 31) + 'c'
            rejects.append(('spaced hash', spaced, None))
            numbered = rows()
            numbered[0]['DiagHostSha256'] = 123
            rejects.append(('non-string hash', numbered, None))
            nulled = rows()
            nulled[2]['DiagHostSha256'] = None
            rejects.append(('null hash', nulled, None))
            conflict = rows()
            conflict[1]['DiagHostSha256'] = other
            rejects.append(('conflicting hash', conflict, None))
            lost_pid = rows()
            del lost_pid[0]['DiagPid']
            rejects.append(('missing pid', lost_pid, None))
            pid_bad = rows()
            pid_bad[2]['DiagPid'] = process.pid + 1
            rejects.append(('conflicting pid', pid_bad, None))
            lost_start = rows()
            del lost_start[1]['DiagStartUnix']
            rejects.append(('missing start', lost_start, None))
            start_bad = rows()
            start_bad[0]['DiagStartUnix'] = 1790640001
            rejects.append(('conflicting start', start_bad, None))
            extra = rows()
            extra.append(stat_row(
                ID='BKLU9', ASC=9, DiagPid=process.pid,
                DiagStartUnix=1790640000, DiagHostSha256=other))
            rejects.append(('extra board hash', extra, None))
            slid = dict(base_proc)
            slid['epoch_after'] = epoch + '9'
            rejects.append(('tick epoch changed', rows(), slid))
            unstable = dict(base_proc)
            unstable['unstable'] = True
            rejects.append(('unstable epoch', rows(), unstable))
            dropped = dict(base_proc)
            dropped['epoch_after'] = None
            rejects.append(('missing after epoch', rows(), dropped))
            split = dict(base_proc)
            ticks = epoch.split(':', 1)[1]
            split['epoch'] = '1:%s' % ticks
            split['epoch_after'] = split['epoch']
            rejects.append(('pid disagrees with tick epoch', rows(), split))
            for name, stat_rows, process_info in rejects:
                got = resolved(stat_rows, process_info)
                if (got is None or got.get('provenance') != mod['PROVENANCE_HOST_CONFIGURED'] or
                        got.get('sha256') != file_sha or got.get('source')):
                    return fail('%s became running provenance: %s' % (name, got))
        finally:
            mod['os'].open = real_open
    finally:
        mod['os'].open = real_open
        if process.poll() is None:
            process.kill()
        process.wait()
        shutil.rmtree(directory)
    return 0


def test_atomic_corrupt_retention():
    directory = tempfile.mkdtemp(prefix='bkb-atom-')
    original = mod['json'].dumps
    try:
        url = 'stratum+tcp://eu3.example:3334'
        record(directory, stat_row(), dev_row(), url, 9000)
        before = open(paths_for(directory)['history'], 'rb').read()

        def interrupt(*_args, **_kwargs):
            raise RuntimeError('interrupted encoding')

        mod['json'].dumps = interrupt
        try:
            try:
                record(directory, stat_row(), dev_row(Accepted=110), url, 9060)
            except RuntimeError:
                pass
            else:
                return fail('interrupted encoding was ignored')
        finally:
            mod['json'].dumps = original
        after = open(paths_for(directory)['history'], 'rb').read()
        if after != before:
            return fail('interrupted write replaced history')
        if any(name.endswith('.tmp') for name in os.listdir(directory)):
            return fail('interrupted write left a temporary file')
        with open(paths_for(directory)['history'], 'wb') as handle:
            handle.write(b'{')
        if mod['record_once'](snapshot(stat_row(), dev_row(), url), paths_for(directory), 9120, 9120.0) is not None:
            return fail('corrupt history was overwritten')
        if open(paths_for(directory)['history'], 'rb').read() != b'{':
            return fail('corrupt history bytes changed')
    finally:
        mod['json'].dumps = original
        shutil.rmtree(directory)

    directory = tempfile.mkdtemp(prefix='bkb-retain-')
    try:
        url = 'stratum+tcp://eu3.example:3334'
        for index, wall in enumerate((0, 1500, 2000)):
            dev = dev_row(
                Accepted=100 + index,
                **{'Difficulty Accepted': 3200.0 + index * 32, 'Device Elapsed': 1000 + index * 60})
            mod['record_once'](
                snapshot(stat_row(DiagSnapMonoUs=1000000 + index), dev, url),
                paths_for(directory), wall, float(wall), proc=proc_for(), retain_seconds=1000)
        stamps = [sample['t'] for sample in mod['read_json'](paths_for(directory)['history'])['boards'][0]['samples']]
        if stamps != [1500, 2000]:
            return fail('retention did not keep the newest samples: %s' % stamps)
        for index, wall in enumerate((3000, 3060, 3120)):
            dev = dev_row(
                Accepted=200 + index,
                **{'Difficulty Accepted': 4000.0 + index * 32, 'Device Elapsed': 2000 + index * 60})
            mod['record_once'](
                snapshot(stat_row(DiagSnapMonoUs=2000000 + index), dev, url),
                paths_for(directory), wall, float(wall), proc=proc_for(),
                retain_seconds=100000, max_samples=2)
        stamps = [sample['t'] for sample in mod['read_json'](paths_for(directory)['history'])['boards'][0]['samples']]
        if stamps != [3060, 3120]:
            return fail('sample cap did not keep the newest two: %s' % stamps)
        contrib = paths_for(directory)['contrib']
        if not os.path.isdir(contrib):
            os.makedirs(contrib)
        seeded = os.path.join(contrib, 'c-0000000000.jsonl')
        with open(seeded, 'wb') as handle:
            handle.write(b'{"seed":1}\n')
        inode = os.stat(seeded).st_ino
        dev = dev_row(Accepted=10, **{'Difficulty Accepted': 320.0, 'Device Elapsed': 10})
        mod['record_once'](
            snapshot(stat_row(), dev, url), paths_for(directory), 10, 10.0,
            proc=proc_for(), retain_seconds=100000)
        data = open(seeded, 'rb').read()
        if os.stat(seeded).st_ino != inode or not data.startswith(b'{"seed":1}\n') or data.count(b'\n') < 2:
            return fail('contribution segment was rewritten instead of appended')
        dev = dev_row(Accepted=11, **{'Difficulty Accepted': 352.0, 'Device Elapsed': 70})
        mod['record_once'](
            snapshot(stat_row(), dev, url), paths_for(directory), 10000, 10000.0,
            proc=proc_for(), retain_seconds=100)
        if os.path.exists(seeded):
            return fail('expired contribution segment was kept')
        if not os.path.exists(os.path.join(contrib, mod['segment_name'](10000))):
            return fail('new contribution segment was not created')
    finally:
        shutil.rmtree(directory)
    return 0


def mem_child():
    import gc
    import resource
    gc.collect()
    paths = mod['default_paths']()
    last = float(os.environ['BKB_DIAG_MEM_LAST'])
    clock_rss = 0
    if os.environ.get('BKB_DIAG_MEM_MODE') == 'combined':
        clock = load('clock_combined_memory', os.path.join(BIN, 'clock-history-recorder.py'))
        clock_path = os.path.join(paths['runtime'], 'clock-seed.json')
        history = clock.load_json(clock_path, {}, compact=True)
        for board in history['boards']:
            clock.accepted_work_rate(board['samples'], 3600)
        clock.write_json_atomic(clock_path + '.result', history)
        del history
        gc.collect()
        clock_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if os.environ.get('BKB_DIAG_MEM_MODE') == 'plain':
        original_read = mod['read_json']
        mod['read_json'] = lambda path, compact=False: original_read(path)
        # Baseline: decoded dicts plus a complete encoded copy, as before.
        mod['write_history_json'] = lambda handle, obj, **kwargs: handle.write(
            json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False))
    snapshot_now = snapshot(
        stat_row(DiagStatEpoch=1, DiagRouteEpoch=1, DiagEnableEpoch=1),
        dev_row(Accepted=10, **{'Difficulty Accepted': 320.0, 'Device Elapsed': 60000}),
        'stratum+tcp://eu3.example:3334')
    started = time.time()
    result = mod['record_once'](
        snapshot_now, paths, last + 60, float(last + 60),
        proc={'epoch': '9:100', 'epoch_after': '9:100', 'source': 'proc'})
    elapsed = time.time() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    size = os.path.getsize(paths['history'])
    kept = len(result['history']['boards'][0]['samples'])
    sys.stdout.write('MEM rss_kb=%d clock_rss_kb=%d elapsed=%.3f bytes=%d kept=%d\n' % (rss, clock_rss, elapsed, size, kept))
    return 0


def write_memory_fixture(directory):
    paths_for(directory)
    os.makedirs(os.path.join(directory, 'fw'))
    base = 1000000
    names = list(mod['DIAG_MONOTONIC'])
    seed = record(directory, stat_row(), dev_row(), 'stratum+tcp://eu3.example:3334', base)
    template = seed['history']['boards'][0]['samples'][0]
    boards = []
    for bid in (0, 1, 2):
        samples = []
        for index in range(18 * 60):
            sample = dict(template)
            sample.update({
                't': base + index * 60, 'tm': base + index * 60,
                'a': index, 'da': index * 32, 'de': index * 60,
                'se': 1, 'ses': 'd', 're': 1, 'rs': 'd', 'ee': 1, 'es': 'd',
                'pe': '9:100', 'ps': 'api',
                'diag': dict((name, index) for name in names),
            })
            samples.append(sample)
        boards.append({'id': bid, 'samples': samples})
    history = {'v': 1, 'step': 60, 'retain': mod['RETENTION_SECONDS'],
               'updated': base, 'host': [], 'artifacts': [], 'boards': boards}
    with open(os.path.join(directory, 'seed.json'), 'w') as handle:
        json.dump(history, handle)
    clock_test = load('clock_fixture', os.path.join(ROOT, 'scripts', 'test-clock-history-memory.py'))
    clock_history = {'boards': [
        {'id': bid, 'samples': [clock_test.sample(base + i * 60) for i in range(10081)]}
        for bid in range(3)
    ]}
    with open(os.path.join(directory, 'clock-seed.json'), 'w') as handle:
        json.dump(clock_history, handle, separators=(',', ':'))


def memory_check():
    directory = tempfile.mkdtemp(prefix='bkb-mem-')
    try:
        env = os.environ.copy()
        # Use one allocator for every measurement. Python arena retention can
        # otherwise skew relative RSS across interpreter versions.
        env['PYTHONMALLOC'] = 'malloc'
        # Generate the fixture in a separate process: its heap must not become
        # the inherited RSS high-water floor of either measurement child.
        subprocess.check_call([sys.executable, os.path.abspath(__file__), '--memory-seed', directory],
                              env=env)
        env['BKB_DIAG_MEM_CHILD'] = '1'
        env['BKB_DIAG_MEM_LAST'] = str(1000000 + (18 * 60 - 1) * 60)
        env['BKB_DIAG_RUNTIME'] = directory
        env['BKB_DIAG_HOST_PATH'] = paths_for(directory)['host_path']
        env['BKB_DIAG_FIRMWARE_DIR'] = os.path.join(directory, 'fw')
        results = {}
        for mode in ('plain', 'compact', 'combined'):
            shutil.copyfile(os.path.join(directory, 'seed.json'), paths_for(directory)['history'])
            env['BKB_DIAG_MEM_MODE'] = mode
            proc = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                # Linux vfork/exec can inherit the parent's RSS high-water
                # mark even after exec. Force fork from this small, single-
                # threaded runner so each measurement starts independently.
                preexec_fn=lambda: None)
            out, err = proc.communicate()
            if not isinstance(out, str):
                out = out.decode('utf-8', 'replace')
                err = err.decode('utf-8', 'replace')
            if proc.returncode != 0 or 'MEM ' not in out:
                return fail('18h %s tick failed: %s' % (mode, err[-500:]))
            fields = dict(token.split('=', 1) for token in out.strip().split()[1:])
            results[mode] = fields
            shutil.copyfile(paths_for(directory)['history'], os.path.join(directory, mode + '.json'))
        if mod['read_json'](os.path.join(directory, 'plain.json')) != mod['read_json'](os.path.join(directory, 'compact.json')):
            return fail('compact history differs from uncompressed history')
        if mod['read_json'](os.path.join(directory, 'compact.json')) != mod['read_json'](os.path.join(directory, 'combined.json')):
            return fail('combined collector changed diagnostic history')
        if mod['read_json'](os.path.join(directory, 'clock-seed.json')) != mod['read_json'](os.path.join(directory, 'clock-seed.json.result')):
            return fail('combined collector changed clock history')
        combined_rss = int(results['combined']['rss_kb'])
        clock_rss = int(results['combined']['clock_rss_kb'])
        sys.stdout.write('Combined week + 18h rss_kb=%d clock_rss_kb=%d\n' % (combined_rss, clock_rss))
        if combined_rss > 90000 or combined_rss > clock_rss + 32768:
            return fail('combined history exceeded peak memory budget')
        fields = results['compact']
        rss = int(fields['rss_kb'])
        elapsed = float(fields['elapsed'])
        size = int(fields['bytes'])
        kept = int(fields['kept'])
        sys.stdout.write('18h tick plain_rss_kb=%s compact_rss_kb=%d elapsed=%.3f bytes=%d kept=%d\n' %
                         (results['plain']['rss_kb'], rss, elapsed, size, kept))
        if size >= 8 * 1024 * 1024:
            return fail('18h history is %d bytes' % size)
        if kept > mod['MAX_SAMPLES_PER_BOARD'] or kept < 18 * 60:
            return fail('18h tick kept %d samples' % kept)
        if elapsed > 20:
            return fail('18h tick took %.3fs' % elapsed)
        if rss > 65000 or rss >= int(results['plain']['rss_kb']) * 0.8:
            return fail('18h tick RSS %d KB exceeds compact memory budget' % rss)
    finally:
        shutil.rmtree(directory)
    return 0


def test_memory():
    # Earlier functional tests may retain allocator arenas. Start a small
    # measurement parent so its children do not inherit that RSS floor.
    return subprocess.call([sys.executable, os.path.abspath(__file__), '--memory-check'])


def test_compact_history_and_native_clock():
    directory = tempfile.mkdtemp(prefix='bkb compact ')
    native = getattr(time, 'monotonic', None)
    original_popen = subprocess.Popen
    try:
        path = os.path.join(directory, 'history.json')
        sample = {'t': 1, 'a': 2, 'diag': {'work_sends': 3, 'future': None},
                  'future': {'quoted': 'a " b', 'unicode': u'\u00e9'}}
        data = {'boards': [{'id': 0, 'samples': [sample] * 257}],
                'host': [{'t': 1, 'name': 'host'}], 'artifacts': []}
        mod['write_json_atomic'](path, data)
        compact = mod['load_history'](path, 1000)
        rows = compact['boards'][0]['samples']
        if not all(isinstance(row, mod['HistorySample']) for row in rows):
            return fail('history was not packed')
        if rows[0].fields is not rows[1].fields:
            return fail('history schemas were not shared')
        if not isinstance(rows[0]['diag'], mod['HistorySample']):
            return fail('diagnostic counters were not packed')
        mod['write_json_atomic'](path, compact)
        restored = mod['read_json'](path)
        if restored['boards'] != data['boards'] or restored['host'] != data['host']:
            return fail('compact round trip changed samples or host metadata')
        with open(path, 'rb') as handle:
            before = handle.read()
        data['boards'][0]['samples'][-1] = {'t': float('nan'), 'a': 2}
        try:
            mod['write_json_atomic'](path, data)
            return fail('nonfinite sample accepted')
        except ValueError:
            pass
        with open(path, 'rb') as handle:
            unchanged = handle.read() == before
        if not unchanged or any(n.endswith('.tmp') for n in os.listdir(directory)):
            return fail('failed later batch changed history or leaked temp file')

        def no_spawn(*args, **kwargs):
            raise AssertionError('monotonic clock spawned a process')
        subprocess.Popen = no_spawn
        if native is not None:
            del time.monotonic
        mod['_MONOTONIC_NATIVE'] = None
        mod['_MONOTONIC_TRIED'] = False
        first = mod['monotonic_seconds']()
        clock = mod['_MONOTONIC_NATIVE']
        second = mod['monotonic_seconds']()
        if first is None or second < first or clock is None or mod['_MONOTONIC_NATIVE'] is not clock:
            return fail('native monotonic clock unavailable or not cached')
        mod['_MONOTONIC_NATIVE'] = lambda: None
        if mod['monotonic_seconds']() is None:
            return fail('proc uptime fallback unavailable')
    finally:
        if native is not None:
            time.monotonic = native
        subprocess.Popen = original_popen
        mod['_MONOTONIC_NATIVE'] = None
        mod['_MONOTONIC_TRIED'] = False
        shutil.rmtree(directory)
    return 0


def main():
    checks = (
        test_endpoints,
        test_bounds_and_registration,
        test_capture_and_window,
        test_pending_and_cosnapshot,
        test_epoch_fallback_and_routes,
        test_process_epoch_units,
        test_hash_cache_and_running_inode,
        test_driver_host_sha,
        test_atomic_corrupt_retention,
        test_compact_history_and_native_clock,
        test_memory,
    )
    for check in checks:
        result = check()
        if result:
            return result
    print('PASS bkb diagnostics recorder')
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--memory-check':
        sys.exit(memory_check())
    if len(sys.argv) == 3 and sys.argv[1] == '--memory-seed':
        write_memory_fixture(sys.argv[2])
        sys.exit(0)
    if os.environ.get('BKB_DIAG_MEM_CHILD') == '1':
        sys.exit(mem_child())
    sys.exit(main())
