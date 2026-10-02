#!/usr/bin/env bash
# Restore the pinned sgminer-baikal source from the bundled offline copy.
# Does not apply patches, configure, or touch a miner. Refuses to overwrite an
# existing checkout that is not already this commit.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PIN_FILE="${ROOT}/sgminer-build/UPSTREAM-PIN"
# shellcheck disable=SC1090
source "${PIN_FILE}"

SRC="${BKB_SGMINER_SRC:-${ROOT}/sgminer-build/src}"
URL="${BKB_SGMINER_URL:-}"
BUNDLE="${ROOT}/${UPSTREAM_BUNDLE}"

usage() {
    cat <<EOF
Usage: sgminer-build/fetch-baseline.sh [--src DIR] [--url URL] [--print-pin]

Restores the included, SHA-256-verified source bundle without network access
and checks out ${UPSTREAM_COMMIT} (short name 3cde8de).
There is no automatic download fallback if the bundle is missing or corrupt.

Only --url or BKB_SGMINER_URL selects an external source instead of the bundle.
The upstream URL, for provenance, is ${UPSTREAM_URL}.

Default destination: ${ROOT}/sgminer-build/src
Override with --src or BKB_SGMINER_SRC. Relative paths are from the current
directory. This script does not reset or delete an existing tree.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --src)
            [[ $# -ge 2 && -n "$2" ]] || { echo "--src requires a directory" >&2; exit 2; }
            SRC=$2
            shift 2
            ;;
        --url)
            [[ $# -ge 2 && -n "$2" ]] || { echo "--url requires a URL or path" >&2; exit 2; }
            URL=$2
            shift 2
            ;;
        --print-pin)
            printf '%s\n' "${UPSTREAM_COMMIT}"
            exit 0
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

if [[ -e "${SRC}" || -L "${SRC}" ]]; then
    if [[ ! -d "${SRC}/.git" ]]; then
        echo "refusing to replace ${SRC}: it exists and is not a git checkout" >&2
        exit 1
    fi
    actual=$(git -C "${SRC}" rev-parse HEAD)
    if [[ "${actual}" != "${UPSTREAM_COMMIT}" ]]; then
        echo "refusing to modify ${SRC}: HEAD ${actual} is not ${UPSTREAM_COMMIT}" >&2
        echo "Choose an empty --src directory. This script will not reset an existing checkout." >&2
        exit 1
    fi
    echo "already at ${UPSTREAM_COMMIT}: ${SRC}"
    exit 0
fi

if [[ -z "${URL}" ]]; then
    if [[ ! -f "${BUNDLE}" ]]; then
        echo "missing bundled sgminer source: ${BUNDLE}" >&2
        echo "Restore the bundle from this repository. No network fallback was attempted." >&2
        exit 1
    fi
    actual_sha=$(sha256sum < "${BUNDLE}" | awk '{print $1}')
    if [[ "${actual_sha}" != "${UPSTREAM_BUNDLE_SHA256}" ]]; then
        echo "bundled sgminer source failed SHA-256 verification: ${BUNDLE}" >&2
        exit 1
    fi
fi

mkdir -p "$(dirname "${SRC}")"
echo "checkout ${UPSTREAM_COMMIT}"
if [[ -z "${URL}" ]]; then
    echo "restoring bundled source (offline): ${BUNDLE}"
    GIT_TERMINAL_PROMPT=0 GIT_ALLOW_PROTOCOL=file git clone --no-checkout "${BUNDLE}" "${SRC}"
else
    echo "cloning explicitly selected source: ${URL}"
    GIT_TERMINAL_PROMPT=0 git clone --no-checkout "${URL}" "${SRC}"
fi
GIT_TERMINAL_PROMPT=0 git -C "${SRC}" checkout --detach "${UPSTREAM_COMMIT}"
actual=$(git -C "${SRC}" rev-parse HEAD)
if [[ "${actual}" != "${UPSTREAM_COMMIT}" ]]; then
    echo "checkout failed: HEAD ${actual} != ${UPSTREAM_COMMIT}" >&2
    exit 1
fi
echo "prepared ${actual}"
echo "${SRC}"
