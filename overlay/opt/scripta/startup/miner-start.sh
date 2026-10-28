#!/bin/bash
# Blakestream-GaintB miner launcher (idempotent watchdog).
#
# Called by /etc/cron.d/scripta every ~30 seconds. Behaviour:
#   - If sgminer is already running   -> check API and log capture
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
umask 0027

LOG_TAG=blakestream-miner
SGMINER=/opt/scripta/bin/sgminer
RUNTIME_DIR=/run/blakestream-miner
RUNTIME_CONF=$RUNTIME_DIR/scripta-runtime-miner.conf
BUILDER_ERR=
BUILDER=/opt/scripta/bin/build-miner-runtime.py
APPLIER=/opt/scripta/bin/apply-board-assignments.py
PREPARE_RUNTIME=/opt/scripta/bin/prepare-runtime-dir.sh
LOCK=/var/run/blakestream-miner-restart.lock
FW_OPERATION_LOCK=/run/blakestream-firmware/bkb-firmware.lock
FW_OPERATION_PID=/run/blakestream-firmware/bkb-firmware.pid
FW_OPERATION_MAX_AGE=1200
FW_RECOVERY_REQUIRED=/var/lib/blakestream-firstboot/firmware-recovery-required
SCRIPTA_CONF=/opt/scripta/etc/scripta.conf
LOG_WRITER=/opt/scripta/bin/capped-log-writer.py
SGMINER_LOG_MAX=${SGMINER_LOG_MAX:-4194304}
SGMINER_LOG_KEEP=${SGMINER_LOG_KEEP:-2097152}

# Logging is independent of Dev Tools visibility. Only an explicit SD choice
# leaves RAM; absent or unreadable settings keep bounded RAM capture enabled.
SGMINER_LOG_CONFIG_OK=0
SGMINER_LOG_TARGET=$(/usr/bin/python -c "
import json,sys
valid=True
try:
  c=json.load(open('$SCRIPTA_CONF'))
except Exception:
  c={}
  valid=False
if not isinstance(c,dict):
  c={}
  valid=False
target=c.get('userDevLogTarget')
if target is not None and (not isinstance(target,(str,type(u''))) or target.upper() not in ('RAM','SD')):
  valid=False
target=target.upper() if isinstance(target,(str,type(u''))) else ''
print('SD' if target=='SD' else 'RAM')
sys.exit(0 if valid else 1)
" 2>/dev/null) && SGMINER_LOG_CONFIG_OK=1
case "$SGMINER_LOG_TARGET" in
    SD)
        SGMINER_LOG=/var/log/blakestream-miner/sgminer.log
        ;;
    *)
        SGMINER_LOG_TARGET=RAM
        SGMINER_LOG=/run/blakestream-miner/sgminer.log
        ;;
esac

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | logger -t "$LOG_TAG"; echo "$*"; }

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

cleanup_legacy_log_tails() {
    # Older dev builds launched sgminer through an orphanable tail -F wrapper:
    #   bash -c tail -F /var/log/sgminer.log 2>&1
    # The current launcher writes logs directly or through capped-log-writer.py,
    # so an orphaned parent-owned tail is stale diagnostic noise.
    local pid ppid args
    while read -r pid ppid args; do
        if [[ "$ppid" == "1" && "$args" == "bash -c tail -F /var/log/sgminer.log 2>&1" ]]; then
            log "cleaning stale legacy sgminer log tail wrapper pid $pid"
            pkill -TERM -P "$pid" -f '^tail -F /var/log/sgminer\.log$' 2>/dev/null || true
            kill "$pid" 2>/dev/null || true
        elif [[ "$ppid" == "1" && "$args" == "tail -F /var/log/sgminer.log" ]]; then
            log "cleaning stale legacy sgminer log tail pid $pid"
            kill "$pid" 2>/dev/null || true
        fi
    done < <(ps -eo pid,ppid,args --no-headers)
}

stop_sgminer() {
    for s in $(screen -ls 2>/dev/null | awk '/sgminer/ {print $1}'); do
        screen -S "$s" -X quit 2>/dev/null
    done
    sleep 1
    pkill -9 -f "$SGMINER " 2>/dev/null
    cleanup_legacy_log_tails
}

fix_screen_dir() {
    if [[ -x /etc/init.d/screen-cleanup ]]; then
        /etc/init.d/screen-cleanup start >/dev/null 2>&1 || true
        return
    fi

    [ -d /var/run/screen ] || mkdir -p /var/run/screen 2>/dev/null || true
    chown root:utmp /var/run/screen 2>/dev/null || true

    BINARYPERM=$(stat -c%a /usr/bin/screen 2>/dev/null || echo 0)
    if [[ "$BINARYPERM" -ge 4000 ]]; then
        chmod 0755 /var/run/screen 2>/dev/null || true
    elif [[ "$BINARYPERM" -ge 2000 ]]; then
        chmod 0775 /var/run/screen 2>/dev/null || true
    else
        chmod 1777 /var/run/screen 2>/dev/null || true
    fi
}

