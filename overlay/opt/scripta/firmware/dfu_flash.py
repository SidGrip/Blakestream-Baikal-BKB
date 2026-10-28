#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Runs ON the BK-B controller (Orange Pi, python2.7). Reversible DFU flash for the
# STM32F407 bridge. Enters DFU via the factory GPIO mechanism (PA18=BOOT0 inverted,
# PA10=NRST), takes a VALIDATED pre-flash backup (reversibility layer 1), writes,
# then reads back and md5-verifies. The stock image kept on the PC is layer 2.
#
# All writes target ONLY the application flash at 0x08000000 (1 MB). Nothing here
# ever touches System memory, option bytes, RDP, or nBOOT0 — the ROM DFU
# bootloader stays intact, so a bad/interrupted write is always re-flashable.
#
# Modes:
#   backup  <out.bin>                       - dump+validate current flash (read-twice)
#   flash   <img.bin> [--md5 HEX] --confirm - validate img -> backup -> write -> verify
#   verify  <img.bin> [--md5 HEX]           - readback current flash, md5-compare (read-only)
#   forcedfu                                - enter DFU and stay (bench-test re-entry; no write)
#   restore-gpio                            - drive app-boot + restore GPIO cfg (manual recovery)
#
# WRITES require --confirm. Without it, 'flash' downgrades to validate + backup + dry-run.
import os, sys, time, fcntl, hashlib, struct, subprocess, tempfile, stat
from subprocess import Popen, PIPE

USBDEVFS_RESET = 21780
GS = '/sys/class/gpio_sw/%s/%s'
DFU = '0483:df11'
APP = '0483:5740'
FLASH_ADDR = '0x08000000'
FLASH_LEN  = '0x100000'           # full 1 MB
FLASH_SIZE = 0x100000
BACKUP_DIR = '/opt/scripta/backups'              # persistent (SD/eMMC): survives reboot
PREFLASH_BACKUP = BACKUP_DIR + '/bkb-preflash-backup.bin'
RUNTIME_DIR = '/run/blakestream-firmware'
RECOVERY_REQUIRED_ENV = 'BKB_RECOVERY_REQUIRED'
RECOVERY_DIR = '/var/lib/blakestream-firstboot'
RECOVERY_REQUIRED = RECOVERY_DIR + '/firmware-recovery-required'

def w(pin, node, val):
    try:
        f = open(GS % (pin, node), 'w'); f.write(str(val)); f.close()
    except Exception, e:
        print "WARN write %s/%s=%s: %s" % (pin, node, val, e)

def cfg_output():
    for p in ('PA18', 'PA10'):
        w(p, 'cfg', 1)
    w('PA18', 'data', 1); w('PA10', 'data', 1)
    time.sleep(0.3)

def cfg_restore():
    w('PA18', 'cfg', 0); w('PA10', 'cfg', 0)

def reset_usb(match):
    try:
        rows = Popen(['lsusb'], stdout=PIPE, stderr=PIPE).communicate()[0].splitlines()
        found = [row.split() for row in rows if match.lower() in row.lower()]
        if not found or len(found[0]) < 4:
            return False
        out = found[0]
        node = "/dev/bus/usb/%s/%s" % (out[1], out[3][:-1])
        fd = os.open(node, os.O_WRONLY); fcntl.ioctl(fd, USBDEVFS_RESET, 0); os.close(fd)
        return True
    except Exception, e:
        print "reset_usb(%s) err: %s" % (match, e); return False

def lsusb_has(pid):
    output = Popen(['lsusb'], stdout=PIPE, stderr=PIPE).communicate()[0]
    return pid.lower() in output.lower() and '0483' in output.lower()

def quiet_call(argv, env=None):
    try:
        with open(os.devnull, 'wb') as null:
            return subprocess.call(argv, env=env, stdout=null, stderr=null)
    except OSError:
        return 127

