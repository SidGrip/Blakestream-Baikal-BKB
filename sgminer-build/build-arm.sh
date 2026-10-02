#!/usr/bin/env bash
# Explicit ARMv7 sgminer build and diagnostic tests. Not part of default QA.
# Does not apt-install on the host, pull an image unless --pull is given,
# register binfmt, flash a device, or replace sgminer.patched.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PIN_FILE="${ROOT}/sgminer-build/UPSTREAM-PIN"
# shellcheck disable=SC1090
source "${PIN_FILE}"

SRC="${BKB_SGMINER_SRC:-${ROOT}/sgminer-build/src}"
OUT="${BKB_ARM_OUTPUT:-${ROOT}/sgminer-build/build/arm}"
IMAGE="${BKB_ARM_IMAGE:-arm32v7/ubuntu:16.04}"
PULL=0
TESTS_ONLY=0

usage() {
    cat <<EOF
Usage: sgminer-build/build-arm.sh [--src DIR] [--out DIR] [--image NAME] [--pull] [--tests-only]

Builds the patched tree with autoreconf and ./configure --enable-baikal inside
an ARMv7 Ubuntu 16.04 container (glibc 2.23 / OpenSSL 1.0.2 class userspace,
matching the controller). The binary is written under the output directory and
is not copied onto sgminer-build/sgminer.patched or the firmware overlay.

Missing docker, a missing image, or a source tree that is not the patched
${UPSTREAM_COMMIT} checkout fails the command. --pull is the only way this
script downloads a base image. Package installation happens only inside the
container, and only when that image does not already have the compilers.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --src) SRC=$2; shift 2 ;;
        --out) OUT=$2; shift 2 ;;
        --image) IMAGE=$2; shift 2 ;;
        --pull) PULL=1; shift ;;
        --tests-only) TESTS_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "${SRC}" != /* ]]; then
    SRC="$(pwd)/${SRC}"
fi
if [[ "${OUT}" != /* ]]; then
    OUT="$(pwd)/${OUT}"
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "missing required tool: docker" >&2
    echo "ARM builds are optional and are not installed or started by default QA." >&2
    exit 1
fi
if [[ ! -f "${SRC}/baikal-diag.h" ]]; then
    echo "patched source required at ${SRC}" >&2
    exit 1
fi
actual=$(git -C "${SRC}" rev-parse HEAD)
if [[ "${actual}" != "${UPSTREAM_COMMIT}" ]]; then
    echo "HEAD ${actual} is not ${UPSTREAM_COMMIT}" >&2
    exit 1
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    if [[ "${PULL}" -ne 1 ]]; then
        echo "docker image not present: ${IMAGE}" >&2
        echo "Re-run with --pull to download it. Default QA does not pull images." >&2
        exit 1
    fi
    docker pull --platform linux/arm/v7 "${IMAGE}"
fi

if ! docker run --rm --platform linux/arm/v7 "${IMAGE}" /bin/true; then
    echo "cannot execute ARMv7 containers: use an ARM host or configure ARM binfmt/QEMU on the build machine" >&2
    echo "This script does not register host emulators automatically." >&2
    exit 1
fi

mkdir -p "${OUT}"
echo "ARM build image: ${IMAGE}"
echo "output: ${OUT}"
docker run --rm --platform linux/arm/v7 \
    -v "${ROOT}:/work:ro" \
    -v "${SRC}:/upstream-src:ro" \
    -v "${OUT}:/out" \
    -e BKB_ARM_TESTS_ONLY="${TESTS_ONLY}" \
    -e BKB_ARM_INSTALL_DEPS="${BKB_ARM_INSTALL_DEPS:-1}" \
    -e BKB_BUILD_JOBS="${BKB_BUILD_JOBS:-4}" \
    "${IMAGE}" \
    bash /work/sgminer-build/build-arm-inner.sh
echo "ARM build finished: ${OUT}"
