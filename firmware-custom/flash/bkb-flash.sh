#!/usr/bin/env bash
# Root-SSH client for the installed, guarded BK-B firmware workflow.
set -euo pipefail
cd "$(dirname "$0")"

usage() {
  cat <<'EOF'
Usage:
  bkb-flash.sh backup
  bkb-flash.sh verify <custom|stock>
  bkb-flash.sh flash <custom|stock> [--confirm]
  bkb-flash.sh rollback [--confirm]

Set MINER to an SSH hostname/address. Confirmed writes run only through the
installed root-owned bkb-firmware.sh detached job workflow.
EOF
}

if [[ "${1:-}" == --help || "${1:-}" == -h || $# == 0 ]]; then usage; exit 0; fi
MINER="${MINER:?set MINER=<miner-ip> to target your unit}"
[[ "$MINER" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || { echo 'invalid MINER hostname/address' >&2; exit 2; }
SSH=(ssh -o ConnectTimeout=8 "root@${MINER}")
SCP=(scp -o ConnectTimeout=8)
umask 077
mkdir -p backups

CUSTOM="../build/bkb-stm32f407-flash-clockmod-v1.bin"
STOCK="../../overlay/opt/scripta/firmware/stock.bin"
CUSTOM_MD5="18babe0c4c67d9146e230f9dc423f2f2"
STOCK_MD5="d5f844be2952fd713717a71a67e2be61"
WRAPPER=/opt/scripta/bin/bkb-firmware.sh
RUNDIR=/run/blakestream-firmware

cmd="${1:-}"; arg="${2:-}"; flag="${3:-}"
img_path() { case "$1" in custom) echo "$CUSTOM";; stock) echo "$STOCK";; *) return 1;; esac; }
want_md5() { case "$1" in custom) echo "$CUSTOM_MD5";; stock) echo "$STOCK_MD5";; *) return 1;; esac; }
remote_img() { case "$1" in custom) echo /opt/scripta/firmware/custom.bin;; stock) echo /opt/scripta/firmware/stock.bin;; *) return 1;; esac; }

check_images() {
  local kind=$1 local_img expected installed got
  local_img=$(img_path "$kind") || { echo 'expected custom or stock' >&2; exit 1; }
  expected=$(want_md5 "$kind")
  installed=$(remote_img "$kind")
  [[ "$(md5sum "$local_img" | cut -d' ' -f1)" == "$expected" ]] || {
    echo "ABORT: local $kind image md5 mismatch" >&2; exit 1;
  }
  got=$("${SSH[@]}" "md5sum '$installed' | cut -d' ' -f1")
  [[ "$got" == "$expected" ]] || { echo "ABORT: staged $kind image md5 mismatch on miner" >&2; exit 1; }
}

