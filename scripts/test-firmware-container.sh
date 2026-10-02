#!/usr/bin/env bash
# Explicit, hardware-free integration phase. Run on a build machine.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMAGE=${BKB_FIRMWARE_QA_IMAGE:-blakestream/firmware-qa:local}

[ "$#" -le 1 ] || { echo 'Unexpected arguments' >&2; exit 2; }
case "${1:-}" in
    --build)
        exec docker build --tag "$IMAGE" "$ROOT/scripts/firmware-qa"
        ;;
    --help)
        printf '%s\n' 'Usage: scripts/test-firmware-container.sh [--build|--help]' \
            'Build explicitly once (network required), then run offline.' \
            'BKB_FIRMWARE_QA_IMAGE selects the prepared image.'
        exit 0
        ;;
    '') ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
esac
command -v docker >/dev/null
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo 'Missing firmware QA image; run this script with --build first.' >&2
    exit 1
}
label=$(docker image inspect --format '{{index .Config.Labels "io.blakestream.firmware-qa"}}' "$IMAGE")
[ "$label" = 1 ] || { echo 'Not a firmware QA image' >&2; exit 1; }
NAME="bkb-firmware-qa-$$-$RANDOM"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Real sudo/screen transitions require setuid, so no-new-privileges is not used.
# No host devices, ports, credentials, writable mounts, or Docker socket enter.
timeout --signal=TERM --kill-after=15s 20m docker run --rm --init --name "$NAME" \
    --pull never --network none --cpus 2 --memory 512m --pids-limit 192 \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --cap-add FSETID --cap-add SETUID --cap-add SETGID --cap-add KILL \
    --cap-add AUDIT_WRITE --tmpfs /tmp:rw,nosuid,size=64m \
    --mount "type=bind,src=$ROOT/scripts,dst=/repo/scripts,readonly" \
    --mount "type=bind,src=$ROOT/overlay,dst=/repo/overlay,readonly" "$IMAGE"
