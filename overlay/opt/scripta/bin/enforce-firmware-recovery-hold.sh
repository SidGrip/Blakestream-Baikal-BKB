#!/bin/sh
# Reassert STM32 ROM DFU at boot whenever a previous write lacks a verified
# application image. The persistent marker is created/fsynced before writes.

set -eu

MARKER=/var/lib/blakestream-firstboot/firmware-recovery-required
PREPARE=/opt/scripta/bin/prepare-runtime-dir.sh
DFU=/opt/scripta/firmware/dfu_flash.py
PY=/usr/bin/python

[ "$#" -eq 0 ] || { echo "firmware recovery hold accepts no arguments" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "firmware recovery hold requires root" >&2; exit 1; }

"$PREPARE"
if [ ! -e "$MARKER" ] && [ ! -L "$MARKER" ]; then
    exit 0
fi
[ -f "$MARKER" ] && [ ! -L "$MARKER" ] || {
    echo "unsafe firmware recovery marker" >&2
    exit 1
}
[ "$(stat -c '%u:%g:%a:%h' "$MARKER" 2>/dev/null || echo unsafe)" = "0:0:600:1" ] || {
    echo "unsafe firmware recovery marker metadata" >&2
    exit 1
}
[ -f "$DFU" ] && [ ! -L "$DFU" ] || {
    echo "firmware recovery flasher unavailable" >&2
    exit 1
}

echo "Firmware recovery marker present; asserting ROM DFU before miner startup."
exec "$PY" -u "$DFU" forcedfu
