#!/bin/bash
# Blakestream-GaintB explicit restart.
#
# Unconditionally kills the running sgminer and re-runs the launcher with the
# current saved-pools.json + board-assignments.json. Used by the Scripta UI
# when the user applies board assignments or clicks the "Reload" button.
#
# DO NOT call from cron — that's miner-start.sh's job (idempotent watchdog).
#
# Uses flock to serialize concurrent restart requests so we don't end up with
# multiple sgminer screens after a race (e.g. PHP save + temp watchdog firing
# at the same moment).

set -u

LOG_TAG=blakestream-miner
SGMINER=/opt/scripta/bin/sgminer
LOCK=/var/run/blakestream-miner-restart.lock
FW_OPERATION_LOCK=/run/blakestream/bkb-firmware.lock
FW_OPERATION_PID=/run/blakestream/bkb-firmware.pid
FW_OPERATION_MAX_AGE=1200
REPAIR=/opt/scripta/bin/repair-system-perms.sh

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') restart: $*" | logger -t "$LOG_TAG"; echo "$*"; }

firmware_operation_active() {
    local pid now mtime age

    [[ -f "$FW_OPERATION_LOCK" ]] || return 1
    pid=$(head -n 1 "$FW_OPERATION_PID" 2>/dev/null | tr -dc '0-9')
    if [[ -n "$pid" && -d "/proc/$pid" ]]; then
        return 0
    fi

    now=$(date +%s)
    mtime=$(stat -c '%Y' "$FW_OPERATION_LOCK" 2>/dev/null || echo 0)
    age=$((now - mtime))
    [[ "$age" -le "$FW_OPERATION_MAX_AGE" ]]
}

repair_system_perms() {
    if [[ -x "$REPAIR" ]]; then
        "$REPAIR" >/dev/null 2>&1 || true
    fi
}

repair_system_perms

if [[ "${1:-}" == "--preflight" ]]; then
    exit 0
fi

if [[ "${BS_FIRMWARE_RESTART:-}" != "1" ]] && firmware_operation_active; then
    log "firmware operation active; explicit restart deferred"
    exit 0
fi

# Re-exec under flock if we don't already hold the lock. Wait up to 30s for
# any concurrent miner-start.sh / miner-restart.sh to finish (shared lock).
if [[ "${BS_LAUNCHER_LOCKED:-}" != "1" ]]; then
    export BS_LAUNCHER_LOCKED=1
    exec flock -w 30 "$LOCK" "$0" "$@"
fi

log "explicit restart requested (locked)"

# Kill running sgminer screens + processes
for s in $(screen -ls 2>/dev/null | awk '/sgminer/ {print $1}'); do
    log "quitting screen $s"
    screen -S "$s" -X quit 2>/dev/null
done
sleep 1
pkill -9 -f "$SGMINER " 2>/dev/null
sleep 1

# Hand off to the regular launcher (which will now find no running sgminer
# and start a fresh one with the current config)
exec /opt/scripta/startup/miner-start.sh
