#!/bin/sh
# On-device firmware helper for the BK-B STM32 bridge, invoked by the web Dev
# Tools "Firmware" tab through a narrow sudoers entry. Runs as root because the
# reversible DFU flasher needs GPIO + USB access. Writes target ONLY the chip's
# application flash; the ROM bootloader is never touched (chip stays recoverable).
#   verify                 - read-only: read the chip, print its md5 (interrupts mining ~40s)
#   revert --confirm        - flash the staged stock image back onto the chip (reversible)
#   flash-custom --confirm  - flash the staged adjustable-clock image onto the chip (reversible)
set -u
FW=/opt/scripta/firmware
DFU="$FW/dfu_flash.py"
STOCK="$FW/stock.bin"
STOCK_MD5=d5f844be2952fd713717a71a67e2be61
CUSTOM="$FW/custom.bin"
CUSTOM_MD5=18babe0c4c67d9146e230f9dc423f2f2
PY=/usr/bin/python
RUNDIR=/run/blakestream
PROG=$RUNDIR/bkb-firmware-progress.log
LOCK=$RUNDIR/bkb-firmware.lock
PIDFILE=$RUNDIR/bkb-firmware.pid
META=$RUNDIR/bkb-firmware-meta.env
RESULT=$RUNDIR/bkb-firmware-result.env
PHASE=$RUNDIR/bkb-firmware-phase
ABORT=$RUNDIR/bkb-firmware-abort
APPLIED=$RUNDIR/bkb-firmware-result-applied
BRIDGE_GLOBAL_READY=$FW/BRIDGE_GLOBAL_V1_READY
BRIDGE_GLOBAL_OLD_READY=$FW/BRIDGE_GLOBAL_V11_READY
SELF=/opt/scripta/bin/bkb-firmware.sh

# Start a fresh, www-data-readable progress log for the Dev Tools terminal
# overlay; dfu_flash.py output is written/tee'd here live (python -u).
fw_ensure_run() {
    mkdir -p "$RUNDIR" 2>/dev/null || true
    chown root:www-data "$RUNDIR" 2>/dev/null || true
    chmod 0775 "$RUNDIR" 2>/dev/null || true
}

fwprog_start() {
    fw_ensure_run
    : > "$PROG" 2>/dev/null && chmod 664 "$PROG" 2>/dev/null || true
}

fwprog_note() {
    fw_ensure_run
    printf '%s\n' "$*" >> "$PROG" 2>/dev/null || true
}

fw_set_phase() {
    fw_ensure_run
    printf '%s\n' "$1" > "$PHASE" 2>/dev/null || true
    chmod 664 "$PHASE" 2>/dev/null || true
    touch "$LOCK" 2>/dev/null || true
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
    tmp="$RESULT.$$"
    {
        echo "op=$op"
        echo "ok=$ok"
        echo "exit_code=$rc"
        echo "started_at=$started"
        echo "finished_at=$finished"
        echo "elapsed=$((finished - started))"
        [ -n "$chip_md5" ] && echo "chip_md5=$chip_md5"
        [ -n "$message" ] && echo "message=$message"
    } > "$tmp" 2>/dev/null || true
    chmod 664 "$tmp" 2>/dev/null || true
    mv "$tmp" "$RESULT" 2>/dev/null || true
}

fw_mark_bridge_global_ready() {
    tmp="$BRIDGE_GLOBAL_READY.$$"
    {
        echo "bridge_firmware=CUSTOM V1"
        echo "chip_md5=$CUSTOM_MD5"
    } > "$tmp" || return 1
    chown root:root "$tmp" 2>/dev/null || true
    chmod 0644 "$tmp" 2>/dev/null || true
    mv "$tmp" "$BRIDGE_GLOBAL_READY"
}

