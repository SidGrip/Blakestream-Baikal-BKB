#!/usr/bin/env bash
# Bootstrap and configure a patched sgminer tree for a native host build.
# Does not apt-install, download, or copy the binary over the reference artifact.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PIN_FILE="${ROOT}/sgminer-build/UPSTREAM-PIN"
# shellcheck disable=SC1090
source "${PIN_FILE}"

SRC="${BKB_SGMINER_SRC:-${ROOT}/sgminer-build/src}"

usage() {
    cat <<EOF
Usage: sgminer-build/configure-host.sh [--src DIR]

Runs autoreconf -fi and ./configure --enable-baikal in a patched checkout of
${UPSTREAM_COMMIT}. Install these host packages yourself before running it
(names are Debian/Ubuntu; not installed by this script):

  build-essential autoconf automake libtool pkg-config
  libcurl4-openssl-dev libssl-dev libudev-dev libncurses-dev

Jansson and libusb are vendored in the upstream tree. Missing tools or
libraries fail the configure step. Native diagnostic unit tests do not need
this script; see sgminer-build/test-native.sh.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --src)
            SRC=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
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

if [[ ! -f "${SRC}/configure.ac" || ! -f "${SRC}/baikal-diag.h" ]]; then
    echo "need a patched sgminer tree at ${SRC}" >&2
    echo "Run fetch-baseline.sh and apply-patches.sh. This script does not fetch or patch." >&2
    exit 1
fi
actual=$(git -C "${SRC}" rev-parse HEAD)
if [[ "${actual}" != "${UPSTREAM_COMMIT}" ]]; then
    echo "HEAD ${actual} is not ${UPSTREAM_COMMIT}" >&2
    exit 1
fi

missing=()
for tool in autoconf automake libtoolize pkg-config gcc make; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        missing+=("${tool}")
    fi
done
if [[ "${#missing[@]}" -gt 0 ]]; then
    echo "missing host build tools: ${missing[*]}" >&2
    echo "Install them yourself. This script does not run a package manager." >&2
    exit 1
fi

cd "${SRC}"
echo "autoreconf -fi"
autoreconf -fi
echo "configure --enable-baikal"
./configure --enable-baikal
echo "configured ${SRC}"
echo "Next, for a host binary: make -C ${SRC}"
echo "Do not copy it over sgminer-build/sgminer.patched or overlay/opt/scripta/bin/sgminer."
