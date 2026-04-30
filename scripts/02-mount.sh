#!/usr/bin/env bash
# 02-mount.sh — Attach the factory image to a loop device and mount its partitions
# read-only for inspection. Requires sudo.
#
# Outputs the loop device path and mount points to ../tmp/mount-state.env so the
# inspection step can pick them up.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[02-mount] This script needs root to manipulate loop devices and mount." >&2
    echo "[02-mount] Re-running under sudo ..." >&2
    exec sudo --preserve-env=PATH "$0" "$@"
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
IMG="${HERE}/firmware-original/PiZero_GB_180105_V1.0.img"
STATE_DIR="${HERE}/tmp"
STATE_FILE="${STATE_DIR}/mount-state.env"

MNT_BOOT="/mnt/baikal-boot-ro"
MNT_ROOT="/mnt/baikal-ro"

if [[ ! -f "${IMG}" ]]; then
    echo "[02-mount] ERROR: ${IMG} not found. Run 01-fetch.sh first." >&2
    exit 1
fi

mkdir -p "${STATE_DIR}" "${MNT_BOOT}" "${MNT_ROOT}"

# Detach any prior loop device for this image
prior=$(losetup -j "${IMG}" | awk -F: '{print $1}')
if [[ -n "${prior}" ]]; then
    echo "[02-mount] Detaching prior loop device(s): ${prior}"
    for dev in ${prior}; do
        # Best-effort unmount of any partition mountpoints first
        for mp in $(lsblk -no MOUNTPOINT "${dev}" | grep -v '^$' || true); do
            umount "${mp}" || true
        done
        losetup -d "${dev}" || true
    done
fi

LOOP=$(losetup -fP --show "${IMG}")
echo "[02-mount] Attached: ${LOOP}"

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
fi

if [[ -n "${PART_BOOT}" ]]; then
    echo "[02-mount] Mounting boot ${PART_BOOT} -> ${MNT_BOOT} (ro)"
    mount -o ro "${PART_BOOT}" "${MNT_BOOT}"
fi

cat > "${STATE_FILE}" <<EOF
LOOP=${LOOP}
PART_BOOT=${PART_BOOT}
PART_ROOT=${PART_ROOT}
MNT_BOOT=${MNT_BOOT}
MNT_ROOT=${MNT_ROOT}
EOF

echo "[02-mount] State written to ${STATE_FILE}"
echo "[02-mount] Ready. To unmount: sudo ./scripts/02-umount.sh"