fw_write_identity() {
    identity="$1"
    chip_md5="$2"
    now="$(date +%s)"
    for path in "$RUNDIR/bkb-firmware-identity.json" /opt/scripta/etc/bkb-firmware-identity.json; do
        dir="$(dirname "$path")"
        mkdir -p "$dir" 2>/dev/null || continue
        tmp="$path.tmp.$$"
        printf '{"chip_md5":"%s","identity":"%s","at":%s}\n' \
            "$chip_md5" "$identity" "$now" > "$tmp" || continue
        chown root:www-data "$tmp" 2>/dev/null || true
        chmod 0664 "$tmp" 2>/dev/null || true
        mv "$tmp" "$path" 2>/dev/null || rm -f "$tmp"
    done
}

fw_restart_miner() {
    BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh "$@"
}

fw_job_finish() {
    fw_set_phase done
    rm -f "$LOCK" "$PIDFILE" "$ABORT" 2>/dev/null || true
}

fw_start_job() {
    op="$1"
    case "$op" in verify|flash-custom|revert) ;; *) echo '{"ok":false,"error":"bad firmware job"}'; exit 1;; esac
    fw_ensure_run
    if fw_active; then
        echo '{"ok":false,"error":"a firmware operation is already in progress"}'
        exit 1
    fi
    rm -f "$LOCK" "$PIDFILE" "$META" "$RESULT" "$PHASE" "$ABORT" "$APPLIED" 2>/dev/null || true
    started="$(date +%s)"
    {
        echo "op=$op"
        echo "started_at=$started"
    } > "$META"
    chmod 664 "$META" 2>/dev/null || true
    fwprog_start
    fw_set_phase starting
    nohup "$SELF" run-job "$op" >/dev/null 2>&1 &
    pid=$!
    echo "$pid" > "$PIDFILE"
    chmod 664 "$PIDFILE" "$LOCK" 2>/dev/null || true
    echo "{\"ok\":true,\"started\":true,\"op\":\"$op\",\"pid\":$pid,\"started_at\":$started}"
}

fw_run_verify_job() {
    started="$(date +%s)"
    echo "$$" > "$PIDFILE" 2>/dev/null || true
    chmod 664 "$PIDFILE" 2>/dev/null || true
    fw_set_phase read
    out=/tmp/bkb-chip-readback.bin
    rm -f "$out"
    "$PY" -u "$DFU" backup "$out" >> "$PROG" 2>&1
    rc=$?
    if [ -f "$ABORT" ]; then
        fwprog_note "Abort requested; read stopped before any firmware write."
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
    echo "$$" > "$PIDFILE" 2>/dev/null || true
    chmod 664 "$PIDFILE" 2>/dev/null || true
    fw_set_phase flash
    # Fail closed before entering DFU. Until a v1 write has read-back-verified,
    # the regenerated runtime config must retain compatible per-board dest=0.
    rm -f "$BRIDGE_GLOBAL_READY" "$BRIDGE_GLOBAL_OLD_READY" 2>/dev/null || true
    BKB_DEFER_MINER_RESTART=1 "$PY" -u "$DFU" flash "$img" --md5 "$expect" --confirm >> "$PROG" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ] && grep -q 'FLASH OK + VERIFIED' "$PROG" 2>/dev/null; then
        if [ "$op" = "flash-custom" ]; then
            fw_mark_bridge_global_ready || rc=1
            [ "$rc" -eq 0 ] && fw_write_identity "CUSTOM V1" "$CUSTOM_MD5"
        else
            fw_write_identity "STOCK" "$STOCK_MD5"
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
    fw_restart_miner >> "$PROG" 2>&1 || true
    finished="$(date +%s)"
    fw_result_write "$op" 0 "$rc" "$started" "$finished" "" "$label flash failed"
    fw_job_finish
    exit "$rc"
}

