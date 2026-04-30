#!/usr/bin/env bash
# 02-umount.sh — Tear down the loop device and mount points created by 02-mount.sh.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo --preserve-env=PATH "$0" "$@"
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="${HERE}/tmp/mount-state.env"

if [[ ! -f "${STATE_FILE}" ]]; then
    echo "[02-umount] No state file at ${STATE_FILE}. Nothing to do."
    exit 0
fi

# shellcheck disable=SC1090
source "${STATE_FILE}"

for mp in "${MNT_BOOT:-}" "${MNT_ROOT:-}"; do
    [[ -n "${mp}" ]] || continue
    if mountpoint -q "${mp}"; then
        echo "[02-umount] Unmounting ${mp}"
        umount "${mp}"
    fi
done

if [[ -n "${LOOP:-}" ]] && losetup "${LOOP}" >/dev/null 2>&1; then
    echo "[02-umount] Detaching ${LOOP}"
    losetup -d "${LOOP}"
fi

rm -f "${STATE_FILE}"
echo "[02-umount] Done."
