#!/usr/bin/env bash
# dev-sync.sh — Live development workflow for a real Baikal BK-B on the network.
#
# Workflow:
#   1. Flash a "dev seed" SD card with the original PiZero_GB_180105_V1.0.img once
#      (or with our build/Blakestream-BKB-v2.0.img after it exists)
#   2. Boot the BK-B, find its IP (e.g. via DHCP / arp / Scripta default mDNS)
#   3. Set up SSH key once: ssh-copy-id root@<baikal-ip>
#   4. Edit files in firmware-modified/rootfs/ or overlay/ on this dev machine
#   5. Run: ./scripts/dev-sync.sh <baikal-ip> [path-or-service]
#      - rsyncs the relevant changes
#      - restarts lighttpd / cgminer / scripta as needed
#      - refresh browser to see changes — no flash required
#
# Usage:
#   ./scripts/dev-sync.sh 192.168.1.50                    # sync overlay/ + restart everything
#   ./scripts/dev-sync.sh 192.168.1.50 web                # sync only /var/www + restart lighttpd
#   ./scripts/dev-sync.sh 192.168.1.50 sgminer            # sync sgminer config + restart cgminer
#   ./scripts/dev-sync.sh 192.168.1.50 full               # sync the entire firmware-modified/rootfs/ (slow, careful)

set -euo pipefail

BAIKAL_IP="${1:-}"
MODE="${2:-overlay}"

if [[ -z "${BAIKAL_IP}" ]]; then
    echo "Usage: $0 <baikal-ip> [overlay|web|sgminer|full]" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${HERE}/.ssh-known_hosts"

ssh_run() {
    ssh ${SSH_OPTS} "root@${BAIKAL_IP}" "$@"
}

case "${MODE}" in
    overlay)
        echo "[dev-sync] Syncing overlay/ -> root@${BAIKAL_IP}:/"
        rsync -avz --rsh="ssh ${SSH_OPTS}" "${HERE}/overlay/" "root@${BAIKAL_IP}:/"
        echo "[dev-sync] Restarting lighttpd + cgminer ..."
        ssh_run "systemctl restart lighttpd; systemctl restart cgminer 2>/dev/null || systemctl restart sgminer 2>/dev/null || true"
        ;;
    web)
        echo "[dev-sync] Syncing only web assets -> /var/www/"
        rsync -avz --rsh="ssh ${SSH_OPTS}" "${HERE}/firmware-modified/rootfs/var/www/" "root@${BAIKAL_IP}:/var/www/"
        echo "[dev-sync] Restarting lighttpd ..."
        ssh_run "systemctl restart lighttpd"
        ;;
    sgminer)
        echo "[dev-sync] Syncing sgminer config files -> /opt/scripta/etc/ and /etc/"
        rsync -avz --rsh="ssh ${SSH_OPTS}" "${HERE}/firmware-modified/rootfs/opt/scripta/etc/" "root@${BAIKAL_IP}:/opt/scripta/etc/"
        ssh_run "systemctl restart cgminer 2>/dev/null || systemctl restart sgminer 2>/dev/null || true"
        ;;
    full)
        echo "[dev-sync] FULL rsync of modified rootfs -> / (this is risky and slow!)"
        echo "[dev-sync] Excluding /dev /proc /sys /run /tmp /lib/modules /boot to avoid bricking"
        read -r -p "Continue? [y/N] " ans
        [[ "${ans}" =~ ^[Yy]$ ]] || exit 0
        rsync -avz --rsh="ssh ${SSH_OPTS}" \
            --exclude='/dev/' --exclude='/proc/' --exclude='/sys/' --exclude='/run/' --exclude='/tmp/' \
            --exclude='/lib/modules/' --exclude='/boot/' \
            "${HERE}/firmware-modified/rootfs/" "root@${BAIKAL_IP}:/"
        ;;
    *)
        echo "Unknown mode: ${MODE}. Use overlay|web|sgminer|full" >&2
        exit 1
        ;;
esac

echo "[dev-sync] Done. Refresh http://${BAIKAL_IP}/ in your browser."
