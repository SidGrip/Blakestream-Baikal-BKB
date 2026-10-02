#!/usr/bin/env bash
# 02-mount.sh — Mount a disposable factory-image copy read-only for inspection.
# The copy allows ext4 journal recovery without changing the verified input.
# Requires sudo.
#
# Outputs the loop device path and mount points to ../tmp/mount-state.env so the
# inspection step can pick them up.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[02-mount] This script needs root to manipulate loop devices and mount." >&2
    echo "[02-mount] Re-running under sudo ..." >&2
    exec sudo --preserve-env=PATH,BKB_FACTORY_IMG,BKB_MNT_BOOT,BKB_MNT_ROOT "$0" "$@"
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
IMG="${BKB_FACTORY_IMG:-${HERE}/firmware-original/PiZero_GB_180105_V1.0.img}"
STATE_DIR="${HERE}/tmp"
STATE_FILE="${STATE_DIR}/mount-state.env"

MNT_BOOT="${BKB_MNT_BOOT:-/mnt/baikal-boot-ro}"
MNT_ROOT="${BKB_MNT_ROOT:-/mnt/baikal-ro}"

if [[ ! -f "${IMG}" ]]; then
    echo "[02-mount] ERROR: ${IMG} not found. Run 01-fetch.sh first." >&2
    exit 1
fi

mkdir -p "${STATE_DIR}" "${MNT_BOOT}" "${MNT_ROOT}"

if [[ -e "${STATE_FILE}" ]]; then
    echo "[02-mount] ERROR: mount state already exists; run 02-umount.sh for that session first." >&2
    exit 1
fi
if mountpoint -q "${MNT_ROOT}" || mountpoint -q "${MNT_BOOT}"; then
    echo "[02-mount] ERROR: requested mount point already in use." >&2
    exit 1
fi

INSPECTION_IMG=$(mktemp "${STATE_DIR}/inspection-XXXXXX.img")
LOOP=""
root_mounted=0
boot_mounted=0
cleanup_failed_mount() {
    local status=$?
    [[ "$boot_mounted" == 0 ]] || umount "${MNT_BOOT}"
    [[ "$root_mounted" == 0 ]] || umount "${MNT_ROOT}"
    [[ -z "$LOOP" ]] || losetup -d "${LOOP}"
    rm -f -- "$INSPECTION_IMG"
    exit "$status"
}
trap cleanup_failed_mount EXIT
cp --reflink=auto "${IMG}" "${INSPECTION_IMG}"
LOOP=$(losetup -fP --show "${INSPECTION_IMG}")
echo "[02-mount] Attached inspection copy: ${LOOP}"

# Show partition layout
echo "[02-mount] Partition layout:"
lsblk -o NAME,SIZE,FSTYPE,LABEL "${LOOP}"

# Identify partitions: typical Orange Pi/Armbian image has p1=FAT boot, p2=ext4 rootfs.
# blkid probes the actual FS signature, which works on freshly-attached loop devices
# even when lsblk's FSTYPE column hasn't been populated yet.
PART_BOOT=""
PART_ROOT=""

for p in "${LOOP}p1" "${LOOP}p2" "${LOOP}p3"; do
    [[ -b "${p}" ]] || continue
    fstype=$(blkid -o value -s TYPE "${p}" 2>/dev/null || true)
    echo "[02-mount]   ${p}: ${fstype:-(none detected)}"
    case "${fstype}" in
        vfat|fat32|msdos) PART_BOOT="${p}" ;;
        ext2|ext3|ext4) PART_ROOT="${p}" ;;
    esac
done

# Fallback: if blkid found nothing, try mounting the whole image (single-partition)
if [[ -z "${PART_ROOT}" && -z "${PART_BOOT}" ]]; then
    echo "[02-mount] No partitions detected via blkid. Attempting whole-image mount as ext4 ..."
    PART_ROOT="${LOOP}"
fi

if [[ -n "${PART_ROOT}" ]]; then
    echo "[02-mount] Mounting rootfs ${PART_ROOT} -> ${MNT_ROOT} (ro)"
    mount -o ro "${PART_ROOT}" "${MNT_ROOT}"
    root_mounted=1
fi

if [[ -n "${PART_BOOT}" ]]; then
    echo "[02-mount] Mounting boot ${PART_BOOT} -> ${MNT_BOOT} (ro)"
    mount -o ro "${PART_BOOT}" "${MNT_BOOT}"
    boot_mounted=1
fi

printf 'LOOP=%q\nPART_BOOT=%q\nPART_ROOT=%q\nMNT_BOOT=%q\nMNT_ROOT=%q\nINSPECTION_IMG=%q\n' \
    "$LOOP" "$PART_BOOT" "$PART_ROOT" "$MNT_BOOT" "$MNT_ROOT" "$INSPECTION_IMG" > "${STATE_FILE}"
trap - EXIT

echo "[02-mount] State written to ${STATE_FILE}"
echo "[02-mount] Ready. To unmount: sudo ./scripts/02-umount.sh"
