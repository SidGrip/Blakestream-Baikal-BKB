#!/usr/bin/env bash
# 05-repack.sh — Build the output Blakestream-BKB-v2.1.img by:
#   1. Copying the original .img to build/
#   2. Mounting the copy's rootfs partition read-write via loop device
#   3. rsync'ing firmware-modified/rootfs/ over the mounted rootfs
#   4. Sync, unmount, detach loop
#   5. SHA-256 the result
#
# Requires sudo.
#
# IMPORTANT: We work on a COPY so the factory image in firmware-original/ stays untouched.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo --preserve-env=PATH "$0" "$@"
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC_IMG="${HERE}/firmware-original/PiZero_GB_180105_V1.0.img"
OUT_DIR="${HERE}/build"
OUT_IMG="${OUT_DIR}/Blakestream-BKB-v2.1.img"
ROOTFS_SRC="${HERE}/firmware-modified/rootfs"
MNT="/mnt/baikal-rw"

if [[ ! -f "${SRC_IMG}" ]]; then
    echo "[05-repack] ERROR: ${SRC_IMG} missing." >&2
    exit 1
fi
if [[ ! -d "${ROOTFS_SRC}" ]]; then
    echo "[05-repack] ERROR: ${ROOTFS_SRC} missing. Run 03-snapshot.sh + 04-apply-overlay.sh first." >&2
    exit 1
fi

mkdir -p "${OUT_DIR}" "${MNT}"

echo "[05-repack] Copying factory image to ${OUT_IMG} (this is large, takes a moment) ..."
cp --reflink=auto "${SRC_IMG}" "${OUT_IMG}"

echo "[05-repack] Attaching loop device with partition scanning ..."
LOOP=$(losetup -fP --show "${OUT_IMG}")
echo "[05-repack] Loop: ${LOOP}"

# Wait for udev to populate FSTYPE on the partition nodes — without this
# the lsblk lookup below races and returns empty before the kernel has
# finished probing. Seen on Ubuntu 24.04 host with the BK-B factory image.
udevadm settle || true
partprobe "${LOOP}" 2>/dev/null || true
sleep 1

# Identify the rootfs partition. Prefer ext4 detection via blkid (more
# reliable than lsblk FSTYPE which races udev). Falls back to lsblk if
# blkid is missing.
PART_ROOT=""
for p in "${LOOP}p1" "${LOOP}p2" "${LOOP}p3" "${LOOP}"; do
    [[ -b "${p}" ]] || continue
    fstype=$(blkid -o value -s TYPE "${p}" 2>/dev/null || lsblk -no FSTYPE "${p}" 2>/dev/null || true)
    if [[ "${fstype}" =~ ^ext[234]$ ]]; then
        PART_ROOT="${p}"
        echo "[05-repack] Detected rootfs partition: ${p} (${fstype})"
        break
    fi
done

if [[ -z "${PART_ROOT}" ]]; then
    losetup -d "${LOOP}" || true
    echo "[05-repack] ERROR: could not find ext4 rootfs partition on ${LOOP}" >&2
    exit 1
fi

echo "[05-repack] Mounting ${PART_ROOT} -> ${MNT} (rw)"
mount "${PART_ROOT}" "${MNT}"

cleanup() {
    echo "[05-repack] Cleanup: syncing and unmounting ..."
    sync
    umount "${MNT}" || true
    losetup -d "${LOOP}" || true
}
trap cleanup EXIT

echo "[05-repack] Fixing ownership: chown -R 0:0 on modified rootfs (dev box uid != Pi uid) ..."
# The overlay files are created as the dev-box user (uid 1000 = sid). On the Pi
# uid 1000 = baikal. rsync --numeric-ids would copy that wrong uid into the image,
# breaking /usr/bin/sudo (needs root + setuid), /etc/sudoers, lighttpd log dir, etc.
# Fix: chown the entire rootfs to root:root first, then fix the known non-root paths.
chown -hR 0:0 "${ROOTFS_SRC}/"
# /home/baikal should be uid 1000 (baikal on the Pi)
if [[ -d "${ROOTFS_SRC}/home/baikal" ]]; then
    chown -hR 1000:1000 "${ROOTFS_SRC}/home/baikal"