def enter_dfu():
    print "Putting the bridge chip into update (DFU) mode..."
    cfg_output()
    w('PA18', 'data', 0)                                  # BOOT0 -> bootloader (inverted)
    w('PA10', 'data', 0); time.sleep(0.1); w('PA10', 'data', 1)   # reset pulse
    time.sleep(1.0)
    reset_usb("STM32F407")
    for i in range(12):
        time.sleep(1)
        if lsusb_has("df11"):
            print "  Update mode ready (%ds)." % (i + 1); return True
    print "  ERROR: the chip did not enter update mode."; return False

def exit_dfu():
    print "Returning the chip to normal running mode..."
    w('PA18', 'data', 1)                                  # app boot
    w('PA10', 'data', 0); time.sleep(0.1); w('PA10', 'data', 1)
    time.sleep(1.0)
    reset_usb("df11")
    time.sleep(3)
    cfg_restore()
    ok = lsusb_has("5740")
    print "  Normal mode:", ok
    return ok

def hold_dfu():
    """Keep the ROM bootloader selected without issuing another reset.

    Once a target write has started, returning to application boot is safe only
    after either that target or the rollback image has read-back-verified.  Do
    not briefly deassert BOOT0 here: a reset or power glitch must continue to
    select the immutable system-memory bootloader.
    """
    print "SAFETY HOLD: no verified application image; keeping the bridge in DFU."
    w('PA18', 'cfg', 1); w('PA10', 'cfg', 1)
    w('PA18', 'data', 0); w('PA10', 'data', 1)
    time.sleep(0.3)
    ok = lsusb_has("df11")
    print "  DFU recovery mode:", ok
    return ok

def stop_miner():
    print "Pausing the miner..."
    quiet_call(['/opt/scripta/startup/miner-stop.sh'])
    quiet_call(['screen', '-S', 'sgminer', '-X', 'quit'])
    quiet_call(['pkill', '-f', '/opt/scripta/bin/sgminer'])
    time.sleep(3)

def start_miner():
    if os.environ.get('BKB_DEFER_MINER_RESTART') == '1':
        print "Miner restart deferred to the guarded firmware wrapper."
        return True
    print "Restarting the miner..."
    env = os.environ.copy()
    env['BS_FIRMWARE_RESTART'] = '1'
    quiet_call(['/opt/scripta/startup/miner-restart.sh'], env=env)
    time.sleep(10)
    alive = quiet_call(['pgrep', '-f', '/opt/scripta/bin/sgminer']) == 0
    print "  Miner running:", alive
    return alive

def md5(path):
    return hashlib.md5(open(path, 'rb').read()).hexdigest()

def ensure_dir_for(path):
    d = os.path.dirname(path)
    try:
        if d and not os.path.isdir(d):
            os.makedirs(d)
    except OSError, e:
        print "WARN mkdir %s: %s" % (d, e)

def secure_output_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if not parent or os.path.islink(parent) or not os.path.isdir(parent):
        return False
    info = os.stat(parent)
    return info.st_uid == 0 and stat.S_IMODE(info.st_mode) & 0o022 == 0

def scratch_path(label):
    if not secure_output_parent(os.path.join(RUNTIME_DIR, 'probe')):
        raise RuntimeError('unsafe firmware runtime directory')
    fd, path = tempfile.mkstemp(prefix='.' + label + '.', dir=RUNTIME_DIR)
    os.close(fd)
    return path