case "${1:-}" in
  start-job)
    fw_start_job "${2:-}"
    ;;
  run-job)
    fw_ensure_run
    case "${2:-}" in
      verify)
        [ -f "$DFU" ] || { fw_result_write verify 0 1 "$(date +%s)" "$(date +%s)" "" "dfu_flash.py missing"; fw_job_finish; exit 1; }
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
    touch "$ABORT" 2>/dev/null || true
    chmod 664 "$ABORT" 2>/dev/null || true
    fwprog_note "Abort requested by web UI; waiting for safe read cleanup."
    echo '{"ok":true,"message":"abort requested; waiting for safe read cleanup"}'
    ;;
  verify)
    [ -f "$DFU" ] || { echo '{"ok":false,"error":"dfu_flash.py missing"}'; exit 1; }
    out=/tmp/bkb-chip-readback.bin
    rm -f "$out"
    fwprog_start
    "$PY" -u "$DFU" backup "$out" > "$PROG" 2>&1
    if [ ! -s "$out" ]; then echo '{"ok":false,"error":"chip read failed (see Dev Tools progress)"}'; exit 1; fi
    md5=$(md5sum "$out" | cut -d' ' -f1)
    rm -f "$out"
    echo "{\"ok\":true,\"chip_md5\":\"$md5\"}"
    ;;
  revert)
    [ "${2:-}" = "--confirm" ] || { echo '{"ok":false,"error":"revert requires --confirm"}'; exit 1; }
    [ -f "$STOCK" ] || { echo '{"ok":false,"error":"stock image missing"}'; exit 1; }
    have=$(md5sum "$STOCK" | cut -d' ' -f1)
    [ "$have" = "$STOCK_MD5" ] || { echo "{\"ok\":false,\"error\":\"staged stock md5 mismatch ($have)\"}"; exit 1; }
    fwprog_start
    rm -f "$BRIDGE_GLOBAL_READY" "$BRIDGE_GLOBAL_OLD_READY" 2>/dev/null || true
    BKB_DEFER_MINER_RESTART=1 "$PY" -u "$DFU" flash "$STOCK" --md5 "$STOCK_MD5" --confirm > "$PROG" 2>&1
    rc=$?
    cat "$PROG"
    if [ "$rc" -eq 0 ] && grep -q 'FLASH OK + VERIFIED' "$PROG" 2>/dev/null; then
        fw_write_identity "STOCK" "$STOCK_MD5"
    fi
    fw_restart_miner >> "$PROG" 2>&1 || true
    exit "$rc"
    ;;
  flash-custom)
    [ "${2:-}" = "--confirm" ] || { echo '{"ok":false,"error":"flash-custom requires --confirm"}'; exit 1; }
    [ -f "$FW/ENCODER_READY" ] || { echo '{"ok":false,"error":"validated sgminer clock mapping and bridge-global coordinator not deployed (ENCODER_READY missing) — flashing V1 now would mis-drive the clocks"}'; exit 1; }
    [ -f "$CUSTOM" ] || { echo '{"ok":false,"error":"custom image missing"}'; exit 1; }
    have=$(md5sum "$CUSTOM" | cut -d' ' -f1)
    [ "$have" = "$CUSTOM_MD5" ] || { echo "{\"ok\":false,\"error\":\"staged custom md5 mismatch ($have)\"}"; exit 1; }
    fwprog_start
    rm -f "$BRIDGE_GLOBAL_READY" "$BRIDGE_GLOBAL_OLD_READY" 2>/dev/null || true
    BKB_DEFER_MINER_RESTART=1 "$PY" -u "$DFU" flash "$CUSTOM" --md5 "$CUSTOM_MD5" --confirm > "$PROG" 2>&1
    rc=$?
    cat "$PROG"
    if [ "$rc" -eq 0 ] && grep -q 'FLASH OK + VERIFIED' "$PROG" 2>/dev/null; then
        fw_mark_bridge_global_ready || rc=1
        [ "$rc" -eq 0 ] && fw_write_identity "CUSTOM V1" "$CUSTOM_MD5"
    fi
    fw_restart_miner >> "$PROG" 2>&1 || true
    exit "$rc"
    ;;
  *)
    echo '{"ok":false,"error":"usage: verify | revert --confirm | flash-custom --confirm"}'; exit 1;;
esac
