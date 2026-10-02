#!/usr/bin/env bash
# Remove superseded factory files and private measurement state from an image.
set -euo pipefail

ROOTFS=$(realpath -e "${1:?rootfs directory required}")
if [[ "$ROOTFS" == / || ! -d "$ROOTFS/etc" || ! -d "$ROOTFS/opt/scripta" ]]; then
    echo "refusing cleanup outside a staged Scripta rootfs: $ROOTFS" >&2
    exit 1
fi

DELETE_PATHS=(
    "etc/dhcp/dhclient-exit-hooks.d/ntp"
    "etc/dhcp/dhclient-exit-hooks.d/ntpdate"
    "etc/network/if-up.d/ntpdate"
    "etc/shellinabox/options-enabled/00+Black on White.css"
    "var/www/f_graph.php"
    "var/www/f_graphReset.php"
    "opt/scripta/etc/cron.d/5min/hashrate"
    "opt/scripta/etc/cron.d/5min/ALERThashrate"
    "opt/scripta/bin/set-timezone.sh"
    "opt/scripta/etc/miner.conf"
    "opt/scripta/etc/network-restore.json"
    "opt/scripta/etc/bkb-firmware-runtime.json"
    "opt/scripta/etc/bkb-firmware-firstboot.json"
    "opt/scripta/etc/bkb-firmware-identity.json"
    "run/blakestream/bkb-firmware-identity.json"
    "opt/scripta/var/firmware-firstboot.log"
    "opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY"
    "opt/scripta/firmware/BRIDGE_GLOBAL_V11_READY"
    "run/blakestream/backup-upload.bin"
    "run/blakestream/backup-export.zip"
    "var/log/lighttpd/error.log"
)
for path in "${DELETE_PATHS[@]}"; do
    target="$ROOTFS/$path"
    if [[ -e "$target" || -L "$target" ]]; then
        rm -f -- "$target"
        printf 'removed: %s\n' "$path"
    fi
done

PY_BIN=${BKB_PYTHON:-python3}
"${PY_BIN}" "$(dirname "$0")/prepare-image-network.py" "$ROOTFS"
"${PY_BIN}" "$(dirname "$0")/prepare-image-startup.py" "$ROOTFS"
"${PY_BIN}" "$(dirname "$0")/prepare-image-wifi.py" --rootfs "$ROOTFS"

# Personal archives and per-installation baselines must never ship in the image.
for path in opt/scripta/etc/backup var/lib/blakestream-clock-baselines var/lib/blakestream-wifi; do
    target="$ROOTFS/$path"
    [[ -e "$target" || -L "$target" ]] || continue
    if [[ -L "$target" || "$(realpath -e "$target")" != "$ROOTFS/"* ]]; then
        echo "refusing private backup cleanup through a symlink: $path" >&2
        exit 1
    fi
    rm -rf -- "$target"
done

# A release image must start with fresh installation identities and no history.
# Match only this recorder's namespace inside the staged filesystem.
for dir in opt/scripta/var run/blakestream; do
    if [[ -L "$ROOTFS/$dir" ]]; then
        echo "refusing measured-rate cleanup through a symlink: $dir" >&2
        exit 1
    fi
    [[ -d "$ROOTFS/$dir" ]] || continue
    find "$ROOTFS/$dir" -maxdepth 1 -type f \
        \( -name 'measured-rates-*.json' -o -name 'measured-rates-*.json.tmp' \
        -o -name 'measured-rates-*.sqlite' -o -name 'measured-rates-*.sqlite-*' \
        -o -name 'measured-rates*.lock' -o -name 'accepted-work-rates.json' \
        -o -name 'network-events.log' -o -name 'system-events.log' \
        -o -name 'cooling-events.log' -o -name 'lighttpd-error.log' \
        -o -name 'diagnostic-log-state.json' -o -name 'diagnostic-logs.lock' \) \
        -print -delete
done
