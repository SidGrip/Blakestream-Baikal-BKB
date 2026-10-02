#!/usr/bin/env bash
# Compile and run host-side BK-B diagnostic tests against a patched tree.
# helpers: baikal-diag unit tests and the concurrency harness (ASan/UBSan).
# tsan: the concurrency harness under ThreadSanitizer (x86_64 needs setarch -R).
# driver: the real driver-baikalu.c USB/GET_INFO harness. It uses vendored
# headers plus a tiny config.h written only under the output directory.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC="${BKB_SGMINER_SRC:-${ROOT}/sgminer-build/src}"
OUT="${BKB_QA_OUT:-${ROOT}/sgminer-build/build/native-tests}"

usage() {
    cat <<EOF
Usage: sgminer-build/test-native.sh [helpers|tsan|driver|all]

Environment:
  BKB_SGMINER_SRC   patched source tree (default: <repo>/sgminer-build/src)
  BKB_QA_OUT        object and binary directory (created; not a device path)

Missing source, gcc, or (for tsan on x86_64) setarch fails the run.
This script does not fetch source, configure the full miner, or install packages.
EOF
}

if [[ "${SRC}" != /* ]]; then
    SRC="$(pwd)/${SRC}"
fi
if [[ "${OUT}" != /* ]]; then
    OUT="$(pwd)/${OUT}"
fi

mode=${1:-all}
case "${mode}" in
    helpers|tsan|driver|all|-h|--help) ;;
    *)
        echo "unknown mode: ${mode}" >&2
        usage >&2
        exit 2
        ;;
esac
if [[ "${mode}" == "-h" || "${mode}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ ! -f "${SRC}/baikal-diag.c" || ! -f "${SRC}/baikal-diag.h" || ! -f "${SRC}/tests/test-baikal-diag.c" ]]; then
    echo "patched diagnostic sources not found in ${SRC}" >&2
    echo "Run fetch-baseline.sh and apply-patches.sh. A missing tree is a failure, not a skip." >&2
    exit 1
fi
if ! command -v gcc >/dev/null 2>&1; then
    echo "missing required compiler: gcc" >&2
    exit 1
fi

mkdir -p "${OUT}"

compile_diag() {
    local src_c=$1
    local name=$2
    local sanitize=$3
    local -a flags=(-std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror -pthread -g -O1 -I"${SRC}" -fno-omit-frame-pointer)
    case "${sanitize}" in
        asan) flags+=(-fsanitize=address,undefined) ;;
        tsan) flags+=(-fsanitize=thread) ;;
        *)
            echo "unknown sanitizer ${sanitize}" >&2
            exit 1
            ;;
    esac
    gcc "${flags[@]}" "${src_c}" "${SRC}/baikal-diag.c" -o "${OUT}/${name}"
}

run_bin() {
    local bin=$1
    if [[ "${2:-}" == "no-aslr" ]]; then
        if [[ "$(uname -m)" != "x86_64" ]]; then
            echo "ThreadSanitizer ASLR workaround is specified for x86_64, not $(uname -m)" >&2
            exit 1
        fi
        if ! command -v setarch >/dev/null 2>&1; then
            echo "missing required tool for ThreadSanitizer: setarch" >&2
            exit 1
        fi
        setarch x86_64 -R "${bin}"
    else
        "${bin}"
    fi
}

run_helpers() {
    echo "== native helpers (ASan/UBSan) =="
    compile_diag "${SRC}/tests/test-baikal-diag.c" test-baikal-diag asan
    compile_diag "${SRC}/tests/test-baikal-diag-driver.c" test-baikal-diag-driver asan
    compile_diag "${ROOT}/scripts/test-bkb-diag-concurrency.c" test-bkb-diag-concurrency asan
    ASAN_OPTIONS=${ASAN_OPTIONS:-detect_leaks=1} \
        UBSAN_OPTIONS=${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1} \
        run_bin "${OUT}/test-baikal-diag"
    ASAN_OPTIONS=${ASAN_OPTIONS:-detect_leaks=1} \
        UBSAN_OPTIONS=${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1} \
        run_bin "${OUT}/test-baikal-diag-driver"
    ASAN_OPTIONS=${ASAN_OPTIONS:-detect_leaks=1} \
        UBSAN_OPTIONS=${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1} \
        run_bin "${OUT}/test-bkb-diag-concurrency"
}

run_tsan() {
    echo "== native concurrency (ThreadSanitizer) =="
    compile_diag "${ROOT}/scripts/test-bkb-diag-concurrency.c" test-bkb-diag-concurrency-tsan tsan
    TSAN_OPTIONS=${TSAN_OPTIONS:-halt_on_error=1} \
        run_bin "${OUT}/test-bkb-diag-concurrency-tsan" no-aslr
}

run_driver() {
    echo "== native driver harness =="
    if [[ ! -f "${SRC}/driver-baikalu.c" || ! -f "${SRC}/sph/sha2.c" ]]; then
        echo "driver sources missing under ${SRC}" >&2
        exit 1
    fi
    if [[ ! -f "${SRC}/submodules/libusb-bk/libusb/libusb.h" || ! -f "${SRC}/submodules/jansson/src/jansson.h" ]]; then
        echo "vendored libusb/jansson headers missing under ${SRC}" >&2
        exit 1
    fi
    mkdir -p "${OUT}/include"
    # Harness-only headers. They are not copied into the sgminer tree and are
    # not a substitute for configure-host.sh when building the miner itself.
    cat > "${OUT}/include/config.h" <<'EOF'
#define HAVE_STDINT_H 1
#define HAVE_STDBOOL_H 1
#define HAVE_UNISTD_H 1
#define HAVE_SYS_STAT_H 1
#define HAVE_STRING_H 1
#define HAVE_STDLIB_H 1
#define HAVE_ALLOCA_H 1
#define HAVE_ALLOCA 1
#define USE_USBUTILS 1
#define USE_BAIKAL 1
#define HAVE_LIBCURL 1
#define STDC_HEADERS 1
#define VERSION "5.6.2-BK_cod3gen_MOD"
#define PACKAGE "sgminer"
#define PACKAGE_VERSION VERSION
EOF
    cat > "${OUT}/include/jansson_config.h" <<'EOF'
#ifndef JANSSON_CONFIG_H
#define JANSSON_CONFIG_H
#define JSON_INLINE inline
#define JSON_INTEGER_IS_LONG_LONG 1
#define JSON_HAVE_LOCALECONV 1
#endif
EOF
    # Do not register unused driver globals with ASan: that defeats --gc-sections
    # and pulls in the whole miner. Stack/heap ASan and UBSan remain enabled.
    gcc -std=gnu11 -Wall -Wno-unused-function -Wno-unused-parameter -Wno-unused-variable \
        -pthread -g -O1 -fno-omit-frame-pointer \
        -ffunction-sections -fdata-sections \
        -fsanitize=address,undefined --param asan-globals=0 \
        -D_GNU_SOURCE \
        -I"${OUT}/include" \
        -I"${SRC}" \
        -I"${SRC}/sph" \
        -I"${SRC}/submodules/jansson/src" \
        -I"${SRC}/submodules/libusb-bk/libusb" \
        "${ROOT}/scripts/test-bkb-driver-io.c" \
        "${SRC}/sph/sha2.c" \
        -Wl,--gc-sections -pthread -lm \
        -o "${OUT}/test-bkb-driver-io"
    ASAN_OPTIONS=${ASAN_OPTIONS:-detect_leaks=1} \
        UBSAN_OPTIONS=${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1} \
        "${OUT}/test-bkb-driver-io"
}

case "${mode}" in
    helpers) run_helpers ;;
    tsan) run_tsan ;;
    driver) run_driver ;;
    all)
        run_helpers
        run_tsan
        run_driver
        ;;
esac
echo "native tests passed (${mode})"
