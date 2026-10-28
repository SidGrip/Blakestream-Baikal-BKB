#!/usr/bin/env python3
"""Reconcile clock settings once for a fresh SD image; never write firmware."""
import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time

ETC = '/opt/scripta/etc'
RUN = '/run/blakestream-firmware'
FW = '/opt/scripta/firmware'
STATE = '/var/lib/blakestream-firstboot'
PENDING = STATE + '/firmware-firstboot.pending'
RECOVERY_REQUIRED = STATE + '/firmware-recovery-required'
RESULT = STATE + '/result.json'
LOG = '/var/log/blakestream-firstboot/firmware-firstboot.log'
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


def write_json(path, data, mode=0o664):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.firstboot-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
            os.fchown(handle.fileno(), 0, 33)
        os.replace(temporary, path)
        sync_directory(directory)
    finally:
        remove(temporary)


def ensure_pending(reason):
    """Persist the boot retry trigger without trusting an existing pathname."""
    fd, temporary = tempfile.mkstemp(prefix='.firstboot-pending-', dir=STATE)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(reason + '\n')
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
            os.fchown(handle.fileno(), 0, 0)
        os.replace(temporary, PENDING)
        sync_directory(STATE)
    finally:
        remove(temporary)


def clear_capability():
    for name in ('BRIDGE_GLOBAL_V1_READY', 'BRIDGE_GLOBAL_V11_READY'):
        remove(FW + '/' + name)
    sync_directory(FW)


def clear_identity():
    remove(ETC + '/bkb-firmware-identity.json')
    sync_directory(ETC)


def validate_secure_dir(path):
    if os.path.islink(path) or not os.path.isdir(path):
        raise RuntimeError('Unsafe privileged state directory: ' + path)
    if os.geteuid() == 0:
        info = os.stat(path)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise RuntimeError('Privileged state directory is writable by non-root: ' + path)


def valid_pending():
    try:
        info = os.lstat(PENDING)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return False
    return os.geteuid() != 0 or (info.st_uid == 0 and stat.S_IMODE(info.st_mode) & 0o022 == 0)


def recovery_hold_present():
    try:
        info = os.lstat(RECOVERY_REQUIRED)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError('Unsafe firmware recovery marker')
    if info.st_nlink != 1:
        raise RuntimeError('Unsafe firmware recovery marker link count')
    if os.geteuid() == 0 and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600):
        raise RuntimeError('Unsafe firmware recovery marker metadata')
    return True


def open_firstboot_log():
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(LOG, flags, 0o640)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (os.geteuid() == 0 and info.st_uid != 0):
        os.close(fd)
        raise RuntimeError('Unsafe first-boot log')
    os.fchmod(fd, 0o640)
    os.fchown(fd, 0, 33)
    return os.fdopen(fd, 'a')


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
    if recovery_hold_present():
        raise RecoveryError('Bridge recovery hold prevents identity read')
    for name in ('result.env', 'result-applied', 'abort', 'meta.env', 'phase'):
        remove(RUN + '/bkb-firmware-' + name)
    progress = RUN + '/bkb-firmware-progress.log'
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


def default_clock_options(options):
    if not isinstance(options, list) or any(not isinstance(item, dict) or
                                           'key' not in item for item in options):
        raise ValueError('Invalid miner options')
    clean = [item for item in options if item['key'] not in CLOCK_KEYS + ('baikal-options',)]
    clean.append({'key': 'baikal-options', 'value': '400:40:55'})
    return clean


def reconcile(verifier=verify_chip):
    validate_secure_dir(RUN)
    validate_secure_dir(os.path.dirname(LOG))
    validate_secure_dir(os.path.dirname(RESULT))
    # Never undo the boot service's DFU hold. A user flash interrupted on the
    # first boot can coexist with the factory pending marker; only a later
    # read-back-verified recovery flash may clear this persistent gate.
    if recovery_hold_present():
        ensure_pending('bridge recovery required before first-boot identity read')
        print('Bridge recovery required; first-boot identity read deferred')
        return 1
    # A previous boot may have died with the bridge outside application mode.
    # Recover that state before considering a new identity read.
    if os.path.exists(ACTIVE):
        return recover()
    if not valid_pending():
        return 0
    if os.path.exists(RESULT):
        remove(PENDING)
        sync_directory(STATE)
        return 0
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
                       'error': reason + '; automatic first-boot check skipped'}, 0o640)
    remove(PENDING)
    sync_directory(STATE)
    print(reason + '; automatic first-boot check skipped')
    return 1


def reconcile_locked(verifier):
    settings_path = ETC + '/scripta.conf'
    options_path = ETC + '/miner.options.json'
    outcome = {'at': int(time.time()), 'ok': False, 'identity': 'UNKNOWN',
               'adjustable_clocks': False}
    settings = None
    write_json(ACTIVE, {'pid': os.getpid()}, 0o600)
    with open_firstboot_log() as log:
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
    write_json(RESULT, outcome, 0o640)
    # The first-boot result has already been applied. Do not let a later web
    # poll reapply the verification result over the user's saved choices.
    applied = RUN + '/bkb-firmware-result-applied'
    fd = os.open(applied, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                 getattr(os, 'O_NOFOLLOW', 0), 0o660)
    with os.fdopen(fd, 'w') as handle:
        handle.write('first-boot\n')
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), 0o660)
        os.fchown(handle.fileno(), 0, 33)
    if outcome.get('recovery_required'):
        ensure_pending('bridge recovery required')
    else:
        remove(PENDING)
        sync_directory(STATE)
        remove(ACTIVE)
    print(json.dumps(outcome, sort_keys=True))
    return 0 if outcome['ok'] else 1


def recover():
    if recovery_hold_present():
        ensure_pending('bridge recovery required before first-boot cleanup')
        print('Bridge recovery required; first-boot GPIO restore suppressed')
        return 1
    if not os.path.exists(ACTIVE):
        return 0
    validate_secure_dir(RUN)
    validate_secure_dir(os.path.dirname(LOG))
    validate_secure_dir(os.path.dirname(RESULT))
    with open_firstboot_log() as log:
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
                'error': 'First-boot firmware check interrupted'}, 0o640)
        result = read_json(RESULT)
        result['recovery_required'] = not restored
        write_json(RESULT, result, 0o640)
    finally:
        for name in ('bkb-firmware.lock', 'bkb-firmware.pid', 'bkb-firmware-abort',
                     'bkb-firmware-result.env'):
            remove(RUN + '/' + name)
        if restored:
            remove(PENDING)
            remove(ACTIVE)
            sync_directory(STATE)
        else:
            ensure_pending('bridge recovery retry required')
    return 0 if restored else 1


def interrupted(signum, frame):
    raise RuntimeError('First-boot firmware check interrupted')


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('First-boot firmware check requires root')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    sys.exit(recover() if sys.argv[1:] == ['--recover'] else reconcile())