protect_sgminer_oom() {
    # The BK-B controller only has ~193 MiB RAM. Bursty diagnostics or PHP
    # workers can trip the kernel lowmemorykiller, which otherwise treats
    # sgminer like any other adj=0 process and may kill the miner first.
    local pid
    for pid in $(pgrep -f "^$SGMINER "; pgrep -f "^SCREEN -dmS sgminer" 2>/dev/null); do
        if [[ -w "/proc/$pid/oom_score_adj" ]]; then
            echo -900 > "/proc/$pid/oom_score_adj" 2>/dev/null || true
        elif [[ -w "/proc/$pid/oom_adj" ]]; then
            echo -16 > "/proc/$pid/oom_adj" 2>/dev/null || true
        fi
    done
}

log_capture_present() {
    # Healthy capture costs no Python watchdog worker. Match the reader's
    # actual stdin pipe, not log mtime (a quiet miner is legitimate).
    local pipe reader
    pipe=$(readlink "/proc/$1/fd/1" 2>/dev/null) || return 1
    [[ "$pipe" == pipe:\[*\] ]] || return 1
    for reader in $(pgrep -f "^/usr/bin/python(2([.]7)?)? $LOG_WRITER " 2>/dev/null); do
        if [[ "$(readlink "/proc/$reader/fd/0" 2>/dev/null)" == "$pipe" ]]; then
            return 0
        fi
    done
    return 1
}

api_command_ok() {
    local port="$1"
    /usr/bin/python - "$port" <<'PY'
import json
import socket
import sys

port = int(sys.argv[1])
s = None
try:
    s = socket.socket()
    s.settimeout(2)
    s.connect(('127.0.0.1', port))
    s.sendall(b'{"command":"version"}')
    try:
        s.shutdown(socket.SHUT_WR)
    except socket.error:
        pass
    data = b''
    while True:
        chunk = s.recv(8192)
        if not chunk:
            break
        data += chunk
    text = data.decode('utf-8', 'replace').rstrip('\x00').strip()
    parsed = json.loads(text)
    status = parsed.get('STATUS') or []
    if status and status[0].get('STATUS') in ('S', 'I'):
        sys.exit(0)
except Exception:
    pass
finally:
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
sys.exit(1)
PY
}

write_api_hang_count() {
    local value="$1" tmp
    tmp=$(mktemp "$RUNTIME_DIR/.miner-api-hang.count.XXXXXX") || return 1
    if ! printf '%s\n' "$value" > "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    chown root:www-data "$tmp" 2>/dev/null || true
    chmod 0640 "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$API_HANG_COUNT_FILE"
}

# Re-exec under flock if we don't already hold it. -n means non-blocking:
# if another start/restart is already running, we exit immediately rather
# than queue up (cron will fire us again in 30s anyway).
if [[ "${BS_LAUNCHER_LOCKED:-}" != "1" ]]; then
    export BS_LAUNCHER_LOCKED=1
    exec flock -n "$LOCK" "$0" "$@" || exit 0
fi

# Firmware reads and writes temporarily remove the STM32 USB bridge. Starting
# sgminer during that interval makes board assignment fail and causes the cron
# watchdog to enter a stop/start loop. Only dfu_flash.py's final restart may
# bypass this guard, after it has returned the bridge to application mode.
if [[ "${BS_FIRMWARE_RESTART:-}" != "1" ]] && firmware_operation_active; then
    log "firmware operation active; miner start deferred"
    exit 0
fi

# Prepare a root-owned, non-writable parent before any runtime-file read or
# write. Never fall back to /tmp or a pre-existing insecure path.
if [[ ! -x "$PREPARE_RUNTIME" ]] || ! "$PREPARE_RUNTIME"; then
    log "ERROR: secure miner runtime directory could not be prepared"
    exit 1
fi

if [[ -f "$FW_RECOVERY_REQUIRED" ]]; then
    log "ERROR: firmware recovery required; bridge is held in DFU and miner start is refused"
    stop_sgminer
    exit 1
fi

cleanup_legacy_log_tails

