#!/usr/bin/env bash
# Apply the numbered patches onto a clean 3cde8de checkout.
# Rejects fuzz, failed hunks, and patches/rejected/0003. Does not commit.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PIN_FILE="${ROOT}/sgminer-build/UPSTREAM-PIN"
# shellcheck disable=SC1090
source "${PIN_FILE}"

SRC="${BKB_SGMINER_SRC:-${ROOT}/sgminer-build/src}"
PATCH_DIR="${ROOT}/sgminer-build/patches"
SUMS="${ROOT}/sgminer-build/PATCHED-SHA256SUMS"
STAMP_NAME=".bkb-patches-applied"

usage() {
    cat <<EOF
Usage: sgminer-build/apply-patches.sh [--src DIR] [--list]

Applies patches/0001 through patches/0037 (36 files; there is no 0003) with
patch --batch --forward -F0 -p1. The rejected 0003 patch is not applied.
The checkout must be exactly ${UPSTREAM_COMMIT} and clean.
After a clean apply, the script checks sgminer-build/PATCHED-SHA256SUMS.
Re-running on an already stamped tree exits 0. It will not reset a dirty tree.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --src)
            SRC=$2
            shift 2
            ;;
        --list)
            mapfile -t PATCHES < <(find "${PATCH_DIR}" -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9]-*.patch' | sort)
            if [[ "${#PATCHES[@]}" -ne "${PATCH_COUNT}" ]]; then
                echo "expected ${PATCH_COUNT} numbered patches, found ${#PATCHES[@]}" >&2
                exit 1
            fi
            for patch in "${PATCHES[@]}"; do
                base=$(basename "${patch}")
                case "${base}" in
                    0003-*)
                        echo "rejected patch was included: ${base}" >&2
                        exit 1
                        ;;
                esac
                printf '%s\n' "${base}"
            done
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

if [[ ! -d "${SRC}/.git" ]]; then
    echo "patched source checkout not found: ${SRC}" >&2
    echo "Run sgminer-build/fetch-baseline.sh --src <dir> first. QA does not clone implicitly." >&2
    exit 1
fi
if ! command -v patch >/dev/null 2>&1; then
    echo "missing required tool: patch" >&2
    exit 1
fi

actual=$(git -C "${SRC}" rev-parse HEAD)
if [[ "${actual}" != "${UPSTREAM_COMMIT}" ]]; then
    echo "refusing to patch ${SRC}: HEAD ${actual} is not ${UPSTREAM_COMMIT}" >&2
    exit 1
fi

mapfile -t PATCHES < <(find "${PATCH_DIR}" -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9]-*.patch' | sort)
if [[ "${#PATCHES[@]}" -ne "${PATCH_COUNT}" ]]; then
    echo "expected ${PATCH_COUNT} numbered patches, found ${#PATCHES[@]}" >&2
    exit 1
fi

stamp_inputs=$(for input in "${PATCHES[@]}" "${SUMS}"; do sha256sum < "${input}"; done | sha256sum | awk '{print $1}')
STAMP="${SRC}/${STAMP_NAME}"
if [[ -f "${STAMP}" && -f "${SRC}/baikal-diag.h" && -f "${SRC}/tests/test-baikal-diag.c" ]]; then
    # shellcheck disable=SC1090
    source "${STAMP}"
    if [[ "${APPLIED_COMMIT:-}" == "${UPSTREAM_COMMIT}" && "${APPLIED_INPUTS:-}" == "${stamp_inputs}" ]]; then
        (cd "${SRC}" && sha256sum -c "${SUMS}")
        echo "patches already applied at ${UPSTREAM_COMMIT}: ${SRC}"
        exit 0
    fi
fi

if [[ -n "$(git -C "${SRC}" status --porcelain)" ]]; then
    echo "refusing to patch a dirty checkout: ${SRC}" >&2
    echo "Fetch a clean ${UPSTREAM_COMMIT} directory, or keep the existing stamp if this tree was already patched by this script." >&2
    exit 1
fi

echo "applying ${#PATCHES[@]} patches with fuzz 0 onto ${UPSTREAM_COMMIT}"
for patch in "${PATCHES[@]}"; do
    base=$(basename "${patch}")
    case "${base}" in
        0003-*)
            echo "refusing to apply rejected patch ${base}" >&2
            exit 1
            ;;
    esac
    echo "== ${base} =="
    set +e
    output=$(patch --batch --forward --no-backup-if-mismatch -F0 -p1 -d "${SRC}" -i "${patch}" 2>&1)
    status=$?
    set -e
    printf '%s\n' "${output}"
    if [[ "${status}" -ne 0 ]]; then
        echo "patch failed: ${base}" >&2
        exit 1
    fi
    if grep -E 'fuzz|FAILED|hunk ignored' <<<"${output}" >/dev/null; then
        echo "patch ${base} was not clean (fuzz or failed hunk)" >&2
        exit 1
    fi
    if find "${SRC}" -name '*.rej' -o -name '*.orig' | grep -q .; then
        echo "patch ${base} left reject or orig files" >&2
        find "${SRC}" -name '*.rej' -o -name '*.orig'
        exit 1
    fi
done

if ! (cd "${SRC}" && sha256sum -c "${SUMS}"); then
    echo "patched tree does not match PATCHED-SHA256SUMS" >&2
    exit 1
fi

cat > "${STAMP}" <<EOF
APPLIED_COMMIT='${UPSTREAM_COMMIT}'
APPLIED_INPUTS='${stamp_inputs}'
EOF
echo "applied ${#PATCHES[@]} patches; checksums match"
