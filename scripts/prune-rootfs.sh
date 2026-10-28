#!/usr/bin/env bash
# Remove superseded factory files and private measurement state from an image.
set -euo pipefail

ROOTFS=$(realpath -e "${1:?rootfs directory required}")
if [[ "$ROOTFS" == / || ! -d "$ROOTFS/etc" || -L "$ROOTFS/etc" ||
      ! -d "$ROOTFS/opt" || -L "$ROOTFS/opt" ||
      ! -d "$ROOTFS/opt/scripta" || -L "$ROOTFS/opt/scripta" ]]; then
    echo "refusing cleanup outside a staged Scripta rootfs: $ROOTFS" >&2
    exit 1
fi

assert_safe_parent() {
    local relative=$1 parent_relative component cursor
    [[ "$relative" != /* && "$relative" != *$'\n'* ]] || {
        echo "refusing unsafe staged path: $relative" >&2
        exit 1
    }
    parent_relative=${relative%/*}
    [[ "$parent_relative" != "$relative" ]] || parent_relative=.
    cursor=$ROOTFS
    IFS='/' read -r -a components <<< "$parent_relative"
    for component in "${components[@]}"; do
        [[ -z "$component" || "$component" == . ]] && continue
        [[ "$component" != .. ]] || {
            echo "refusing parent traversal in staged path: $relative" >&2
            exit 1
        }
        cursor="$cursor/$component"
        if [[ -L "$cursor" ]]; then
            echo "refusing cleanup through a symlinked parent: $relative" >&2
            exit 1
        fi
        if [[ -e "$cursor" && ! -d "$cursor" ]]; then
            echo "refusing cleanup through a non-directory parent: $relative" >&2
            exit 1
        fi
    done
}

DELETE_PATHS=(
    "etc/dhcp/dhclient-exit-hooks.d/ntp"
    "etc/dhcp/dhclient-exit-hooks.d/ntpdate"
    "etc/network/if-up.d/ntpdate"
    "etc/shellinabox/options-enabled/00+Black on White.css"
    "etc/rc2.d/S01shellinabox"
    "etc/rc3.d/S01shellinabox"
    "etc/rc4.d/S01shellinabox"
    "etc/rc5.d/S01shellinabox"
    "etc/cron.daily/samba"
    "etc/dhcp/dhclient-enter-hooks.d/samba"
    "etc/lighttpd/certs/lighttpd.pem"
    "var/lib/systemd/random-seed"
    "var/lib/urandom/random-seed"
    "var/lib/blakestream-identity"
    "var/lib/blakestream-firstboot"
    "var/lib/blakestream-backups"
    "run/blakestream-miner"
    "run/blakestream-control"
    "run/blakestream-firmware"
    "run/blakestream-security"
    "run/blakestream-backup"
    "run/blakestream-web"
    "var/lib/dhcp/dhclient.wlan0.leases"
    "var/lib/shellinabox/certificate.pem"
    "var/mail/root"
    "var/mail/www-data"
    "var/www/f_graph.php"
    "var/www/f_graphReset.php"
    "var/www/f_hostHardCtl.php"
    "var/www/f_minerHardCtl.php"
    "var/www/f_minercompat.php"
    "var/www/inc/host.inc.php"
    "var/www/miner.php"
    "var/www/info.php"
    "var/www/test.php"
    "var/www/index.html"
    "var/www/index.lighttpd.html"
    "var/www/lighttpd.pem"
    "var/www/css/shellinabox.css"
    "var/www/rrd"
    "opt/scripta/etc/cron.d/5min/hashrate"
    "opt/scripta/etc/cron.d/5min/ALERThashrate"
    "opt/scripta/bin/set-timezone.sh"
    "opt/scripta/etc/miner.conf"
    "usr/bin/update_fw.py"
    "root/.bash_history"
    "root/.viminfo"
    "home/baikal/.bash_history"
    "home/baikal/.viminfo"
    "home/baikal/.vim/.netrwhist"
    "home/baikal/.rnd"
    "opt/scripta/etc/network-restore.json"
    "opt/scripta/etc/bkb-firmware-runtime.json"
    "opt/scripta/etc/bkb-firmware-firstboot.json"
    "opt/scripta/etc/bkb-firmware-firstboot.pending"
    "opt/scripta/etc/bkb-firmware-identity.json"
    "run/blakestream/bkb-firmware-identity.json"
    "opt/scripta/var/firmware-firstboot.log"
    "opt/scripta/var/security.log"
    "var/log/blakestream-security"
    "var/log/blakestream-miner"
    "var/log/blakestream-firstboot"
    "opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY"
    "opt/scripta/firmware/BRIDGE_GLOBAL_V11_READY"
    "run/blakestream/backup-upload.bin"
    "run/blakestream/backup-export.zip"
    "var/log/lighttpd/error.log"
)
for path in "${DELETE_PATHS[@]}"; do
    target="$ROOTFS/$path"
    if [[ -e "$target" || -L "$target" ]]; then
        assert_safe_parent "$path"
        if [[ -d "$target" && ! -L "$target" ]]; then
            rm -rf -- "$target"
        else
            rm -f -- "$target"
        fi
        printf 'removed: %s\n' "$path"
    fi
done

# Disable inherited network daemons that are not part of the documented
# management surface. Canonical systemd units are masked separately by
# prepare-image-startup.py; removing SysV start links closes generator/fallback
# paths while retaining package files for compatibility.
for init_dir in "$ROOTFS"/etc/rc[2-5].d; do
    [[ -d "$init_dir" && ! -L "$init_dir" ]] || continue
    find -P "$init_dir" -maxdepth 1 -type l \
        \( -name 'S??shellinabox' -o -name 'S??dnsmasq' \
        -o -name 'S??smbd' -o -name 'S??nmbd' -o -name 'S??samba-ad-dc' \
        -o -name 'S??rsync' -o -name 'S??sendmail' -o -name 'S??ntp' \
        -o -name 'S??avahi-daemon' \) -printf 'removed: %P\n' -delete
done

# Generic images must never contain cloned SSH server identities. The first
# boot identity service waits for real device entropy and regenerates every
# key type required by sshd_config.
ssh_dir="$ROOTFS/etc/ssh"
assert_safe_parent etc/ssh/host-key
if [[ -L "$ssh_dir" ]]; then
    echo "refusing SSH identity cleanup through a symlink" >&2
    exit 1
fi
if [[ -d "$ssh_dir" ]]; then
    find -P "$ssh_dir" -maxdepth 1 \( -type f -o -type l \) -name 'ssh_host_*' \
        -printf 'removed: etc/ssh/%f\n' -delete
fi

# systemd 229 generates a machine ID from an empty /etc/machine-id. Remove the
# cloned D-Bus fallback first, then point D-Bus at the newly generated ID.
machine_id="$ROOTFS/etc/machine-id"
assert_safe_parent etc/machine-id
if [[ -L "$machine_id" ]]; then
    echo "refusing symlinked machine-id cleanup" >&2
    exit 1
fi
: > "$machine_id"
if [[ $EUID -eq 0 ]]; then
    chown 0:0 "$machine_id"
fi
chmod 0444 "$machine_id"
dbus_dir="$ROOTFS/var/lib/dbus"
assert_safe_parent var/lib/dbus/machine-id
if [[ -L "$dbus_dir" ]]; then
    echo "refusing D-Bus machine-id cleanup through a symlink" >&2
    exit 1
fi
mkdir -p "$dbus_dir"
dbus_machine_id="$dbus_dir/machine-id"
if [[ -e "$dbus_machine_id" || -L "$dbus_machine_id" ]]; then
    rm -f -- "$dbus_machine_id"
fi
ln -s ../../../etc/machine-id "$dbus_machine_id"

# Leases, generated dhclient fragments, and timestamps belong to the factory
# installation, not to every flashed device.
nm_state="$ROOTFS/var/lib/NetworkManager"
assert_safe_parent var/lib/NetworkManager/state
if [[ -L "$nm_state" ]]; then
    echo "refusing NetworkManager state cleanup through a symlink" >&2
    exit 1
fi
if [[ -d "$nm_state" ]]; then
    find -P "$nm_state" -maxdepth 1 -type f \
        \( -name 'dhclient-*-eth0.lease' -o -name 'dhclient-eth0.conf*' \
        -o -name 'timestamps*' \) -printf '%p\n' -delete
fi

# Factory/runtime temporary state is not installation-neutral. Keep the
# standard sticky directories themselves, but never ship their contents.
# -P and -xdev ensure an inherited link or mount cannot redirect cleanup.
for path in tmp var/tmp; do
    target="$ROOTFS/$path"
    assert_safe_parent "$path/placeholder"
    if [[ -L "$target" ]] || [[ -e "$target" && ! -d "$target" ]]; then
        echo "refusing temporary-state cleanup through a non-directory: $path" >&2
        exit 1
    fi
    mkdir -p "$target"
    find -P "$target" -xdev -mindepth 1 -delete
    if [[ $EUID -eq 0 ]]; then
        chown 0:0 "$target"
    fi
    chmod 1777 "$target"
done

PY_BIN=${BKB_PYTHON:-python3}
"${PY_BIN}" "$(dirname "$0")/prepare-image-network.py" "$ROOTFS"
"${PY_BIN}" "$(dirname "$0")/prepare-image-startup.py" "$ROOTFS"
"${PY_BIN}" "$(dirname "$0")/prepare-image-wifi.py" --rootfs "$ROOTFS"

# Personal archives and per-installation baselines must never ship in the image.
for path in \
    opt/scripta/etc/backup var/lib/blakestream-clock-baselines var/lib/blakestream-wifi \
    root/.sgminer etc/mail/tls var/lib/samba var/cache/samba var/log/samba \
    opt/scripta/http/rrd opt/scripta/var/rrd opt/scripta/backups
do
    target="$ROOTFS/$path"
    [[ -e "$target" || -L "$target" ]] || continue
    assert_safe_parent "$path"
    if [[ -L "$target" || "$(realpath -e "$target")" != "$ROOTFS/"* ]]; then
        echo "refusing private backup cleanup through a symlink: $path" >&2
        exit 1
    fi
    rm -rf -- "$target"
done

# Preserve log-file ownership/modes expected by old tools while removing all
# factory login and daemon history from the release image.
# /var/run is the distribution's normal ../run symlink. Address the on-disk
# file through its canonical staged parent so the symlink-safety guard remains
# strict without rejecting an unmodified factory rootfs.
for path in var/log/wtmp var/log/btmp var/log/lastlog var/log/faillog run/utmp; do
    target="$ROOTFS/$path"
    [[ -e "$target" || -L "$target" ]] || continue
    assert_safe_parent "$path"
    if [[ -L "$target" || ! -f "$target" ]]; then
        echo "refusing login-log cleanup through a non-regular path: $path" >&2
        exit 1
    fi
    truncate -s 0 "$target"
done
log_dir="$ROOTFS/var/log"
assert_safe_parent var/log/placeholder
if [[ -L "$log_dir" ]]; then
    echo "refusing log cleanup through a symlink" >&2
    exit 1
fi
if [[ -d "$log_dir" ]]; then
    find -P "$log_dir" -maxdepth 1 -type f \
        \( -name '*.log' -o -name 'syslog*' -o -name 'messages*' \
        -o -name 'daemon.log*' -o -name 'kern.log*' -o -name 'auth.log*' \
        -o -name 'user.log*' -o -name 'mail.log*' -o -name 'mail.err*' \
        -o -name 'mail.info*' -o -name 'mail.warn*' \) \
        -exec truncate -s 0 {} +
fi

# A release image must start with fresh installation identities and no history.
# Match only this recorder's namespace inside the staged filesystem.
for dir in opt/scripta/var run/blakestream; do
    assert_safe_parent "$dir/placeholder"
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

# Development-host bytecode must not leak into a release image. Limit this to
# application trees; distribution Python caches under /usr are left intact.
for dir in opt/scripta var/www; do
    target="$ROOTFS/$dir"
    assert_safe_parent "$dir/placeholder"
    if [[ -L "$target" ]]; then
        echo "refusing bytecode cleanup through a symlink: $dir" >&2
        exit 1
    fi
    [[ -d "$target" ]] || continue
    find "$target" -xdev -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -delete
    find "$target" -xdev -depth -type d -name __pycache__ -empty -print -delete
done