# 1. Idempotent check — is sgminer already running?
#    A wedged sgminer keeps its process alive while its API goes dead, which a
#    plain pgrep can never catch — so a running instance must also answer an
#    API probe. After 3 consecutive failed probes (~90s at the 30s cron
#    cadence) the hung instance is killed and relaunched via the normal path
#    below. The counter lives in tmpfs so a reboot clears it, and a young
#    process (<60s) is never probed — its API may legitimately still be coming
#    up.
API_HANG_COUNT_FILE=$RUNTIME_DIR/miner-api-hang.count
API_HANG_THRESHOLD=3
API_HANG_MIN_UPTIME=60
if pgrep -f "$SGMINER " >/dev/null 2>&1; then
    protect_sgminer_oom
    SG_PID=$(pgrep -f "$SGMINER " | head -1)
    # A dead logger is independent of API health. Recovery checks the actual
    # pipe readers and the running miner's original destination, never mtime
    # or today's settings. It never signals or restarts the miner.
    if ! log_capture_present "$SG_PID" && ! /usr/bin/python "$LOG_WRITER" --recover "$SG_PID" "$SGMINER"; then
        log "WARNING: sgminer log recovery could not be verified; miner left running"
    fi
    SG_UP=$(ps -o etimes= -p "$SG_PID" 2>/dev/null | tr -dc '0-9')
    if [[ -z "$SG_UP" || "$SG_UP" -lt "$API_HANG_MIN_UPTIME" ]]; then
        exit 0
    fi
    API_PORT=$(grep -o '"api-port"[^,}]*' "$RUNTIME_CONF" 2>/dev/null | head -1 | grep -o '[0-9]\+' || echo 4028)
    if api_command_ok "$API_PORT"; then
        rm -f "$API_HANG_COUNT_FILE" 2>/dev/null || true
        exit 0
    fi
    FAILS=$(cat "$API_HANG_COUNT_FILE" 2>/dev/null | tr -dc '0-9')
    FAILS=$(( ${FAILS:-0} + 1 ))
    if [[ "$FAILS" -lt "$API_HANG_THRESHOLD" ]]; then
        write_api_hang_count "$FAILS" || true
        log "sgminer running but API probe failed ($FAILS/$API_HANG_THRESHOLD); restart after $API_HANG_THRESHOLD consecutive failures"
        exit 0
    fi
    rm -f "$API_HANG_COUNT_FILE" 2>/dev/null || true
    log "sgminer HUNG: process alive but API dead for $FAILS consecutive probes — killing and relaunching"
    stop_sgminer
    # fall through to the normal relaunch path below
fi

# 2. Reap any leftover empty screens
for s in $(screen -ls 2>/dev/null | awk '/sgminer/ {print $1}'); do
    screen -S "$s" -X quit 2>/dev/null
done
sleep 1

# 3. Generate the runtime config from saved-pools + assignments
if [[ -x "$BUILDER" ]]; then
    BUILDER_ERR=$(mktemp "$RUNTIME_DIR/.build-miner-runtime.err.XXXXXX") || {
        log "ERROR: cannot create secure builder error log"
        exit 1
    }
    trap 'rm -f -- "$BUILDER_ERR"' EXIT
    SUMMARY=$(/usr/bin/python "$BUILDER" 2>"$BUILDER_ERR")
    BUILD_RC=$?
else
    log "ERROR: $BUILDER missing; refusing to start sgminer without a runtime config"
    exit 1
fi

# 4. Decide what to launch
case $BUILD_RC in
    0)
        log "starting sgminer with runtime config: $SUMMARY"
        fix_screen_dir
        # Both destinations live under root-owned, non-group-writable parents.
        # Never read legacy logs from the shared web runtime: they may be
        # attacker-planted symlinks from an older image.
        : >> "$SGMINER_LOG" || { log "ERROR: cannot create sealed sgminer log"; exit 1; }
        chown root:www-data "$SGMINER_LOG" || exit 1
        chmod 0660 "$SGMINER_LOG" || exit 1
        printf "===== %s sgminer launch =====\n" "$(date '+%Y-%m-%d %H:%M:%S')" \
            | /usr/bin/python "$LOG_WRITER" --once "$SGMINER_LOG" "$SGMINER_LOG_MAX" "$SGMINER_LOG_KEEP"
        screen -dmS sgminer bash -c "BS_SGMINER_LOG='$SGMINER_LOG' BS_SGMINER_LOG_MAX='$SGMINER_LOG_MAX' BS_SGMINER_LOG_KEEP='$SGMINER_LOG_KEEP' '$SGMINER' --text-only -c '$RUNTIME_CONF' --api-listen 2>&1 | /usr/bin/python '$LOG_WRITER' '$SGMINER_LOG' '$SGMINER_LOG_MAX' '$SGMINER_LOG_KEEP'"
        ;;
    2)
        log "no boards assigned to any pool — sgminer not started (idle)"
        exit 0
        ;;
    *)
        ERR=$(head -5 "$BUILDER_ERR" 2>/dev/null)
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
    protect_sgminer_oom
    if api_command_ok "$API_PORT"; then
        log "sgminer started OK (api ready on port $API_PORT after ${i}s)"
        api_ready=1
        break
    fi
done

protect_sgminer_oom

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
