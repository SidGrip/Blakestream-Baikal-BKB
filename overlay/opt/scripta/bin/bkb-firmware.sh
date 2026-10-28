#!/bin/sh
# On-device firmware helper for the BK-B STM32 bridge, invoked by the web Dev
# Tools "Firmware" tab through a narrow sudoers entry. Runs as root because the
# reversible DFU flasher needs GPIO + USB access. Writes target ONLY the chip's
# application flash; the ROM bootloader is never touched (chip stays recoverable).
# Public entry points are detached start-job operations plus abort-job for a
# still-read-only verify. The run-job verb is the wrapper's internal worker;
# legacy synchronous flash verbs are deliberately unsupported because they
# bypassed durable job state and restart gating.
set -u
umask 027
FW=/opt/scripta/firmware
DFU="$FW/dfu_flash.py"
STOCK="$FW/stock.bin"
STOCK_MD5=d5f844be2952fd713717a71a67e2be61
CUSTOM="$FW/custom.bin"
CUSTOM_MD5=18babe0c4c67d9146e230f9dc423f2f2
PY=/usr/bin/python
RUNDIR=/run/blakestream-firmware
PROG=$RUNDIR/bkb-firmware-progress.log
LOCK=$RUNDIR/bkb-firmware.lock
PIDFILE=$RUNDIR/bkb-firmware.pid
META=$RUNDIR/bkb-firmware-meta.env
RESULT=$RUNDIR/bkb-firmware-result.env
PHASE=$RUNDIR/bkb-firmware-phase
ABORT=$RUNDIR/bkb-firmware-abort
APPLIED=$RUNDIR/bkb-firmware-result-applied
START_LOCK=$RUNDIR/bkb-firmware-start.lock
RECOVERY_DIR=/var/lib/blakestream-firstboot
RECOVERY_REQUIRED=$RECOVERY_DIR/firmware-recovery-required
BRIDGE_GLOBAL_READY=$FW/BRIDGE_GLOBAL_V1_READY
BRIDGE_GLOBAL_OLD_READY=$FW/BRIDGE_GLOBAL_V11_READY
IDENTITY=/opt/scripta/etc/bkb-firmware-identity.json
SELF=/opt/scripta/bin/bkb-firmware.sh

# Start a fresh, www-data-readable progress log for the Dev Tools terminal
# overlay; dfu_flash.py output is written/tee'd here live (python -u).
fw_ensure_run() {
    /bin/sh /opt/scripta/bin/prepare-runtime-dir.sh || return 1
    [ -d "$RUNDIR" ] && [ ! -L "$RUNDIR" ] || return 1
    [ "$(stat -c '%u:%g:%a' "$RUNDIR" 2>/dev/null || echo unsafe)" = '0:33:750' ] || return 1
}

fwprog_start() {
    fw_ensure_run || return 1
    # Publish a new inode for every operation. Never truncate an inode that
    # could have survived from an older, less strictly protected runtime.
    rm -f -- "$PROG" || return 1
    : > "$PROG" || return 1
    chown root:www-data "$PROG"
    chmod 0640 "$PROG"
}

fwprog_note() {
    fw_ensure_run || return 1
    printf '%s\n' "$*" >> "$PROG" 2>/dev/null || true
}

fw_set_phase() {
    fw_ensure_run || return 1
    tmp="$(mktemp "$RUNDIR/.phase.tmp.XXXXXX")" || return 1
    printf '%s\n' "$1" > "$tmp" || { rm -f -- "$tmp"; return 1; }
    chown root:www-data "$tmp"
    chmod 0640 "$tmp"
    mv -f -- "$tmp" "$PHASE"
    : >> "$LOCK"
    chown root:www-data "$LOCK"
    chmod 0640 "$LOCK"
}

fw_pid_running() {
    pid="${1:-}"
    [ -n "$pid" ] || return 1
    case "$pid" in *[!0-9]*|'') return 1;; esac
    [ -d "/proc/$pid" ]
}

fw_current_pid() {
    [ -f "$PIDFILE" ] || return 1
    head -n 1 "$PIDFILE" 2>/dev/null | tr -dc '0-9'
}

