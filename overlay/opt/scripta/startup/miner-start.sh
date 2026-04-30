#!/bin/bash
# Blakestream-GaintB miner launcher (idempotent watchdog).
#
# Called by /etc/cron.d/scripta every ~30 seconds. Behaviour:
#   - If sgminer is already running   -> NO-OP (do not disturb)
#   - If sgminer is NOT running       -> generate runtime config + launch
#   - If no boards are assigned       -> stay idle (kill any leftover screens)
#
# This is critical: the factory cron entry runs miner-start.sh every 30 seconds
# as a watchdog. The factory script only restarted sgminer if it had crashed.
# Earlier versions of this script unconditionally killed + restarted sgminer
# every time, causing constant interruption and "Miner seems down" alerts in
# the dashboard.
#
# For EXPLICIT restart (e.g. when the user changes pool assignments via the
# Scripta UI), use the sibling script miner-restart.sh instead.
#
# Uses flock SHARED with miner-restart.sh so we can never have two
# launchers racing each other (which would create duplicate sgminer screens
# fighting for the USB device).

set -u

LOG_TAG=blakestream-miner
SGMINER=/opt/scripta/bin/sgminer
RUNTIME_CONF=/tmp/scripta-runtime-miner.conf
BUILDER=/opt/scripta/bin/build-miner-runtime.py
APPLIER=/opt/scripta/bin/apply-board-assignments.py
LOCK=/var/run/blakestream-miner-restart.lock

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | logger -t "$LOG_TAG"; echo "$*"; }

stop_sgminer() {
    for s in $(screen -ls 2>/dev/null | awk '/sgminer/ {print $1}'); do
        screen -S "$s" -X quit 2>/dev/null
    done
    sleep 1
    pkill -9 -f "$SGMINER " 2>/dev/null
}

# Re-exec under flock if we don't already hold it. -n means non-blocking:
# if another start/restart is already running, we exit immediately rather
# than queue up (cron will fire us again in 30s anyway).
if [[ "${BS_LAUNCHER_LOCKED:-}" != "1" ]]; then
    export BS_LAUNCHER_LOCKED=1
    exec flock -n "$LOCK" "$0" "$@" || exit 0
fi

# 1. Idempotent check — is sgminer already running?
if pgrep -f "$SGMINER " >/dev/null 2>&1; then
    # sgminer is running. Nothing to do.
    exit 0
fi

# 2. Reap any leftover empty screens
for s in $(screen -ls 2>/dev/null | awk '/sgminer/ {print $1}'); do
    screen -S "$s" -X quit 2>/dev/null
done
sleep 1

# 3. Generate the runtime config from saved-pools + assignments
if [[ -x "$BUILDER" ]]; then
    SUMMARY=$(/usr/bin/python "$BUILDER" 2>/tmp/build-miner-runtime.err)
    BUILD_RC=$?
else
    log "ERROR: $BUILDER missing; refusing to start sgminer without a runtime config"
    exit 1
fi

# 4. Decide what to launch
case $BUILD_RC in
    0)
        log "starting sgminer with runtime config: $SUMMARY"
        screen -dmS sgminer "$SGMINER" -c "$RUNTIME_CONF" --api-listen
        ;;
    2)
        log "no boards assigned to any pool — sgminer not started (idle)"
        exit 0
        ;;
    *)
        ERR=$(cat /tmp/build-miner-runtime.err 2>/dev/null | head -5)
        log "ERROR: build-miner-runtime.py failed (rc=$BUILD_RC): $ERR"
        exit 1
        ;;
esac

# 5. Wait for sgminer's API to come up (it takes ~6-10 sec) before reporting
#    success. We poll the api-port from the runtime config rather than relying
#    on the screen socket which can be present before sgminer is ready.
API_PORT=$(grep -o '"api-port"[^,}]*' "$RUNTIME_CONF" 2>/dev/null | head -1 | grep -o '[0-9]\+' || echo 4028)
api_ready=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    sleep 1
    if (echo > /dev/tcp/127.0.0.1/$API_PORT) 2>/dev/null; then
        log "sgminer started OK (api ready on port $API_PORT after ${i}s)"
        api_ready=1
        break
    fi
done

# Apply per-board enable/disable based on board-assignments.json + temp-state.json
# This is how 'idle' actually translates to a board not hashing - sgminer's
# --device flag doesn't pin, but the runtime ascdisable API command does.
if [[ $api_ready -eq 0 ]]; then
    log "ERROR: sgminer API not responding on port $API_PORT after 15s; stopping sgminer to fail closed"
    stop_sgminer
    exit 1
fi

if [[ ! -x "$APPLIER" ]]; then
    log "ERROR: $APPLIER missing or not executable; stopping sgminer to fail closed"
    stop_sgminer
    exit 1
fi

/usr/bin/python "$APPLIER" "$API_PORT" 2>&1 | logger -t "$LOG_TAG"
APPLY_RC=${PIPESTATUS[0]}
if [[ $APPLY_RC -ne 0 ]]; then
    log "ERROR: apply-board-assignments.py failed (rc=$APPLY_RC); stopping sgminer to fail closed"
    stop_sgminer
    exit 1
fi

exit 0
