#!/usr/bin/env bash
# 04-apply-overlay.sh — rsync the overlay/ tree into firmware-modified/rootfs/.
# The overlay mirrors rootfs paths and only contains files we want to add or replace.
# We deliberately do NOT use --delete: the overlay is additive.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="${HERE}/overlay"
ROOTFS="${HERE}/firmware-modified/rootfs"

if [[ ! -d "${ROOTFS}" ]]; then
    echo "[04-apply-overlay] ERROR: ${ROOTFS} missing. Run 03-snapshot.sh first." >&2
    exit 1
fi

if [[ ! -d "${OVERLAY}" ]]; then
    echo "[04-apply-overlay] ERROR: ${OVERLAY} missing." >&2
    exit 1
fi

echo "[04-apply-overlay] Applying ${OVERLAY}/ -> ${ROOTFS}/"
rsync -aHAX --numeric-ids --info=progress2 --human-readable "${OVERLAY}/" "${ROOTFS}/"

# ----------------------------------------------------------------------------
# Cleanup: files we want REMOVED from the rootfs that the additive overlay
# can't delete on its own. List of paths relative to ROOTFS.
# ----------------------------------------------------------------------------
DELETE_PATHS=(
    # Shellinabox: factory default was "Black on White" (the leading + makes
    # it default). We replace it with "White On Black" via the overlay above.
    # Remove the old default symlink so the new one wins.
    "etc/shellinabox/options-enabled/00+Black on White.css"
    # v2.x: per-board hashrate replaces the factory aggregate RRDtool graphs.
    # The new pipeline is hashrate-history-recorder.py (cron) +
    # f_blakestream.php?action=load_hashrate_history + bsHashrateChart directive.
    # Remove the factory graph PHP renderers and the 5-min PHP writer so they
    # can't accidentally clobber state. Their cron lines are already commented
    # out in overlay/etc/cron.d/scripta but the PHP files would still answer
    # /f_graph.php directly if a stray bookmark hits them.
    "var/www/f_graph.php"
    "var/www/f_graphReset.php"
    "opt/scripta/etc/cron.d/5min/hashrate"
    "opt/scripta/etc/cron.d/5min/ALERThashrate"
)
echo "[04-apply-overlay] Cleanup: removing legacy paths from rootfs"
for p in "${DELETE_PATHS[@]}"; do
    target="${ROOTFS}/${p}"
    if [[ -e "${target}" || -L "${target}" ]]; then
        rm -f "${target}"
        echo "  removed: ${p}"
    fi
done

echo "[04-apply-overlay] Done. Files in this overlay:"
( cd "${OVERLAY}" && find . -type f -o -type l ) | sort