fw_cleanup_if_stale() {
    pid="$(fw_current_pid 2>/dev/null || true)"
    if fw_pid_running "$pid"; then
        return 0
    fi
    if [ -f "$LOCK" ]; then
        now="$(date +%s)"
        mt="$(stat -c '%Y' "$LOCK" 2>/dev/null || echo 0)"
        age=$((now - mt))
        if [ "$age" -gt 1200 ]; then
            rm -f "$LOCK" "$PIDFILE" "$META" "$PHASE" "$ABORT" 2>/dev/null || true
        fi
    fi
}

fw_active() {
    fw_cleanup_if_stale
    pid="$(fw_current_pid 2>/dev/null || true)"
    if fw_pid_running "$pid"; then
        return 0
    fi
    if [ -f "$LOCK" ]; then
        now="$(date +%s)"
        mt="$(stat -c '%Y' "$LOCK" 2>/dev/null || echo 0)"
        [ $((now - mt)) -le 1200 ] && return 0
    fi
    return 1
}

fw_result_write() {
    op="$1"; ok="$2"; rc="$3"; started="$4"; finished="$5"; chip_md5="${6:-}"; message="${7:-}"
    tmp="$(mktemp "$RUNDIR/.result.tmp.XXXXXX")" || return 1
    {
        echo "op=$op"
        echo "ok=$ok"
        echo "exit_code=$rc"
        echo "started_at=$started"
        echo "finished_at=$finished"
        echo "elapsed=$((finished - started))"
        [ -n "$chip_md5" ] && echo "chip_md5=$chip_md5"
        [ -n "$message" ] && echo "message=$message"
        true
    } > "$tmp" || { rm -f -- "$tmp"; return 1; }
    chown root:www-data "$tmp"
    chmod 0640 "$tmp"
    mv -f -- "$tmp" "$RESULT"
}

fw_mark_bridge_global_ready() {
    tmp="$BRIDGE_GLOBAL_READY.$$"
    {
        echo "bridge_firmware=CUSTOM V1"
        echo "chip_md5=$CUSTOM_MD5"
    } > "$tmp" || return 1
    chown root:root "$tmp" 2>/dev/null || true
    chmod 0644 "$tmp" 2>/dev/null || true
    fw_fsync_file "$tmp" || { rm -f -- "$tmp"; return 1; }
    mv "$tmp" "$BRIDGE_GLOBAL_READY" || { rm -f -- "$tmp"; return 1; }
    fw_fsync_dir "$FW"
}

fw_fsync_file() {
    "$PY" -c 'import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY); os.fsync(fd); os.close(fd)' "$1"
}

fw_fsync_dir() {
    "$PY" -c 'import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); os.fsync(fd); os.close(fd)' "$1"
}

fw_mark_recovery_required() {
    [ -d "$RECOVERY_DIR" ] && [ ! -L "$RECOVERY_DIR" ] || return 1
    recovery_tmp="$(mktemp "$RECOVERY_DIR/.firmware-recovery-required.XXXXXX")" || return 1
    {
        echo "recovery_required=1"
        echo "operation=$1"
        echo "recorded_at=$(date +%s)"
    } > "$recovery_tmp" || { rm -f -- "$recovery_tmp"; return 1; }
    chown root:root "$recovery_tmp" || { rm -f -- "$recovery_tmp"; return 1; }
    chmod 0600 "$recovery_tmp" || { rm -f -- "$recovery_tmp"; return 1; }
    fw_fsync_file "$recovery_tmp" || { rm -f -- "$recovery_tmp"; return 1; }
    mv -f -- "$recovery_tmp" "$RECOVERY_REQUIRED" || { rm -f -- "$recovery_tmp"; return 1; }
    fw_fsync_dir "$RECOVERY_DIR"
}

fw_clear_recovery_required() {
    rm -f -- "$RECOVERY_REQUIRED" || return 1
    if ! fw_fsync_dir "$RECOVERY_DIR"; then
        # Keep the live gate present if durable removal cannot be proven.
        fw_mark_recovery_required clear-fsync-failed >/dev/null 2>&1 || true
        return 1
    fi
    [ ! -e "$RECOVERY_REQUIRED" ] && [ ! -L "$RECOVERY_REQUIRED" ]
}