wait_job() {
  local job=$1 expected=${2:-}
  "${SSH[@]}" "$WRAPPER start-job $job"
  "${SSH[@]}" "
    i=0
    while [ \"\$i\" -lt 180 ]; do
      if [ -s '$RUNDIR/bkb-firmware-result.env' ]; then
        pid=\$(cat '$RUNDIR/bkb-firmware.pid' 2>/dev/null || true)
        if [ -z \"\$pid\" ] || ! kill -0 \"\$pid\" 2>/dev/null; then break; fi
      fi
      i=\$((i + 1)); sleep 2
    done
    test -s '$RUNDIR/bkb-firmware-result.env' || { echo 'firmware job timed out' >&2; exit 1; }
    cat '$RUNDIR/bkb-firmware-progress.log'
    cat '$RUNDIR/bkb-firmware-result.env'
    grep -qx 'ok=1' '$RUNDIR/bkb-firmware-result.env'
    ${expected:+grep -qx 'chip_md5=$expected' '$RUNDIR/bkb-firmware-result.env'}
  "
}

pull_preflash_backup() {
  local ts dst sz
  ts=$(date +%Y%m%d-%H%M%S)
  dst="backups/preflash-${ts}.bin"
  if "${SCP[@]}" root@${MINER}:/opt/scripta/backups/bkb-preflash-backup.bin "$dst" >/dev/null 2>&1; then
    sz=$(stat -c%s "$dst" 2>/dev/null || echo 0)
    [[ "$sz" == 1048576 ]] || { echo "WARN: retained backup is $sz bytes; treat with care" >&2; return 1; }
    echo "pre-flash backup -> $dst"; md5sum "$dst"
  else
    echo 'WARN: could not pull the persistent pre-flash backup' >&2
    return 1
  fi
}

guarded_read() {
  local mode=$1 kind=${2:-} remote_out=${3:-} expected installed
  expected=$(want_md5 "$kind" 2>/dev/null || true)
  installed=$(remote_img "$kind" 2>/dev/null || true)
  "${SSH[@]}" "
    set -eu
    /bin/sh /opt/scripta/bin/prepare-runtime-dir.sh
    test ! -e /var/lib/blakestream-firstboot/firmware-recovery-required
    exec 9>>'$RUNDIR/bkb-firmware-start.lock'
    flock -n 9
    test ! -e '$RUNDIR/bkb-firmware.lock'
    echo \$\$ > '$RUNDIR/bkb-firmware.pid'
    : > '$RUNDIR/bkb-firmware.lock'
    chown root:www-data '$RUNDIR/bkb-firmware.pid' '$RUNDIR/bkb-firmware.lock'
    chmod 0640 '$RUNDIR/bkb-firmware.pid' '$RUNDIR/bkb-firmware.lock'
    trap 'rm -f -- "$RUNDIR/bkb-firmware.lock" "$RUNDIR/bkb-firmware.pid"' EXIT HUP INT TERM
    if [ '$mode' = backup ]; then
      rm -f -- '$remote_out'
      /usr/bin/python -u /opt/scripta/firmware/dfu_flash.py backup '$remote_out'
    else
      /usr/bin/python -u /opt/scripta/firmware/dfu_flash.py verify '$installed' --md5 '$expected'
    fi
  "
}

case "$cmd" in
  backup)
    token="$(date +%s)-${RANDOM}"
    remote_backup="$RUNDIR/manual-backup-${token}.bin"
    cleanup_remote_backup() {
      "${SSH[@]}" "rm -f -- '$remote_backup'" >/dev/null 2>&1 || true
    }
    trap cleanup_remote_backup EXIT
    trap 'exit 130' HUP INT TERM
    backup_rc=0
    guarded_read backup stock "$remote_backup" || backup_rc=$?
    if [[ "$backup_rc" != 0 ]]; then
      echo "remote backup failed (exit $backup_rc)" >&2
      exit "$backup_rc"
    fi
    local_backup="backups/bkb-backup-${token}.bin"
    copy_rc=0
    "${SCP[@]}" "root@${MINER}:${remote_backup}" "$local_backup" >/dev/null || copy_rc=$?
    [[ "$copy_rc" == 0 && "$(stat -c%s "$local_backup" 2>/dev/null || echo 0)" == 1048576 ]] || {
      rm -f -- "$local_backup"
      echo 'backup transfer failed or was incomplete' >&2
      exit 1
    }
    cleanup_remote_backup
    trap - EXIT HUP INT TERM
    echo "backup pulled -> $local_backup"; md5sum "$local_backup"
    ;;
  verify)
    check_images "$arg"
    wait_job verify "$(want_md5 "$arg")"
    ;;
  flash)
    check_images "$arg"
    if [[ "$flag" == --confirm ]]; then
      [[ "$arg" != custom ]] || "${SSH[@]}" 'test -f /opt/scripta/firmware/ENCODER_READY'
      wait_job "$([[ "$arg" == custom ]] && echo flash-custom || echo revert)"
      pull_preflash_backup || true
    else
      echo "VALIDATION ONLY: local and staged $arg images match; add --confirm for the guarded write job."
    fi
    ;;
  rollback)
    check_images stock
    if [[ "$arg" == --confirm ]]; then
      wait_job revert
      pull_preflash_backup || true
    else
      echo 'VALIDATION ONLY: local and staged stock images match; add --confirm for the guarded revert job.'
    fi
    ;;
  *) usage; exit 1;;
esac
