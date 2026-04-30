#!/bin/bash
# Blakestream-GaintB explicit restart.
#
# Unconditionally kills the running sgminer and re-runs the launcher with the
# current saved-pools.json + board-assignments.json. Used by the Scripta UI
# when the user saves pool changes or clicks the "Reload" button.
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

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') restart: $*" | logger -t "$LOG_TAG"; echo "$*"; }

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