fw_clear_bridge_capability() {
    # The first restart precedes the web result handler. Invalidate its old
    # identity now so a cached CUSTOM V1 record cannot authorize saved Index
    # overrides after the bridge has been replaced with stock firmware.
    rm -f -- "$BRIDGE_GLOBAL_READY" "$BRIDGE_GLOBAL_OLD_READY" "$IDENTITY" || return 1
    fw_fsync_dir "$FW" || return 1
    fw_fsync_dir "$(dirname "$IDENTITY")"
}

fw_restart_miner() {
    BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh "$@"
}

fw_job_finish() {
    fw_set_phase done
    rm -f "$LOCK" "$PIDFILE" "$ABORT" 2>/dev/null || true
}

fw_job_recovery_hold() {
    fw_set_phase recovery-required
    rm -f "$LOCK" "$PIDFILE" "$ABORT" 2>/dev/null || true
}

fw_start_job() {
    op="$1"
    case "$op" in verify|flash-custom|revert) ;; *) echo '{"ok":false,"error":"bad firmware job"}'; exit 1;; esac
    fw_ensure_run || { echo '{"ok":false,"error":"unsafe firmware runtime"}'; exit 1; }
    if [ "$op" = verify ] && [ -f "$RECOVERY_REQUIRED" ]; then
        echo '{"ok":false,"error":"bridge recovery required; flash a validated stock or custom image"}'
        exit 1
    fi
    # Serialize the idle check and child PID publication. The operation's
    # PID/phase files become the long-lived guard; fd 9 must not leak into the
    # miner processes that the worker eventually starts.
    exec 9>>"$START_LOCK" || { echo '{"ok":false,"error":"firmware job lock unavailable"}'; exit 1; }
    chown root:root "$START_LOCK" || exit 1
    chmod 0600 "$START_LOCK" || exit 1
    if ! flock -n 9; then
        echo '{"ok":false,"error":"a firmware operation is already in progress"}'
        exit 1
    fi
    if fw_active; then
        echo '{"ok":false,"error":"a firmware operation is already in progress"}'
        exit 1
    fi
    rm -f "$LOCK" "$PIDFILE" "$META" "$RESULT" "$PHASE" "$ABORT" "$APPLIED" 2>/dev/null || true
    : > "$APPLIED" || exit 1
    chown root:www-data "$APPLIED"
    chmod 0660 "$APPLIED"
    started="$(date +%s)"
    meta_tmp="$(mktemp "$RUNDIR/.meta.tmp.XXXXXX")" || exit 1
    {
        echo "op=$op"
        echo "started_at=$started"
    } > "$meta_tmp" || { rm -f -- "$meta_tmp"; exit 1; }
    chown root:www-data "$meta_tmp"
    chmod 0640 "$meta_tmp"
    mv -f -- "$meta_tmp" "$META"
    fwprog_start
    fw_set_phase starting
    nohup "$SELF" run-job "$op" 9>&- >/dev/null 2>&1 &
    pid=$!
    echo "$pid" > "$PIDFILE"
    chown root:www-data "$PIDFILE" "$LOCK"
    chmod 0640 "$PIDFILE" "$LOCK"
    exec 9>&-
    echo "{\"ok\":true,\"started\":true,\"op\":\"$op\",\"pid\":$pid,\"started_at\":$started}"
}

fw_run_verify_job() {
    started="$(date +%s)"
    echo "$$" > "$PIDFILE" || exit 1
    chown root:www-data "$PIDFILE"
    chmod 0640 "$PIDFILE"
    fw_set_phase read
    out="$(mktemp "$RUNDIR/.chip-readback.tmp.XXXXXX")" || exit 1
    "$PY" -u "$DFU" backup "$out" >> "$PROG" 2>&1
    rc=$?
    if [ -f "$ABORT" ]; then
        fwprog_note "Abort requested; read stopped before any firmware write."
        rm -f -- "$out"
        finished="$(date +%s)"
        fw_result_write verify 0 130 "$started" "$finished" "" "aborted"
        fw_job_finish
        exit 130
    fi
    if [ "$rc" -eq 0 ] && [ -s "$out" ]; then
        chip_md5="$(md5sum "$out" | cut -d' ' -f1)"
        rm -f "$out"
        fwprog_note "VERIFY READ OK: chip md5 $chip_md5"
        finished="$(date +%s)"
        fw_result_write verify 1 0 "$started" "$finished" "$chip_md5" ""
        fw_job_finish
        exit 0
    fi
    rm -f "$out"
    finished="$(date +%s)"
    fw_result_write verify 0 "$rc" "$started" "$finished" "" "chip read failed"
    fw_job_finish
    exit "$rc"
}

