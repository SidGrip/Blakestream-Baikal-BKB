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
import os, sys, time, fcntl, hashlib, struct, subprocess
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
        out = Popen("lsusb | grep -i %s" % match, shell=True, stdout=PIPE).stdout.read().strip().split()
        if len(out) < 4:
            return False
        node = "/dev/bus/usb/%s/%s" % (out[1], out[3][:-1])
        fd = os.open(node, os.O_WRONLY); fcntl.ioctl(fd, USBDEVFS_RESET, 0); os.close(fd)
        return True
    except Exception, e:
        print "reset_usb(%s) err: %s" % (match, e); return False

def lsusb_has(pid):
    return pid in Popen("lsusb | grep -i 0483", shell=True, stdout=PIPE).stdout.read()

def enter_dfu():
    print "--- enter DFU ---"
    cfg_output()
    w('PA18', 'data', 0)                                  # BOOT0 -> bootloader (inverted)
    w('PA10', 'data', 0); time.sleep(0.1); w('PA10', 'data', 1)   # reset pulse
    time.sleep(1.0)
    reset_usb("STM32F407")
    for i in range(12):
        time.sleep(1)
        if lsusb_has("df11"):
            print "  DFU present after %ds" % (i + 1); return True
    print "  ERROR: device did not enter DFU"; return False

def exit_dfu():
    print "--- exit DFU (boot app) ---"
    w('PA18', 'data', 1)                                  # app boot
    w('PA10', 'data', 0); time.sleep(0.1); w('PA10', 'data', 1)
    time.sleep(1.0)
    reset_usb("df11")
    time.sleep(3)
    cfg_restore()
    ok = lsusb_has("5740")
    print "  app mode:", ok
    return ok

def stop_miner():
    print "--- stop miner ---"
    subprocess.call("/opt/scripta/startup/miner-stop.sh 2>/dev/null", shell=True)
    subprocess.call("screen -S sgminer -X quit 2>/dev/null", shell=True)
    subprocess.call("pkill -f /opt/scripta/bin/sgminer", shell=True)
    time.sleep(3)

def start_miner():
    if os.environ.get('BKB_DEFER_MINER_RESTART') == '1':
        print "--- miner restart deferred to guarded firmware wrapper ---"
        return True
    print "--- restart miner ---"
    subprocess.call("BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh >/tmp/bkb-miner-restart.log 2>&1", shell=True)
    time.sleep(10)
    alive = subprocess.call("pgrep -f /opt/scripta/bin/sgminer >/dev/null", shell=True) == 0
    print "  sgminer running:", alive
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
    print "--- DFU upload (read flash) -> %s ---" % out
    if os.path.exists(out):
        os.remove(out)
    rc = subprocess.call("dfu-util -a 0 -d %s -U %s -s %s:%s" % (DFU, out, FLASH_ADDR, FLASH_LEN), shell=True)
    ok = (rc == 0 and os.path.exists(out) and os.path.getsize(out) == 0x100000)
    if ok:
        print "  uploaded %d bytes md5=%s" % (os.path.getsize(out), md5(out))
    return ok

def dfu_download(img):
    sz = os.path.getsize(img) if os.path.exists(img) else -1
    if sz != FLASH_SIZE:
        print "REFUSING download: %s size %d != %d (truncated/corrupt)" % (img, sz, FLASH_SIZE)
        return False
    print "--- DFU download (WRITE flash) <- %s md5=%s ---" % (img, md5(img))
    rc = subprocess.call("dfu-util -a 0 -d %s -D %s -s %s" % (DFU, img, FLASH_ADDR), shell=True)
    return rc == 0

def dfu_upload_verified(out):
    """Read the chip twice and require byte-identical, sane results before the
    backup is trusted. A flaky/partial read therefore fails SAFE (caller aborts
    rather than writing with a bad rollback source)."""
    ensure_dir_for(out)
    if not dfu_upload(out):
        return (False, 'first read failed')
    out2 = out + '.2'
    if not dfu_upload(out2):
        return (False, 'second read failed')
    same = (md5(out) == md5(out2))
    try:
        os.remove(out2)
    except OSError:
        pass
    if not same:
        return (False, 'two reads disagree (unstable read)')
    return validate_image(out, label='backup')

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
    for i in range(3):
        dfu_download(backup)
        got = readback_md5('/tmp/bkb-rollback-rb.bin', tries=2)
        if got == want:
            print "ROLLED BACK + VERIFIED: chip restored to md5=%s" % got
            return fail_rc
        print "  rollback verify attempt %d: got %s want %s" % (i + 1, got, want)
    print "ROLLBACK UNVERIFIED — chip left in DFU; re-run rollback (or flash stock)."
    return 9

def do_backup(out):
    ensure_dir_for(out)
    stop_miner()
    try:
        if not enter_dfu():
            return 2
        ok, why = dfu_upload_verified(out)
        print "BACKUP %s -> %s (%s)" % ("OK" if ok else "FAILED", out, why)
        return 0 if ok else 3
    finally:
        exit_dfu(); start_miner()

def do_verify(img, expect_md5=None):
    ok, why = validate_image(img, expect_md5=expect_md5, label='compare image')
    if not ok:
        print "ABORT: %s" % why
        return 8
    stop_miner()
    try:
        if not enter_dfu():
            return 2
        got = readback_md5('/tmp/bkb-readback.bin', tries=3)
        match = (got is not None and got == md5(img))
        print "VERIFY current-vs-%s : %s (current=%s want=%s)" % (
            img, "MATCH" if match else "DIFFER", got if got else "?", md5(img))
        return 0 if match else 4
    finally:
        exit_dfu(); start_miner()

def do_flash(img, confirm, expect_md5=None):
    # validate the image we intend to write BEFORE touching the chip
    ok, why = validate_image(img, expect_md5=expect_md5, label='target image')
    if not ok:
        print "ABORT: %s (not entering DFU)" % why
        return 8
    want = md5(img)
    stop_miner()
    try:
        if not enter_dfu():
            return 2
        # reversibility layer 1: a VALIDATED (read-twice) backup of the chip now
        okb, whyb = dfu_upload_verified(PREFLASH_BACKUP)
        if not okb:
            print "ABORT: pre-flash backup invalid (%s), not writing" % whyb
            return 3
        print "  pre-flash backup OK: %s md5=%s" % (PREFLASH_BACKUP, md5(PREFLASH_BACKUP))
        if not confirm:
            print "DRY RUN (no --confirm): validated + backed up, NOT writing."
            return 0
        if not dfu_download(img):
            print "WRITE FAILED. Rolling back to pre-flash backup..."
            return rollback_to(PREFLASH_BACKUP, 5)
        got = readback_md5('/tmp/bkb-readback.bin', tries=3)
        if got is None:
            print "READBACK FAILED after retries. Rolling back to pre-flash backup..."
            return rollback_to(PREFLASH_BACKUP, 6)
        if got != want:
            print "VERIFY MISMATCH (got %s want %s). Rolling back..." % (got, want)
            return rollback_to(PREFLASH_BACKUP, 7)
        print "FLASH OK + VERIFIED: chip now md5=%s == %s" % (got, img)
        return 0
    finally:
        exit_dfu(); start_miner()

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
