#!/usr/bin/env bash
# 01-fetch.sh — Download the Baikal BK-B factory firmware image and verify integrity.
#
# The image is hosted on a public Google Drive link. The user has already enabled
# anyone-with-link access. We use the usercontent.google.com direct-download URL
# (with the confirm token bypass for files >100 MB) so curl works without a browser.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST_FILE="${BKB_FACTORY_IMG:-${HERE}/firmware-original/PiZero_GB_180105_V1.0.img}"
DEST_DIR="$(dirname "${DEST_FILE}")"
EXPECTED_BYTES=3904897024
EXPECTED_SHA256=de3b514d890c9581d1fe22e90574ae534c2d19d3bdee6a33db0a5df7a37447be

# Public Drive download URL provided by the user (file id 1R8Okh-eu7wwl9j3Xj3O89oaL9Jr_s9kI)
URL='https://drive.usercontent.google.com/download?id=1R8Okh-eu7wwl9j3Xj3O89oaL9Jr_s9kI&export=download&authuser=0&confirm=t'

mkdir -p "${DEST_DIR}"

if [[ -f "${DEST_FILE}" ]]; then
    actual_bytes=$(stat -c '%s' "${DEST_FILE}")
    if [[ "${actual_bytes}" -eq "${EXPECTED_BYTES}" ]]; then
        echo "[01-fetch] Image already present; verifying its checksum before use."
    else
        echo "[01-fetch] ERROR: existing file has wrong size (${actual_bytes} != ${EXPECTED_BYTES}); leaving it unchanged." >&2
        exit 1
    fi
fi

if [[ ! -f "${DEST_FILE}" ]]; then
    echo "[01-fetch] Downloading PiZero_GB_180105_V1.0.img (~3.9 GB) ..."
    # -L: follow redirects, -C -: resume if interrupted, --fail: error on HTTP 4xx/5xx
    curl -L -C - --fail --output "${DEST_FILE}.partial" "${URL}"
fi

INPUT="${DEST_FILE}"
[[ -f "${INPUT}" ]] || INPUT="${DEST_FILE}.partial"
actual_bytes=$(stat -c '%s' "${INPUT}")
if [[ "${actual_bytes}" -ne "${EXPECTED_BYTES}" ]]; then
    echo "[01-fetch] ERROR: downloaded size ${actual_bytes} != expected ${EXPECTED_BYTES}" >&2
    exit 1
fi

echo "[01-fetch] Generating SHA-256 ..."
actual_sha=$(sha256sum "${INPUT}" | awk '{print $1}')
if [[ "${actual_sha}" != "${EXPECTED_SHA256}" ]]; then
    echo "[01-fetch] ERROR: checksum ${actual_sha} != ${EXPECTED_SHA256}; not accepting this image." >&2
    exit 1
fi
if [[ "${INPUT}" != "${DEST_FILE}" ]]; then
    mv "${INPUT}" "${DEST_FILE}"
fi
printf '%s  %s\n' "${actual_sha}" "$(basename "${DEST_FILE}")" > "${DEST_FILE}.sha256"
cat "${DEST_FILE}.sha256"

echo "[01-fetch] OK. ${DEST_FILE}"