fw_run_flash_job() {
    op="$1"
    img="$2"
    expect="$3"
    label="$4"
    started="$(date +%s)"
    echo "$$" > "$PIDFILE" || exit 1
    chown root:www-data "$PIDFILE"
    chmod 0640 "$PIDFILE"
    fw_set_phase flash
    # Fail closed before entering DFU. Until a v1 write has read-back-verified,
    # the regenerated runtime config must retain compatible per-board dest=0.
    if ! fw_clear_bridge_capability; then
        fwprog_note "Could not durably clear bridge capability state; refusing to write."
        finished="$(date +%s)"
        fw_result_write "$op" 0 1 "$started" "$finished" "" "$label capability state could not be cleared"
        fw_job_finish
        exit 1
    fi
    was_recovery=0
    [ -f "$RECOVERY_REQUIRED" ] && was_recovery=1
    # Publish a persistent fail-closed gate before the flasher can issue its
    # first write. A process crash, SIGKILL, or controller power loss therefore
    # cannot let the cron watchdog boot an unverified bridge image afterward.
    if ! fw_mark_recovery_required "$op"; then
        fwprog_note "Could not publish the persistent firmware recovery hold; refusing to write."
        finished="$(date +%s)"
        fw_result_write "$op" 0 1 "$started" "$finished" "" "$label recovery hold could not be created"
        fw_job_finish
        exit 1
    fi
    BKB_DEFER_MINER_RESTART=1 BKB_RECOVERY_REQUIRED="$was_recovery" \
        "$PY" -u "$DFU" flash "$img" --md5 "$expect" --confirm >> "$PROG" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ] && grep -q 'FLASH OK + VERIFIED' "$PROG" 2>/dev/null; then
        if ! fw_clear_recovery_required; then
            fwprog_note "Firmware verified, but the persistent recovery hold could not be cleared; miner remains stopped."
            finished="$(date +%s)"
            fw_result_write "$op" 0 1 "$started" "$finished" "" "$label recovery hold could not be cleared"
            fw_job_recovery_hold
            exit 1
        fi
        if [ "$op" = "flash-custom" ]; then
            fw_mark_bridge_global_ready || rc=1
        fi
        if [ "$rc" -ne 0 ]; then
            fwprog_note "Failed to enable the verified V1 runtime capability."
            fw_restart_miner >> "$PROG" 2>&1 || true
            finished="$(date +%s)"
            fw_result_write "$op" 0 "$rc" "$started" "$finished" "" "$label capability activation failed"
            fw_job_finish
            exit "$rc"
        fi
        fw_set_phase restart
        fw_restart_miner >> "$PROG" 2>&1
        rc=$?
        finished="$(date +%s)"
        if [ "$rc" -ne 0 ]; then
            fwprog_note "Firmware readback verified, but miner restart failed; inspect the miner startup log."
            fw_result_write "$op" 0 "$rc" "$started" "$finished" "" "$label miner restart failed"
            fw_job_finish
            exit "$rc"
        fi
        fw_result_write "$op" 1 0 "$started" "$finished" "" ""
        fw_job_finish
        exit 0
    fi
    [ "$rc" -ne 0 ] || rc=1
    safe_failure=0
    case "$rc" in
      5|6|7) safe_failure=1 ;; # rollback was read-back-verified
      2|3|8) [ "$was_recovery" -eq 0 ] && safe_failure=1 ;; # no target write
    esac
    if [ "$safe_failure" -eq 1 ] && fw_clear_recovery_required; then
        fw_restart_miner >> "$PROG" 2>&1 || true
        result_message="$label flash failed; previous verified firmware retained"
        finish_mode=normal
    else
        fwprog_note "RECOVERY REQUIRED: bridge held in DFU; miner restart suppressed. Flash a validated stock or custom image to recover."
        result_message="$label recovery required; bridge held in DFU"
        finish_mode=recovery
    fi
    finished="$(date +%s)"
    fw_result_write "$op" 0 "$rc" "$started" "$finished" "" "$result_message"
    if [ "$finish_mode" = recovery ]; then
        fw_job_recovery_hold
    else
        fw_job_finish
    fi
    exit "$rc"
}

