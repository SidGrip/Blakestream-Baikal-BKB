#!/usr/bin/env bash
# 06-compress.sh — xz the output image for distribution.
# Uses bounded parallelism. The .img will shrink dramatically because
# unused rootfs space (zeros) compresses to almost nothing.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
IMG="${BKB_IMAGE_OUT:-${HERE}/build}/Blakestream-GaintB-v2.2.img"
THREADS="${BKB_XZ_THREADS:-2}"
PRESET="${BKB_XZ_PRESET:-9}"
if [[ ! "$THREADS" =~ ^[1-9][0-9]*$ || ! "$PRESET" =~ ^[0-9]$ ]]; then
    echo "[06-compress] ERROR: BKB_XZ_THREADS must be positive; BKB_XZ_PRESET must be 0-9." >&2
    exit 1
fi

if [[ ! -f "${IMG}" ]]; then
    echo "[06-compress] ERROR: ${IMG} missing. Run 05-repack.sh first." >&2
    exit 1
fi

echo "[06-compress] Compressing ${IMG} (xz -T${THREADS} -${PRESET}, this may take a while) ..."
xz -T"${THREADS}" -"${PRESET}" -k "${IMG}"

XZ="${IMG}.xz"
echo "[06-compress] Done."
ls -lh "${IMG}" "${XZ}"
(cd "$(dirname "${XZ}")" && sha256sum "$(basename "${XZ}")") > "${XZ}.sha256"
cat "${XZ}.sha256"
