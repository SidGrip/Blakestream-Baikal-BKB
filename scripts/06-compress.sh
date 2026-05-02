#!/usr/bin/env bash
# 06-compress.sh — xz the output image for distribution.
# Uses all CPU cores and max compression. The .img will shrink dramatically because
# unused rootfs space (zeros) compresses to almost nothing.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
IMG="${HERE}/build/Blakestream-BKB-v2.1.img"

if [[ ! -f "${IMG}" ]]; then
    echo "[06-compress] ERROR: ${IMG} missing. Run 05-repack.sh first." >&2
    exit 1
fi

echo "[06-compress] Compressing ${IMG} (xz -T0 -9, this may take a while) ..."
xz -T0 -9 -k -f "${IMG}"

XZ="${IMG}.xz"
echo "[06-compress] Done."
ls -lh "${IMG}" "${XZ}"
sha256sum "${XZ}" > "${XZ}.sha256"
cat "${XZ}.sha256"