case "${1:-}" in
  start-job)
    fw_start_job "${2:-}"
    ;;
  run-job)
    fw_ensure_run || exit 1
    case "${2:-}" in
      verify)
        if [ -f "$RECOVERY_REQUIRED" ]; then
            fw_result_write verify 0 9 "$(date +%s)" "$(date +%s)" "" "bridge recovery required; identity read suppressed"
            fw_job_recovery_hold
            exit 9
        fi
        [ -f "$DFU" ] || { fw_result_write verify 0 1 "$(date +%s)" "$(date +%s)" "" "dfu_flash.py missing"; fw_job_finish; exit 1; }
        fwprog_start || exit 1
        fw_run_verify_job
        ;;
      flash-custom)
        [ -f "$DFU" ] || { fw_result_write flash-custom 0 1 "$(date +%s)" "$(date +%s)" "" "dfu_flash.py missing"; fw_job_finish; exit 1; }
        [ -f "$FW/ENCODER_READY" ] || { fw_result_write flash-custom 0 1 "$(date +%s)" "$(date +%s)" "" "ENCODER_READY missing"; fw_job_finish; exit 1; }
        [ -f "$CUSTOM" ] || { fw_result_write flash-custom 0 1 "$(date +%s)" "$(date +%s)" "" "custom image missing"; fw_job_finish; exit 1; }
        have=$(md5sum "$CUSTOM" | cut -d' ' -f1)
        [ "$have" = "$CUSTOM_MD5" ] || { fw_result_write flash-custom 0 1 "$(date +%s)" "$(date +%s)" "" "custom md5 mismatch"; fw_job_finish; exit 1; }
        fw_run_flash_job flash-custom "$CUSTOM" "$CUSTOM_MD5" custom
        ;;
      revert)
        [ -f "$DFU" ] || { fw_result_write revert 0 1 "$(date +%s)" "$(date +%s)" "" "dfu_flash.py missing"; fw_job_finish; exit 1; }
        [ -f "$STOCK" ] || { fw_result_write revert 0 1 "$(date +%s)" "$(date +%s)" "" "stock image missing"; fw_job_finish; exit 1; }
        have=$(md5sum "$STOCK" | cut -d' ' -f1)
        [ "$have" = "$STOCK_MD5" ] || { fw_result_write revert 0 1 "$(date +%s)" "$(date +%s)" "" "stock md5 mismatch"; fw_job_finish; exit 1; }
        fw_run_flash_job revert "$STOCK" "$STOCK_MD5" stock
        ;;
      *)
        fw_result_write unknown 0 1 "$(date +%s)" "$(date +%s)" "" "bad firmware job"
        fw_job_finish
        exit 1
        ;;
    esac
    ;;
  abort-job)
    fw_cleanup_if_stale
    pid="$(fw_current_pid 2>/dev/null || true)"
    op="$(grep '^op=' "$META" 2>/dev/null | head -n 1 | cut -d= -f2-)"
    if ! fw_pid_running "$pid"; then
        echo '{"ok":false,"error":"no firmware job is running"}'
        exit 1
    fi
    if [ "$op" != "verify" ]; then
        echo '{"ok":false,"error":"firmware writes cannot be aborted"}'
        exit 1
    fi
    : > "$ABORT" || exit 1
    chown root:www-data "$ABORT"
    chmod 0640 "$ABORT"
    fwprog_note "Abort requested by web UI; waiting for safe read cleanup."
    echo '{"ok":true,"message":"abort requested; waiting for safe read cleanup"}'
    ;;
  *)
    echo '{"ok":false,"error":"usage: start-job verify|revert|flash-custom | abort-job"}'; exit 1;;
esac
