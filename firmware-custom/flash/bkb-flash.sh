#!/usr/bin/env bash
# Local driver for the reversible DFU flash of the BK-B STM32 bridge.
# Copies the on-miner flasher + the target image to the miner, runs it, and pulls
# the pre-flash backup back to this repo. Writes ONLY with --confirm.
#
# Usage:
#   ./bkb-flash.sh backup                 # dump current chip -> backups/ (read-only)
#   ./bkb-flash.sh verify <custom|stock>  # read-back compare chip vs an image (read-only)
#   ./bkb-flash.sh flash  custom          # backup -> dry-run (no write) unless --confirm
#   ./bkb-flash.sh flash  custom --confirm
#   ./bkb-flash.sh rollback               # re-flash the STOCK image (needs --confirm to write)
#
# "Double reversible":
#   layer 1 = pre-flash backup taken live by dfu_flash.py at flash time (backups/)
#   layer 2 = the verified stock image in ../../firmware-dump/ (md5 d5f844be...)
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == --help || "${1:-}" == -h || $# == 0 ]]; then
  sed -n '2,12p' "$0"
  echo 'Set MINER to an SSH hostname/address; configure SSH authentication yourself.'
  echo 'Backup, verify, and dry-run operations also pause mining to enter DFU.'
  exit 0
fi
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

cmd="${1:-}"; arg="${2:-}"; flag="${3:-}"

img_path() { case "$1" in custom) echo "$CUSTOM";; stock) echo "$STOCK";; *) echo "";; esac; }
want_md5() { case "$1" in custom) echo "$CUSTOM_MD5";; stock) echo "$STOCK_MD5";; *) echo "";; esac; }

push_flasher() { "${SCP[@]}" dfu_flash.py root@${MINER}:/tmp/dfu_flash.py >/dev/null; }

# Pull the pre-flash backup off the miner's persistent store and validate its
# size, so the layer-1 copy is preserved on the PC and a short/missing pull is
# loud rather than silently swallowed.
pull_preflash_backup() {
  local ts dst sz
  ts=$(date +%Y%m%d-%H%M%S)
  dst="backups/preflash-${ts}.bin"
  if "${SCP[@]}" root@${MINER}:/opt/scripta/backups/bkb-preflash-backup.bin "$dst" >/dev/null 2>&1; then
    sz=$(stat -c%s "$dst" 2>/dev/null || echo 0)
    if [ "$sz" = "1048576" ]; then
      echo "pre-flash backup -> $dst"; md5sum "$dst"
    else
      echo "WARN: pulled pre-flash backup is $sz bytes (expected 1048576) — kept $dst, treat with care"
    fi
  else
    echo "WARN: could not pull pre-flash backup (/opt/scripta/backups/bkb-preflash-backup.bin)"
  fi
}