fi
# /var/www should be uid 33 (www-data) for lighttpd
if [[ -d "${ROOTFS_SRC}/var/www" ]]; then
    chown -hR 33:33 "${ROOTFS_SRC}/var/www"
fi
# /var/log/lighttpd should be www-data
if [[ -d "${ROOTFS_SRC}/var/log/lighttpd" ]]; then
    chown -hR 33:33 "${ROOTFS_SRC}/var/log/lighttpd"
fi
# /var/cache/lighttpd is where mod_compress writes gzipped cache files for
# static assets (partials/, ng/, js/). Without www-data write here, the
# Miner page partial (and others) fail to load with mod_compress errors.
if [[ -d "${ROOTFS_SRC}/var/cache/lighttpd" ]]; then
    chown -hR 33:33 "${ROOTFS_SRC}/var/cache/lighttpd"
fi
# Scripta config files written by f_blakestream.php / f_settings.php / f_backup.php
# must be writable by www-data. The etc dir itself needs to be group-writable so
# atomic temp+rename saves work.
if [[ -d "${ROOTFS_SRC}/opt/scripta/etc" ]]; then
    chgrp 33 "${ROOTFS_SRC}/opt/scripta/etc"
    chmod 775 "${ROOTFS_SRC}/opt/scripta/etc"
    # Blakestream-managed JSONs + factory files PHP needs to write (settings,
    # web password, sgminer options). Without these the Settings tab silently
    # fails to save — file_put_contents() returns false and the toast says
    # "Configuration saved" but nothing actually persists.
    for f in saved-pools.json board-assignments.json temp-config.json temp-state.json failover-config.json \
             scripta.conf uipasswd miner.options.json miner.pools.json miner.conf; do
        if [[ -f "${ROOTFS_SRC}/opt/scripta/etc/${f}" ]]; then
            chown 33:33 "${ROOTFS_SRC}/opt/scripta/etc/${f}"
        fi
    done
    if [[ -d "${ROOTFS_SRC}/opt/scripta/etc/backup" ]]; then
        chown -R 33:33 "${ROOTFS_SRC}/opt/scripta/etc/backup"
    fi
fi
# /opt/scripta/var (pool history, rrd graphs) is also written by www-data
if [[ -d "${ROOTFS_SRC}/opt/scripta/var" ]]; then
    chown -hR 33:33 "${ROOTFS_SRC}/opt/scripta/var"
fi
# /usr/bin/sudo needs setuid
if [[ -f "${ROOTFS_SRC}/usr/bin/sudo" ]]; then
    chmod 4755 "${ROOTFS_SRC}/usr/bin/sudo"
fi
# /etc/sudoers needs 440
if [[ -f "${ROOTFS_SRC}/etc/sudoers" ]]; then
    chmod 440 "${ROOTFS_SRC}/etc/sudoers"
fi
# /etc/cron.d/* must be 644 — group/other-writable files are silently
# skipped by cron with INSECURE MODE in the daemon log.
if [[ -d "${ROOTFS_SRC}/etc/cron.d" ]]; then
    chmod 644 "${ROOTFS_SRC}"/etc/cron.d/* 2>/dev/null || true
fi

echo "[05-repack] rsync'ing modified rootfs into image ..."
# --delete is enabled: the modified rootfs IS the source of truth at this point.
# Exclude the kernel pseudo-filesystems just in case the modified rootfs has stale /dev /proc /sys
rsync -aHAX --numeric-ids --delete \
    --exclude='/dev/*' --exclude='/proc/*' --exclude='/sys/*' --exclude='/run/*' --exclude='/tmp/*' \
    --info=progress2 --human-readable \
    "${ROOTFS_SRC}/" "${MNT}/"

echo "[05-repack] Sync ..."
sync

# trap will umount + detach
trap - EXIT
cleanup

echo "[05-repack] Generating SHA-256 ..."
sha256sum "${OUT_IMG}" > "${OUT_IMG}.sha256"
cat "${OUT_IMG}.sha256"

INVOKING_USER="${SUDO_USER:-${USER:-sid}}"
chown "${INVOKING_USER}:${INVOKING_USER}" "${OUT_IMG}" "${OUT_IMG}.sha256" 2>/dev/null || true

echo "[05-repack] Done. Output: ${OUT_IMG}"