def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def ensure_durable_root_dir(path):
    """Create/validate a persistent root-only directory and its parent entry.

    The release image intentionally contains no firmware backup payloads, so
    BACKUP_DIR can be absent on the first flash.  Fsyncing a backup file and
    BACKUP_DIR is not sufficient in that case: the newly created BACKUP_DIR
    name must also be committed in /opt/scripta before a target write starts.
    """
    parent = os.path.dirname(os.path.abspath(path))
    try:
        parent_info = os.lstat(parent)
        if (stat.S_ISLNK(parent_info.st_mode) or
                not stat.S_ISDIR(parent_info.st_mode) or
                parent_info.st_uid != 0 or
                stat.S_IMODE(parent_info.st_mode) & 0o022):
            return False
        if not os.path.lexists(path):
            os.mkdir(path, 0o700)
            os.chown(path, 0, 0)
            os.chmod(path, 0o700)
        info = os.lstat(path)
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or
                info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
            return False
        # Always repeat both syncs.  This also recovers safely after a prior
        # attempt observed an fsync error but the in-memory directory remains.
        _fsync_dir(path)
        _fsync_dir(parent)
        return True
    except (IOError, OSError):
        return False

def recovery_required_present():
    if not os.path.lexists(RECOVERY_REQUIRED):
        return False
    parent = os.stat(RECOVERY_DIR)
    info = os.lstat(RECOVERY_REQUIRED)
    if (os.path.islink(RECOVERY_DIR) or not stat.S_ISDIR(parent.st_mode) or
            parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022 or
            not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1):
        raise RuntimeError('unsafe firmware recovery marker')
    return True

def mark_recovery_required(reason):
    """Durably publish the miner-start gate before the first DFU write."""
    if not os.path.isdir(RECOVERY_DIR) or os.path.islink(RECOVERY_DIR):
        return False
    parent = os.stat(RECOVERY_DIR)
    if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022:
        return False
    fd = -1
    tmp = None
    renamed = False
    try:
        fd, tmp = tempfile.mkstemp(prefix='.firmware-recovery-required.', dir=RECOVERY_DIR)
        payload = ('recovery_required=1\nreason=%s\nrecorded_at=%d\n' %
                   (reason, int(time.time()))).encode('ascii')
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError('short recovery-marker write')
            offset += written
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        os.rename(tmp, RECOVERY_REQUIRED)
        renamed = True
        _fsync_dir(RECOVERY_DIR)
        return recovery_required_present()
    except (IOError, OSError, RuntimeError):
        return False
    finally:
        if fd >= 0:
            os.close(fd)
        if not renamed and tmp is not None:
            try: os.unlink(tmp)
            except OSError: pass

def clear_recovery_required():
    """Remove the gate only when its directory update is durable."""
    try:
        if os.path.lexists(RECOVERY_REQUIRED):
            if not recovery_required_present():
                return False
            os.unlink(RECOVERY_REQUIRED)
        _fsync_dir(RECOVERY_DIR)
        return True
    except (IOError, OSError, RuntimeError):
        # Keep the live system fail-closed even if the directory fsync failed.
        mark_recovery_required('clear-fsync-failed')
        return False

def validate_image(path, expect_md5=None, label='image'):
    """Reject an obviously-bad 1 MB image: wrong size, all-0x00 / all-0xFF
    (erased/glitched), a bad Cortex-M vector table, or an md5 mismatch. Used both
    for images we are about to WRITE and for backups we READ from the chip, so a
    worthless backup can never silently become the rollback source."""
    if not os.path.exists(path):
        return (False, '%s missing' % label)
    sz = os.path.getsize(path)
    if sz != FLASH_SIZE:
        return (False, '%s size %d != %d' % (label, sz, FLASH_SIZE))
    data = open(path, 'rb').read()
    if data == ('\x00' * FLASH_SIZE):
        return (False, '%s is all 0x00' % label)
    if data == ('\xff' * FLASH_SIZE):
        return (False, '%s is all 0xFF (erased)' % label)
    sp, rv = struct.unpack('<II', data[:8])
    if not (0x20000000 <= sp <= 0x20020000):
        return (False, '%s bad initial SP 0x%08X' % (label, sp))
    if (rv & 1) != 1 or not (0x08000000 <= (rv & ~1) <= 0x080FFFFF):
        return (False, '%s bad reset vector 0x%08X' % (label, rv))
    if expect_md5 and md5(path) != expect_md5:
        return (False, '%s md5 %s != %s' % (label, md5(path), expect_md5))
    return (True, 'ok')