case "$cmd" in
  backup)
    ts=$(date +%Y%m%d-%H%M%S)
    push_flasher
    "${SSH[@]}" "python /tmp/dfu_flash.py backup /tmp/bkb-backup-${ts}.bin"
    "${SCP[@]}" root@${MINER}:/tmp/bkb-backup-${ts}.bin backups/ >/dev/null
    echo "backup pulled -> backups/bkb-backup-${ts}.bin"; md5sum backups/bkb-backup-${ts}.bin
    ;;
  verify)
    img=$(img_path "$arg"); [ -z "$img" ] && { echo "verify custom|stock"; exit 1; }
    want=$(want_md5 "$arg")
    echo "local $arg md5: $(md5sum "$img" | cut -d' ' -f1) (want ${arg^^}=$want)"
    [ "$(md5sum "$img" | cut -d' ' -f1)" = "$want" ] || { echo "ABORT: $arg md5 mismatch locally"; exit 1; }
    push_flasher
    "${SCP[@]}" "$img" root@${MINER}:/tmp/verify-img.bin >/dev/null
    "${SSH[@]}" "python /tmp/dfu_flash.py verify /tmp/verify-img.bin --md5 $want"
    ;;
  flash)
    img=$(img_path "$arg"); [ -z "$img" ] && { echo "flash custom|stock [--confirm]"; exit 1; }
    want=$(want_md5 "$arg")
    [ "$(md5sum "$img" | cut -d' ' -f1)" = "$want" ] || { echo "ABORT: $arg md5 mismatch locally"; exit 1; }
    if [ "$arg" = custom ]; then
      "${SSH[@]}" 'test -f /opt/scripta/firmware/ENCODER_READY' || { echo 'ABORT: matching encoder readiness marker missing' >&2; exit 1; }
    fi
    push_flasher
    "${SCP[@]}" "$img" root@${MINER}:/tmp/flash-img.bin >/dev/null
    if [ "$flag" = "--confirm" ]; then
      echo ">>> WRITING $arg to the BK-B bridge (reversible; validated pre-flash backup taken first) <<<"
      if [ "$arg" = "custom" ]; then
        "${SSH[@]}" "rm -f /opt/scripta/firmware/BRIDGE_GLOBAL_V11_READY /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY;
          if BKB_DEFER_MINER_RESTART=1 python /tmp/dfu_flash.py flash /tmp/flash-img.bin --md5 $want --confirm; then
            printf 'bridge_firmware=CUSTOM V1\nchip_md5=$CUSTOM_MD5\n' > /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY;
            chown root:root /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY;
            chmod 0644 /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY;
            now=\$(date +%s);
            printf '{\"chip_md5\":\"$CUSTOM_MD5\",\"identity\":\"CUSTOM V1\",\"at\":%s}\n' \"\$now\" > /opt/scripta/etc/bkb-firmware-identity.json;
            chown root:www-data /opt/scripta/etc/bkb-firmware-identity.json;
            chmod 0664 /opt/scripta/etc/bkb-firmware-identity.json;
            BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh;
          else
            rc=\$?;
            BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh || true;
            exit \$rc;
          fi"
      else
        "${SSH[@]}" "rm -f /opt/scripta/firmware/BRIDGE_GLOBAL_V11_READY /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY;
          if BKB_DEFER_MINER_RESTART=1 python /tmp/dfu_flash.py flash /tmp/flash-img.bin --md5 $want --confirm; then
            BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh;
          else
            rc=\$?;
            BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh || true;
            exit \$rc;
          fi"
      fi
    else
      echo ">>> DRY RUN: validate + pre-flash backup, NOT writing (add --confirm to write) <<<"
      "${SSH[@]}" "python /tmp/dfu_flash.py flash /tmp/flash-img.bin --md5 $want"
    fi
    pull_preflash_backup
    ;;
  rollback)
    [ "$(md5sum "$STOCK" | cut -d' ' -f1)" = "$STOCK_MD5" ] || { echo "ABORT: stock image md5 mismatch locally"; exit 1; }
    push_flasher
    "${SCP[@]}" "$STOCK" root@${MINER}:/tmp/stock-img.bin >/dev/null
    if [ "$arg" = "--confirm" ]; then
      echo ">>> ROLLBACK: writing STOCK image to restore the bridge <<<"
        "${SSH[@]}" "rm -f /opt/scripta/firmware/BRIDGE_GLOBAL_V11_READY /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY;
          if BKB_DEFER_MINER_RESTART=1 python /tmp/dfu_flash.py flash /tmp/stock-img.bin --md5 $STOCK_MD5 --confirm; then
            now=\$(date +%s);
            printf '{\"chip_md5\":\"$STOCK_MD5\",\"identity\":\"STOCK\",\"at\":%s}\n' \"\$now\" > /opt/scripta/etc/bkb-firmware-identity.json;
            chown root:www-data /opt/scripta/etc/bkb-firmware-identity.json;
            chmod 0664 /opt/scripta/etc/bkb-firmware-identity.json;
            BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh;
        else
          rc=\$?;
          BS_FIRMWARE_RESTART=1 /opt/scripta/startup/miner-restart.sh || true;
          exit \$rc;
        fi"
    else
      echo ">>> DRY RUN rollback (add --confirm to write stock) <<<"
      "${SSH[@]}" "python /tmp/dfu_flash.py flash /tmp/stock-img.bin --md5 $STOCK_MD5"
    fi
    pull_preflash_backup
    ;;
  *)
    sed -n '2,20p' "$0"; exit 1;;
esac
