#!/usr/bin/env bash
# Runs inside the ARMv7 container. Not a default QA step.
set -euo pipefail

SRC_IN=${BKB_ARM_SRC_MOUNT:-/upstream-src}
WORK=${BKB_ARM_WORK:-/out/source-work}
OUT=${BKB_ARM_OUT:-/out}
REPO=${BKB_ARM_REPO:-/work}

if [[ ! -f "${SRC_IN}/configure.ac" || ! -f "${SRC_IN}/baikal-diag.h" ]]; then
    echo "mounted source is not a patched sgminer tree: ${SRC_IN}" >&2
    exit 1
fi

need_packages=0
for tool in gcc make autoconf automake libtoolize pkg-config file; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        need_packages=1
    fi
done
for pc in libcurl openssl libudev; do
    if ! pkg-config --exists "${pc}" 2>/dev/null; then
        need_packages=1
    fi
done
if [[ "${need_packages}" -eq 1 ]]; then
    if [[ "${BKB_ARM_INSTALL_DEPS:-1}" != 1 ]]; then
        echo "ARM image is missing the build tools or libraries." >&2
        echo "Re-run with the default BKB_ARM_INSTALL_DEPS=1 to apt-get inside this container only," >&2
        echo "or use an image that already has them. The host package manager is not invoked." >&2
        exit 1
    fi
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        ca-certificates build-essential autoconf automake libtool pkg-config file \
        libcurl4-openssl-dev libjansson-dev libssl-dev libudev-dev \
        libncurses5-dev
fi

if [[ -e "${WORK}" ]]; then
    echo "build work directory already exists: ${WORK}; choose a fresh output directory" >&2
    exit 1
fi
mkdir -p "${WORK}" "${OUT}"
cp -a "${SRC_IN}/." "${WORK}/"
cd "${WORK}"

if [[ "${BKB_ARM_TESTS_ONLY:-0}" != 1 ]]; then
    echo "autoreconf -fi"
    autoreconf -fi
    echo "configure --enable-baikal"
    ./configure --enable-baikal
    make -j"${BKB_BUILD_JOBS:-4}"
    test -x ./sgminer
    cp -f ./sgminer "${OUT}/sgminer"
    timeout 30 ./sgminer --version
    machine=$(od -An -t u2 -j 18 -N 2 ./sgminer | tr -d ' ')
    if [[ "${machine}" != "40" ]]; then
        echo "built sgminer e_machine=${machine}, expected 40 (EM_ARM)" >&2
        exit 1
    fi
    echo "ARM sgminer written to ${OUT}/sgminer (not promoted over the reference binary)"
fi

export BKB_SGMINER_SRC="${WORK}"
export BKB_QA_OUT="${OUT}/native"
bash "${REPO}/sgminer-build/test-native.sh" helpers
bash "${REPO}/sgminer-build/test-native.sh" driver
echo "ARM compatibility tests passed"