def restore_gpio():
    """Manual recovery: force app-boot strap + restore pin cfg, leaving the bridge
    running the application again (mirror of exit_dfu without the USB reset)."""
    print "--- restore GPIO (app boot) ---"
    try:
        cfg_output()
        w('PA18', 'data', 1)
        w('PA10', 'data', 0); time.sleep(0.1); w('PA10', 'data', 1)
        time.sleep(1.0)
    finally:
        cfg_restore()

def dfu_upload(out):
    print("Reading the chip (1 MB)...")
    ensure_dir_for(out)
    if not secure_output_parent(out) or os.path.islink(out):
        print("  Read REFUSED: unsafe output path.")
        return False
    staging = None
    try:
        # dfu-util 0.8 requires a nonexistent upload destination. Keep that
        # pathname inside a private directory until a complete read is ready.
        staging = tempfile.mkdtemp(prefix='.dfu-upload-', dir=os.path.dirname(os.path.abspath(out)))
        uploaded = os.path.join(staging, 'flash.bin')
        with open(os.devnull, 'wb') as null:
            rc = subprocess.call(['dfu-util', '-a', '0', '-d', DFU, '-U', uploaded,
                                  '-s', FLASH_ADDR + ':' + FLASH_LEN],
                                 stdout=null, stderr=subprocess.STDOUT)
        if rc != 0:
            print("  Read FAILED (dfu-util rc=%d)." % rc)
            return False
        fd = os.open(uploaded, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != FLASH_SIZE:
                print("  Read FAILED: incomplete or unsafe upload file.")
                return False
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        os.rename(uploaded, out)
        print("  Read OK.")
        return True
    except (IOError, OSError) as error:
        print("  Read FAILED: %s" % error)
        return False
    finally:
        if staging is not None:
            try: os.unlink(os.path.join(staging, 'flash.bin'))
            except OSError: pass
            try: os.rmdir(staging)
            except OSError: pass

def dfu_download(img):
    sz = os.path.getsize(img) if os.path.exists(img) else -1
    if sz != FLASH_SIZE:
        print "REFUSING to write: %s size %d != %d (truncated/corrupt)" % (img, sz, FLASH_SIZE)
        return False
    print "Writing the firmware to the chip (1 MB)..."
    with open(os.devnull, 'wb') as null:
        rc = subprocess.call(['dfu-util', '-a', '0', '-d', DFU, '-D', img,
                              '-s', FLASH_ADDR], stdout=null,
                             stderr=subprocess.STDOUT)
    return rc == 0

def dfu_upload_verified(out):
    """Read the chip twice and require byte-identical, sane results before the
    backup is trusted. A flaky/partial read therefore fails SAFE (caller aborts
    rather than writing with a bad rollback source). The previous published
    backup remains untouched until the replacement has been double-read,
    validated, file-fsynced, atomically renamed, and its directory fsynced."""
    parent = os.path.dirname(os.path.abspath(out))
    if parent == BACKUP_DIR:
        if not ensure_durable_root_dir(parent):
            return (False, 'could not durably prepare persistent backup directory')
    else:
        ensure_dir_for(out)
    if not secure_output_parent(out) or os.path.islink(out):
        return (False, 'unsafe backup destination')
    print "Taking a verified safety backup (reading the chip twice)..."
    fd1, first = tempfile.mkstemp(prefix='.' + os.path.basename(out) + '.read1.', dir=parent)
    fd2, second = tempfile.mkstemp(prefix='.' + os.path.basename(out) + '.read2.', dir=parent)
    os.close(fd1); os.close(fd2)
    published = False
    try:
        if not dfu_upload(first):
            return (False, 'first read failed')
        if not dfu_upload(second):
            return (False, 'second read failed')
        if md5(first) != md5(second):
            return (False, 'two reads disagree (unstable read)')
        ok, why = validate_image(first, label='backup')
        if not ok:
            return (False, why)
        fd = os.open(first, os.O_RDWR | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return (False, 'unsafe verified backup inode')
            os.fchown(fd, 0, 0)
            os.fchmod(fd, 0o600)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(first, out)
        published = True
        _fsync_dir(parent)
        return (True, 'ok')
    except (IOError, OSError):
        return (False, 'could not durably publish verified backup')
    finally:
        for path in (None if published else first, second):
            if path is None:
                continue
            try: os.remove(path)
            except OSError: pass

def readback_md5(rb, tries=3):
    for i in range(tries):
        if dfu_upload(rb):
            return md5(rb)
        time.sleep(1)
    return None

def rollback_to(backup, fail_rc):
    """Write `backup`, then read it back and verify. Returns fail_rc (the original
    operation still failed, but the chip is restored) only when the rollback is
    md5-verified; returns 9 if the rollback itself cannot be verified, which means
    the chip is still in DFU and the operator must rollback again."""
    if not os.path.exists(backup):
        print "ROLLBACK IMPOSSIBLE: backup %s missing (use layer-2 stock image)" % backup
        return 9
    want = md5(backup)
    rb = scratch_path('rollback-readback')
    try:
        for i in range(3):
            dfu_download(backup)
            got = readback_md5(rb, tries=2)
            if got == want:
                print "ROLLED BACK + VERIFIED: chip restored to md5=%s" % got
                return fail_rc
            print "  rollback verify attempt %d: got %s want %s" % (i + 1, got, want)
    finally:
        try: os.remove(rb)
        except OSError: pass
    print "ROLLBACK UNVERIFIED — chip left in DFU; re-run rollback (or flash stock)."
    return 9

def do_backup(out):
    ensure_dir_for(out)
    stop_miner()
    try:
        if not enter_dfu():
            return 2
        ok, why = dfu_upload_verified(out)
        print "Chip read %s (%s)." % ("complete" if ok else "FAILED", why)
        return 0 if ok else 3
    finally:
        exit_dfu(); start_miner()

def do_verify(img, expect_md5=None):
    ok, why = validate_image(img, expect_md5=expect_md5, label='compare image')
    if not ok:
        print "ABORT: %s" % why
        return 8
    rb = scratch_path('verify-readback')
    stop_miner()
    try:
        if not enter_dfu():
            return 2
        got = readback_md5(rb, tries=3)
        match = (got is not None and got == md5(img))
        print "VERIFY current-vs-%s : %s (current=%s want=%s)" % (
            img, "MATCH" if match else "DIFFER", got if got else "?", md5(img))
        return 0 if match else 4
    finally:
        try: os.remove(rb)
        except OSError: pass
        exit_dfu(); start_miner()

def do_flash(img, confirm, expect_md5=None):
    # validate the image we intend to write BEFORE touching the chip
    ok, why = validate_image(img, expect_md5=expect_md5, label='target image')
    if not ok:
        print "ABORT: %s (not entering DFU)" % why
        return 8
    want = md5(img)
    rb = scratch_path('flash-readback')
    recovery_env = os.environ.get(RECOVERY_REQUIRED_ENV)
    if recovery_env in ('0', '1'):
        # The guarded wrapper publishes the crash marker before exec, so its
        # explicit value records whether the hold predated this operation.
        recovery_mode = (recovery_env == '1')
    else:
        recovery_mode = recovery_required_present()
    # In an ordinary operation the current application remains safe until the
    # first target write begins.  A persisted recovery hold means the previous
    # operation did not establish that invariant, so only a newly verified
    # target or rollback may make it safe again.
    safe_to_boot = not recovery_mode
    rollback_available = False
    write_started = False
    stop_miner()
    try:
        if not enter_dfu():
            return 2
        if recovery_mode:
            # Do not overwrite the last known-good rollback image with the
            # unverified contents that caused the recovery hold.  A validated
            # persistent backup remains useful, but the pinned/validated target
            # is sufficient to attempt explicit recovery when it is absent.
            okb, whyb = validate_image(PREFLASH_BACKUP, label='persisted safety backup')
            rollback_available = okb
            if okb:
                print "  Recovery mode: retained safety backup validated."
            else:
                print "  Recovery mode: no usable prior backup (%s); target write must verify." % whyb
        else:
            # Reversibility layer 1: a VALIDATED (read-twice) backup of the chip now.
            okb, whyb = dfu_upload_verified(PREFLASH_BACKUP)
            if not okb:
                print "ABORT: the safety backup did not validate (%s) — nothing written." % whyb
                return 3
            rollback_available = True
            print "  Safety backup taken and verified."
        if not confirm:
            print "Dry run: validated + safety-backed-up, nothing written."
            return 0
        # From this point forward, only a read-back-verified target or rollback
        # may permit application boot. Publish and fsync the persistent gate
        # before invoking dfu-util so a crash or power loss also fails closed.
        safe_to_boot = False
        if not mark_recovery_required('target-write-started'):
            print "Could not durably publish the firmware recovery hold; refusing to write."
            return 9
        write_started = True
        if not dfu_download(img):
            if rollback_available:
                print "Write failed — rolling back to the safety backup..."
                rc = rollback_to(PREFLASH_BACKUP, 5)
                safe_to_boot = (rc != 9)
                return rc
            print "Write failed and no verified rollback image is available."
            return 9
        print "Verifying the written firmware (reading it back)..."
        got = readback_md5(rb, tries=3)
        if got is None:
            if rollback_available:
                print "Could not read the chip back — rolling back to the safety backup..."
                rc = rollback_to(PREFLASH_BACKUP, 6)
                safe_to_boot = (rc != 9)
                return rc
            print "Could not verify the target and no verified rollback image is available."
            return 9
        if got != want:
            if rollback_available:
                print "Verification mismatch (got %s, expected %s) — rolling back..." % (got, want)
                rc = rollback_to(PREFLASH_BACKUP, 7)
                safe_to_boot = (rc != 9)
                return rc
            print "Verification mismatch (got %s, expected %s); no verified rollback image is available." % (got, want)
            return 9
        safe_to_boot = True
        print "FLASH OK + VERIFIED: flash contents match the target (md5 %s)." % got
        return 0
    finally:
        try: os.remove(rb)
        except OSError: pass
        if write_started and safe_to_boot and not clear_recovery_required():
            print "Could not durably clear the firmware recovery hold."
            safe_to_boot = False
        if safe_to_boot:
            exit_dfu(); start_miner()
        else:
            hold_dfu()

USAGE = ("usage: backup <out.bin> | flash <img.bin> [--md5 HEX] --confirm | "
         "verify <img.bin> [--md5 HEX] | forcedfu | restore-gpio")

def main():
    a = sys.argv[1:]
    if not a:
        print USAGE
        return 1
    mode = a[0]
    confirm = '--confirm' in a
    expect_md5 = None
    if '--md5' in a:
        i = a.index('--md5')
        if i + 1 < len(a):
            expect_md5 = a[i + 1].lower()
    if mode == 'backup' and len(a) >= 2:
        return do_backup(a[1])
    if mode == 'verify' and len(a) >= 2:
        return do_verify(a[1], expect_md5)
    if mode == 'flash' and len(a) >= 2:
        return do_flash(a[1], confirm, expect_md5)
    if mode == 'forcedfu':
        stop_miner()
        ok = enter_dfu()
        print "FORCE DFU: %s (chip left in DFU; run 'restore-gpio' or 'flash ... --confirm' next)" % ok
        return 0 if ok else 2
    if mode == 'restore-gpio':
        restore_gpio()
        start_miner()
        return 0
    print USAGE
    return 1

if __name__ == '__main__':
    sys.exit(main())
