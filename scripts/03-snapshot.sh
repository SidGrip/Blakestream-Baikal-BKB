#!/usr/bin/env bash
# 03-snapshot.sh — Copy the mounted rootfs into:
#   - firmware-original/rootfs-snapshot/  (read-only reference; never modified)
#   - firmware-modified/rootfs/            (working copy we patch)
#
# Requires that 02-mount.sh has already attached the image. Uses sudo for the
# rsync because rootfs has files owned by root with mode bits we want to preserve.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo --preserve-env=PATH "$0" "$@"
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="${HERE}/tmp/mount-state.env"

if [[ ! -f "${STATE_FILE}" ]]; then
    echo "[03-snapshot] ERROR: ${STATE_FILE} missing. Run 02-mount.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${STATE_FILE}"

if [[ -z "${MNT_ROOT:-}" ]] || ! mountpoint -q "${MNT_ROOT}"; then
    echo "[03-snapshot] ERROR: rootfs is not mounted at ${MNT_ROOT:-?}" >&2
    exit 1
fi

SNAP_RO="${HERE}/firmware-original/rootfs-snapshot"
SNAP_RW="${HERE}/firmware-modified/rootfs"

mkdir -p "${SNAP_RO}" "${SNAP_RW}"

# Use --numeric-ids and -aHAX to preserve ownership, hardlinks, ACLs, xattrs.
# This is critical: sgminer + Scripta install paths and the Baikal kernel modules
# rely on correct ownership and capability bits.
RSYNC_OPTS=(-aHAX --numeric-ids --info=progress2 --human-readable)

echo "[03-snapshot] Copying rootfs -> ${SNAP_RO} (read-only reference)"
rsync "${RSYNC_OPTS[@]}" "${MNT_ROOT}/" "${SNAP_RO}/"

echo "[03-snapshot] Copying rootfs -> ${SNAP_RW} (working copy)"
rsync "${RSYNC_OPTS[@]}" "${MNT_ROOT}/" "${SNAP_RW}/"

# Make the reference snapshot immutable-by-convention: chmod -w on top-level entries
# (a real chattr +i would require root forever; this is a tripwire, not a lock).
chmod -R a-w "${SNAP_RO}" 2>/dev/null || true

# Hand the working copy back to the invoking user so we can edit it without sudo.
INVOKING_USER="${SUDO_USER:-${USER:-sid}}"
chown -R "${INVOKING_USER}:${INVOKING_USER}" "${SNAP_RW}" 2>/dev/null || true

echo "[03-snapshot] Done. Working copy at ${SNAP_RW}"
echo "[03-snapshot] You can now safely run 02-umount.sh — the original image is no longer needed for editing."
