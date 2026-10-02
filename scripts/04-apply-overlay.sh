#!/usr/bin/env bash
# 04-apply-overlay.sh — rsync the overlay/ tree into firmware-modified/rootfs/.
# The overlay mirrors rootfs paths and only contains files we want to add or replace.
# We deliberately do NOT use --delete: the overlay is additive.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="${HERE}/overlay"
ROOTFS="${HERE}/firmware-modified/rootfs"

if [[ ! -d "${ROOTFS}" ]]; then
    echo "[04-apply-overlay] ERROR: ${ROOTFS} missing. Run 03-snapshot.sh first." >&2
    exit 1
fi

if [[ ! -d "${OVERLAY}" ]]; then
    echo "[04-apply-overlay] ERROR: ${OVERLAY} missing." >&2
    exit 1
fi

echo "[04-apply-overlay] Applying ${OVERLAY}/ -> ${ROOTFS}/"
rsync -aHAX --numeric-ids --info=progress2 --human-readable "${OVERLAY}/" "${ROOTFS}/"
chmod 0644 "${ROOTFS}/etc/sysctl.d/90-blakestream-dual-network.conf"
mkdir -p "${ROOTFS}/etc/systemd/system/multi-user.target.wants"
ln -sfn ../blakestream-measured-rates.service "${ROOTFS}/etc/systemd/system/multi-user.target.wants/blakestream-measured-rates.service"
mkdir -p "${ROOTFS}/etc/systemd/system/timers.target.wants"
ln -sfn ../blakestream-network-rollback.timer "${ROOTFS}/etc/systemd/system/timers.target.wants/blakestream-network-rollback.timer"
ln -sfn ../blakestream-wifi-rollback.timer "${ROOTFS}/etc/systemd/system/timers.target.wants/blakestream-wifi-rollback.timer"
mkdir -p "${ROOTFS}/etc/systemd/system/NetworkManager.service.wants"
ln -sfn ../blakestream-wifi-default.service "${ROOTFS}/etc/systemd/system/NetworkManager.service.wants/blakestream-wifi-default.service"

echo "[04-apply-overlay] Cleanup: removing legacy paths from rootfs"
bash "${HERE}/scripts/prune-rootfs.sh" "${ROOTFS}"

echo "[04-apply-overlay] Done. Files in this overlay:"
( cd "${OVERLAY}" && find . -type f -o -type l ) | sort
