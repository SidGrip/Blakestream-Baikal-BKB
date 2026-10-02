#!/usr/bin/env bash
# Assemble a BK-B clock patch and splice it into a verified stock bridge dump.
# Does not flash or download. Outputs default to an ignored build directory.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CALLER=$(pwd)
PIN_FILE="${SCRIPT_DIR}/STOCK-BRIDGE.txt"

usage() {
    cat <<EOF
Usage: firmware-custom/build.sh [--dump PATH] [--out-dir PATH] [--check-dump] [variant]

The release variant is v1 (the default), with active-slot clock handling.

Pass --dump or set BKB_STOCK_DUMP to a verified stock STM32 image.
The repository includes overlay/opt/scripta/firmware/stock.bin.
If neither is set, the script looks at firmware-dump/bkb-stm32f407-flash.bin
and fails if that file is absent or does not match firmware-custom/STOCK-BRIDGE.txt.
Relative --dump and --out-dir paths are resolved from the current directory.

--check-dump verifies the dump and exits without a compiler.
A missing compiler fails the build. Nothing is installed or flashed.
EOF
}

read_pin() {
    local key=$1
    local value
    value=$(awk -F= -v key="${key}" '$1 == key { print $2; exit }' "${PIN_FILE}")
    if [[ -z "${value}" ]]; then
        echo "stock pin ${PIN_FILE} has no ${key}" >&2
        exit 1
    fi
    printf '%s\n' "${value}"
}

resolve_path() {
    local path=$1
    if [[ "${path}" != /* ]]; then
        path="${CALLER}/${path}"
    fi
    local dir file
    dir=$(dirname "${path}")
    file=$(basename "${path}")
    if [[ -d "${dir}" ]]; then
        dir=$(cd "${dir}" && pwd)
    fi
    printf '%s/%s\n' "${dir}" "${file}"
}

if [[ ! -f "${PIN_FILE}" ]]; then
    echo "missing stock identity file: ${PIN_FILE}" >&2
    exit 1
fi

EXPECT_SIZE=$(read_pin size)
EXPECT_MD5=$(read_pin md5)
EXPECT_SHA=$(read_pin sha256)
VARIANT=v1
DUMP="${BKB_STOCK_DUMP:-}"
OUT_DIR="${BKB_FIRMWARE_OUT:-${SCRIPT_DIR}/../sgminer-build/build/firmware-rebuild}"
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dump)
            DUMP=$2
            shift 2
            ;;
        --out-dir)
            OUT_DIR=$2
            shift 2
            ;;
        --check-dump)
            CHECK_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            VARIANT=$1
            shift
            ;;
    esac
done
if [[ $# -gt 0 ]]; then
    echo "unexpected extra arguments: $*" >&2
    exit 2
fi

CPPFLAGS=()
case "${VARIANT}" in
    v1) SRC=patch_set_option_v1.S ;;
    *)
        echo "unknown variant: ${VARIANT}" >&2
        usage >&2
        exit 2
        ;;
esac

if [[ -z "${DUMP}" ]]; then
    DUMP="${SCRIPT_DIR}/../firmware-dump/bkb-stm32f407-flash.bin"
fi
DUMP=$(resolve_path "${DUMP}")
OUT_DIR=$(resolve_path "${OUT_DIR}")

if [[ ! -f "${DUMP}" ]]; then
    echo "stock bridge dump not found: ${DUMP}" >&2
    echo "The vendor image is gitignored and is not downloaded." >&2
    echo "Pass --dump PATH or set BKB_STOCK_DUMP." >&2
    echo "Expected size ${EXPECT_SIZE}, md5 ${EXPECT_MD5}, sha256 ${EXPECT_SHA}." >&2
    exit 1
fi
for tool in md5sum sha256sum stat; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "missing required tool: ${tool}" >&2
        exit 1
    fi
done
actual_size=$(stat -c '%s' "${DUMP}")
actual_md5=$(md5sum "${DUMP}" | awk '{print $1}')
actual_sha=$(sha256sum "${DUMP}" | awk '{print $1}')
if [[ "${actual_size}" != "${EXPECT_SIZE}" || "${actual_md5}" != "${EXPECT_MD5}" || "${actual_sha}" != "${EXPECT_SHA}" ]]; then
    echo "stock bridge dump failed verification: ${DUMP}" >&2
    echo "  size  ${actual_size} (expected ${EXPECT_SIZE})" >&2
    echo "  md5   ${actual_md5} (expected ${EXPECT_MD5})" >&2
    echo "  sha256 ${actual_sha} (expected ${EXPECT_SHA})" >&2
    exit 1
fi
echo "stock dump verified: ${DUMP}"
if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    exit 0
fi

for tool in arm-none-eabi-gcc arm-none-eabi-ld arm-none-eabi-objcopy arm-none-eabi-objdump python3; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "missing required firmware tool: ${tool}" >&2
        echo "Install the ARM bare-metal toolchain yourself. This script does not." >&2
        exit 1
    fi
done

OUT_NAME="bkb-stm32f407-flash-clockmod-${VARIANT}.bin"
mkdir -p "${OUT_DIR}"
ELF="${OUT_DIR}/patch-${VARIANT}.elf"
STUB="${OUT_DIR}/patch-${VARIANT}.bin"
OBJ="${OUT_DIR}/patch-${VARIANT}.o"
OUT="${OUT_DIR}/${OUT_NAME}"
PATCH_VMA=0x08005000
DISPATCH_OFF=0x3D80
NEW_HANDLER=0x08005001

echo "== variant: ${VARIANT} (${SRC} -> ${OUT}) =="
echo "== assemble =="
arm-none-eabi-gcc "${CPPFLAGS[@]}" -c -mcpu=cortex-m4 -mthumb -Os -ffreestanding -nostdlib \
    "${SCRIPT_DIR}/${SRC}" -o "${OBJ}"
if ! arm-none-eabi-ld -Ttext="${PATCH_VMA}" --section-start .text="${PATCH_VMA}" "${OBJ}" -o "${ELF}" 2>/dev/null; then
    arm-none-eabi-ld -Ttext="${PATCH_VMA}" "${OBJ}" -o "${ELF}"
fi
arm-none-eabi-objcopy -O binary "${ELF}" "${STUB}"
echo "stub size: $(stat -c %s "${STUB}") bytes"

echo "== splice =="
python3 "${SCRIPT_DIR}/splice.py" "${DUMP}" "${STUB}" "${OUT}" "${PATCH_VMA}" "${DISPATCH_OFF}" "${NEW_HANDLER}"

echo "== verify =="
arm-none-eabi-objdump -D -b binary -m arm -M force-thumb \
    --adjust-vma="${PATCH_VMA}" --start-address=0 --stop-address=0x60 "${STUB}" | sed -n '7,60p'
python3 - "${OUT}" <<'PY'
import struct, sys
data = open(sys.argv[1], 'rb').read()
word = struct.unpack_from('<I', data, 0x3D80)[0]
print('patched dispatch word @0x08003D80: 0x%08X' % word)
if len(data) != 1048576 or word != 0x08005001:
    sys.stderr.write('spliced image failed structural check\n')
    sys.exit(1)
PY
echo "== done: ${OUT} =="
if [[ "${OUT_DIR}" == "${SCRIPT_DIR}/build" ]]; then
    echo "NOTE: output is in the tracked reference directory. This command does not commit it."
fi
