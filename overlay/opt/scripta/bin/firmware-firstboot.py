#!/usr/bin/env python3
"""Reconcile clock settings once for a fresh SD image; never write firmware."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

ETC = '/opt/scripta/etc'
RUN = '/run/blakestream'
FW = '/opt/scripta/firmware'
PENDING = ETC + '/bkb-firmware-firstboot.pending'
RESULT = ETC + '/bkb-firmware-firstboot.json'
LOG = '/opt/scripta/var/firmware-firstboot.log'
LAUNCH_LOCK = '/var/run/blakestream-miner-restart.lock'
ACTIVE = RUN + '/bkb-firstboot-active'
WRAPPER = '/opt/scripta/bin/bkb-firmware.sh'
STOCK_MD5 = 'd5f844be2952fd713717a71a67e2be61'
CUSTOM_MD5 = '18babe0c4c67d9146e230f9dc423f2f2'
CUSTOM_IMAGES = {CUSTOM_MD5: 'CUSTOM V1',
                 '0e8467eddabf589ad94549a8d572c759': 'CUSTOM V1'}
CLOCK_KEYS = ('baikal-clock-mode', 'baikal-board-clocks',
              'baikal-experimental-clock-index', 'baikal-board-clock-index',
              'blakestream-clock-scope', 'baikal-bridge-global')


class RecoveryError(RuntimeError):
    pass


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def write_json(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.firstboot-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o664)
            os.fchown(handle.fileno(), 0, 33)
        os.replace(temporary, path)
        sync_directory(directory)
    finally:
        remove(temporary)


def clear_capability():
    for name in ('BRIDGE_GLOBAL_V1_READY', 'BRIDGE_GLOBAL_V11_READY'):
        remove(FW + '/' + name)
    sync_directory(FW)


def clear_identity():
    for directory in (ETC, RUN):
        remove(directory + '/bkb-firmware-identity.json')
        sync_directory(directory)


def mark_custom_capability(digest):
    path = FW + '/BRIDGE_GLOBAL_V1_READY'
    fd, temporary = tempfile.mkstemp(prefix='.firstboot-', dir=FW)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write('bridge_firmware=' + CUSTOM_IMAGES[digest] + '\nchip_md5=' + digest + '\n')
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary, path)
        sync_directory(FW)
    finally:
        remove(temporary)


def run_command(command, timeout, output):
    env = os.environ.copy()
    env['BKB_DEFER_MINER_RESTART'] = '1'
    child = subprocess.Popen(command, env=env, stdout=output,
                             stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return child.wait(timeout=timeout)
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)


def bridge_running():
    try:
        output = subprocess.check_output(['lsusb', '-d', '0483:5740'],
            stderr=subprocess.STDOUT, universal_newlines=True, timeout=3)
        return '0483:5740' in output
    except (OSError, subprocess.SubprocessError):
        return False


def restore_bridge(log):
    try:
        rc = run_command(['/usr/bin/python', FW + '/dfu_flash.py', 'restore-gpio'], 12, log)
        return rc == 0 and bridge_running()
    except (OSError, subprocess.SubprocessError):
        return False


def verify_chip(log):
    # Reuse the guarded double-read path. The launcher lock and boot ordering
    # keep mining stopped until identity and settings have both been saved.
    for name in ('result.env', 'result-applied', 'abort', 'meta.env', 'phase'):
        remove(RUN + '/bkb-firmware-' + name)
    progress = RUN + '/bkb-firmware-progress.log'
    with open(progress, 'w'):
        pass
    os.chmod(progress, 0o664)
    os.chown(progress, 0, 33)
    try:
        rc = run_command([WRAPPER, 'run-job', 'verify'], 110, log)
        with open(RUN + '/bkb-firmware-result.env') as handle:
            result = dict(line.strip().split('=', 1) for line in handle if '=' in line)
        if rc != 0 or result.get('ok') != '1' or result.get('op') != 'verify':
            raise RuntimeError('Bridge firmware read did not verify')
        if not bridge_running():
            raise RuntimeError('Bridge did not return to normal mode')
        digest = result.get('chip_md5', '')
        if len(digest) != 32 or any(c not in '0123456789abcdef' for c in digest):
            raise RuntimeError('Invalid firmware identity result')
        return digest
    except Exception:
        # A timed-out read may have been killed while BOOT0 was asserted.
        # This only restores GPIO/application mode, never flash contents.
        if not restore_bridge(log):
            raise RecoveryError('Bridge recovery could not confirm normal mode')
        raise
    finally:
        if os.path.exists(progress):
            with open(progress) as handle:
                log.write(handle.read())
        for name in ('bkb-firmware.lock', 'bkb-firmware.pid', 'bkb-firmware-abort'):
            remove(RUN + '/' + name)
        for path in ('/tmp/bkb-chip-readback.bin', '/tmp/bkb-chip-readback.bin.2'):
            remove(path)


def default_clock_options(options):
    if not isinstance(options, list) or any(not isinstance(item, dict) or
                                           'key' not in item for item in options):
        raise ValueError('Invalid miner options')
    clean = [item for item in options if item['key'] not in CLOCK_KEYS + ('baikal-options',)]
    clean.append({'key': 'baikal-options', 'value': '400:40:55'})
    return clean


def reconcile(verifier=verify_chip):
    if not os.path.exists(PENDING):
        return 0
    if os.path.exists(RESULT):
        remove(PENDING)
        return 0
    os.makedirs(RUN, exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LAUNCH_LOCK, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return skip_busy('Miner launcher busy')
        if os.path.exists(RUN + '/bkb-firmware.lock'):
            return skip_busy('Firmware operation active')
        return reconcile_locked(verifier)


def skip_busy(reason):
    # Do not retry on another boot, when the user may have saved new choices.
    # Leave the active operation's configuration and firmware state alone.
    write_json(RESULT, {'at': int(time.time()), 'ok': False, 'identity': 'UNKNOWN',
                       'error': reason + '; automatic first-boot check skipped'})
    remove(PENDING)
    sync_directory(ETC)
    print(reason + '; automatic first-boot check skipped')
    return 1


def reconcile_locked(verifier):
    settings_path = ETC + '/scripta.conf'
    options_path = ETC + '/miner.options.json'
    outcome = {'at': int(time.time()), 'ok': False, 'identity': 'UNKNOWN',
               'adjustable_clocks': False}
    settings = None
    write_json(ACTIVE, {'pid': os.getpid()})
    with open(LOG, 'a') as log:
        os.chmod(LOG, 0o664)
        os.chown(LOG, 0, 33)
        log.write('First-boot bridge check: read only\n')
        try:
            settings = read_json(settings_path)
            if not isinstance(settings, dict):
                raise ValueError('Invalid settings')
            settings['userAdjustableClocks'] = False
            write_json(settings_path, settings)
            clear_capability()
            clear_identity()
            write_json(options_path, default_clock_options(read_json(options_path)))
            digest = verifier(log)
            identity = CUSTOM_IMAGES.get(digest, 'STOCK' if digest == STOCK_MD5 else 'UNKNOWN')
            record = {'identity': identity, 'chip_md5': digest, 'at': int(time.time())}
            outcome.update(identity=identity, chip_md5=digest)
            if identity == 'UNKNOWN':
                raise RuntimeError('Unrecognized bridge firmware; adjustable clocks remain disabled')
            if digest in CUSTOM_IMAGES and not os.path.isfile(FW + '/ENCODER_READY'):
                raise RuntimeError('Clock encoder is not ready; adjustable clocks remain disabled')
            write_json(ETC + '/bkb-firmware-identity.json', record)
            write_json(RUN + '/bkb-firmware-identity.json', record)
            if digest in CUSTOM_IMAGES:
                mark_custom_capability(digest)
            settings['userAdjustableClocks'] = digest in CUSTOM_IMAGES
            write_json(settings_path, settings)
            outcome.update(ok=True, adjustable_clocks=settings['userAdjustableClocks'])
        except Exception as exc:
            outcome['error'] = str(exc)
            outcome['recovery_required'] = isinstance(exc, RecoveryError)
            clear_capability()
            clear_identity()
            if isinstance(settings, dict):
                settings['userAdjustableClocks'] = False
                write_json(settings_path, settings)
        log.write(json.dumps(outcome, sort_keys=True) + '\n')
        log.flush()
        os.fsync(log.fileno())
    write_json(RESULT, outcome)
    # The first-boot result has already been applied. Do not let a later web
    # poll reapply the verification result over the user's saved choices.
    with open(RUN + '/bkb-firmware-result-applied', 'w') as handle:
        handle.write('first-boot\n')
    os.chmod(RUN + '/bkb-firmware-result-applied', 0o664)
    os.chown(RUN + '/bkb-firmware-result-applied', 0, 33)
    remove(PENDING)
    sync_directory(ETC)
    if not outcome.get('recovery_required'):
        remove(ACTIVE)
    print(json.dumps(outcome, sort_keys=True))
    return 0 if outcome['ok'] else 1


def recover():
    if not os.path.exists(ACTIVE):
        return 0
    with open(LOG, 'a') as log:
        restored = restore_bridge(log)
    try:
        if not os.path.exists(RESULT):
            settings = read_json(ETC + '/scripta.conf')
            settings['userAdjustableClocks'] = False
            write_json(ETC + '/scripta.conf', settings)
            clear_capability()
            clear_identity()
            write_json(RESULT, {'at': int(time.time()), 'ok': False,
                'identity': 'UNKNOWN', 'adjustable_clocks': False,
                'error': 'First-boot firmware check interrupted'})
        result = read_json(RESULT)
        result['recovery_required'] = not restored
        write_json(RESULT, result)
    finally:
        for name in ('bkb-firmware.lock', 'bkb-firmware.pid', 'bkb-firmware-abort',
                     'bkb-firmware-result.env'):
            remove(RUN + '/' + name)
        remove(PENDING)
        if restored:
            remove(ACTIVE)
    return 0 if restored else 1


def interrupted(signum, frame):
    raise RuntimeError('First-boot firmware check interrupted')


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('First-boot firmware check requires root')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    sys.exit(recover() if sys.argv[1:] == ['--recover'] else reconcile())
