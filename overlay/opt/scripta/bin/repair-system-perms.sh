#!/bin/sh
# Repair root-owned helper permissions that must survive firmware extraction,
# SD-card metadata drift, and manual maintenance.

set -eu

changed=0

note_change() {
    changed=1
}

set_owner_mode() {
    path="$1"
    owner="$2"
    mode="$3"

    [ -e "$path" ] || return 0
    [ ! -L "$path" ] || return 0

    current_owner="$(stat -c '%U:%G' "$path" 2>/dev/null || true)"
    current_mode="$(stat -c '%a' "$path" 2>/dev/null || true)"

    if [ "$current_owner" != "$owner" ]; then
        chown "$owner" "$path"
        note_change
    fi
    if [ "$current_mode" != "$mode" ]; then
        chmod "$mode" "$path"
        note_change
    fi
}

require_root_sealed_dir() {
    path="$1"
    [ -d "$path" ] && [ ! -L "$path" ] || {
        echo "unsafe privileged helper directory: $path" >&2
        exit 1
    }
    uid="$(stat -c '%u' "$path" 2>/dev/null || echo unsafe)"
    path_mode="$(stat -c '%a' "$path" 2>/dev/null || echo unsafe)"
    case "$path_mode" in *[!0-7]*|'') echo "unreadable privileged helper directory: $path" >&2; exit 1;; esac
    [ "$uid" = 0 ] && [ $((0$path_mode & 0022)) -eq 0 ] || {
        echo "privileged helper directory is not root-sealed: $path" >&2
        exit 1
    }
}

write_www_sudo_policy() {
    cat <<'EOF'
# Blakestream-GaintB: allow lighttpd's www-data user to run only the narrow
# root helpers needed by the web UI and recorder runtime setup.
#
# Scope: explicit commands below only. Nothing else.
# Blakestream sudo policy v2: empty quotes mean no command-line arguments.

www-data ALL=(root) NOPASSWD: /opt/scripta/startup/miner-start.sh ""
www-data ALL=(root) NOPASSWD: /opt/scripta/startup/miner-restart.sh ""
www-data ALL=(root) NOPASSWD: /opt/scripta/startup/miner-restart.sh --preflight
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/prepare-runtime-dir.sh ""
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/bkb-firmware.sh start-job verify, /opt/scripta/bin/bkb-firmware.sh start-job revert, /opt/scripta/bin/bkb-firmware.sh start-job flash-custom, /opt/scripta/bin/bkb-firmware.sh abort-job
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/network-settings.py status, /opt/scripta/bin/network-settings.py apply, /opt/scripta/bin/network-settings.py confirm, /opt/scripta/bin/network-settings.py rollback
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/network-settings.py pending
EOF
}

write_wifi_sudo_policy() {
    cat <<'EOF'
# Blakestream-GaintB: www-data may run only these Wi-Fi helper verbs.
# Passwords and other fields travel on stdin. Worker/boot verbs are omitted.

www-data ALL=(root) NOPASSWD: /opt/scripta/bin/wifi-settings.py status, /opt/scripta/bin/wifi-settings.py pending, /opt/scripta/bin/wifi-settings.py scan, /opt/scripta/bin/wifi-settings.py connect, /opt/scripta/bin/wifi-settings.py disconnect, /opt/scripta/bin/wifi-settings.py forget, /opt/scripta/bin/wifi-settings.py radio
EOF
}

write_backup_sudo_policy() {
    cat <<'EOF'
# Fixed backup verbs; validated names and data travel on stdin, never shell args.
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/backup-settings.py snapshot, /opt/scripta/bin/backup-settings.py restore, /opt/scripta/bin/backup-settings.py list, /opt/scripta/bin/backup-settings.py export, /opt/scripta/bin/backup-settings.py import, /opt/scripta/bin/backup-settings.py finish, /opt/scripta/bin/backup-settings.py rename, /opt/scripta/bin/backup-settings.py delete
EOF
}

install_sudo_policy() {
    file="$1"
    writer="$2"
    tmp="$(mktemp "$file.tmp.XXXXXX")"
    "$writer" > "$tmp"
    chown root:root "$tmp"
    chmod 0440 "$tmp"

    if command -v visudo >/dev/null 2>&1; then
        visudo -cf "$tmp" >/dev/null 2>&1 || {
            rm -f "$tmp"
            return 1
        }
    fi

    if [ -f "$file" ] && [ ! -L "$file" ] && cmp -s "$tmp" "$file"; then
        rm -f -- "$tmp"
        return 0
    fi
    mv -f -- "$tmp" "$file"
    note_change
}

ensure_blakestream_sudoers() {
    install_sudo_policy /etc/sudoers.d/blakestream-www write_www_sudo_policy
    install_sudo_policy /etc/sudoers.d/blakestream-wifi write_wifi_sudo_policy
    install_sudo_policy /etc/sudoers.d/blakestream-backup write_backup_sudo_policy
}

if [ -f /etc/sudoers ] && [ ! -L /etc/sudoers ]; then
    set_owner_mode /etc/sudoers root:root 440
fi

if [ -d /etc/sudoers.d ] && [ ! -L /etc/sudoers.d ]; then
    set_owner_mode /etc/sudoers.d root:root 750
fi

if [ -d /etc/cron.d ] && [ ! -L /etc/cron.d ]; then
    set_owner_mode /etc/cron.d root:root 755
fi
set_owner_mode /etc/cron.d/scripta root:root 644
set_owner_mode /etc/cron.d/sendmail root:root 644
for cron_file in /etc/cron.d/blakestream-*; do
    [ -e "$cron_file" ] || continue
    set_owner_mode "$cron_file" root:root 644
done

# Older overlays repaired the factory sendmail queue shortly before its cron
# retry. Alerts use direct SMTP, so that retry and repair cron are obsolete.
if [ -e /etc/cron.d/blakestream-sendmail-spool ]; then
    rm -f /etc/cron.d/blakestream-sendmail-spool
    note_change
fi

ensure_blakestream_sudoers
set_owner_mode /etc/sudoers.d/blakestream-www root:root 440
set_owner_mode /etc/sudoers.d/blakestream-wifi root:root 440
set_owner_mode /etc/sudoers.d/blakestream-backup root:root 440

mkdir -p /tmp 2>/dev/null || true
set_owner_mode /tmp root:root 1777

# Repair the shared directory without deleting any live session sockets.
if [ -x /usr/bin/screen ]; then
    mkdir -p /run/screen
    screen_mode=1777
    if [ -u /usr/bin/screen ]; then
        screen_mode=755
    elif [ -g /usr/bin/screen ]; then
        screen_mode=775
    fi
    set_owner_mode /run/screen root:utmp "$screen_mode"
fi

mkdir -p /run/lighttpd /var/run/sshd 2>/dev/null || true
set_owner_mode /run/lighttpd www-data:www-data 755
set_owner_mode /var/run/sshd root:root 755

# Do not path-chown files in /opt/scripta/etc: www-data legitimately owns write
# access to that parent and could swap a checked entry before chown/chmod. Image
# packaging and the PHP password writer set uipasswd without privilege. Root
# outputs are prepared only below sealed parents.
require_root_sealed_dir /opt
require_root_sealed_dir /opt/scripta
require_root_sealed_dir /opt/scripta/bin
set_owner_mode /opt/scripta/bin/prepare-runtime-dir.sh root:root 755
[ -f /opt/scripta/bin/prepare-runtime-dir.sh ] && [ ! -L /opt/scripta/bin/prepare-runtime-dir.sh ] || {
    echo "secure runtime helper is unavailable" >&2
    exit 1
}
/bin/sh /opt/scripta/bin/prepare-runtime-dir.sh

# Web code is executable policy, not writable application state. Packaging
# installs it root:root (directories 0755, single-link files 0644). Runtime
# repair is deliberately read-only here: walking and mutating a tree that may
# contain an attacker-writable ancestor creates pathname races. Fail closed so
# lighttpd never starts from a drifted or hard-linked webroot.
[ -d /var/www ] && [ ! -L /var/www ] || {
    echo "unsafe webroot: /var/www is not a real directory" >&2
    exit 1
}
if find -P /var/www -xdev \
    \( -type l -o ! -user root -o ! -group root -o \
       \( -type d ! -perm 0755 \) -o \
       \( -type f \( ! -perm 0644 -o -links +1 \) \) -o \
       \( ! -type d ! -type f \) \) -print -quit | grep -q .; then
    echo "unsafe webroot: ownership, mode, type, or link count drift" >&2
    exit 1
fi

# Clear stale firmware-operation locks after crashes or power loss. Active DFU
# jobs keep a live PID; old locks without a process must not block future reads
# or recovery flashes forever.
if [ -f /run/blakestream-firmware/bkb-firmware.lock ]; then
    fw_pid="$(head -n 1 /run/blakestream-firmware/bkb-firmware.pid 2>/dev/null | tr -dc '0-9' || true)"
    fw_mtime="$(stat -c '%Y' /run/blakestream-firmware/bkb-firmware.lock 2>/dev/null || echo 0)"
    fw_age="$(($(date +%s) - fw_mtime))"
    if { [ -z "$fw_pid" ] || [ ! -d "/proc/$fw_pid" ]; } && [ "$fw_age" -gt 1200 ]; then
        rm -f /run/blakestream-firmware/bkb-firmware.lock \
              /run/blakestream-firmware/bkb-firmware.pid \
              /run/blakestream-firmware/bkb-firmware-meta.env \
              /run/blakestream-firmware/bkb-firmware-phase \
              /run/blakestream-firmware/bkb-firmware-abort 2>/dev/null || true
        note_change
    fi
fi

set_owner_mode /opt/scripta/bin/repair-system-perms.sh root:root 755
set_owner_mode /opt/scripta/bin/prepare-web-runtime.sh root:root 755
set_owner_mode /opt/scripta/bin/ensure-web-tls.sh root:root 755
set_owner_mode /opt/scripta/bin/prepare-device-identity.sh root:root 755
set_owner_mode /opt/scripta/bin/seed-device-entropy.py root:root 755
set_owner_mode /opt/scripta/bin/enforce-firmware-recovery-hold.sh root:root 755
set_owner_mode /opt/scripta/policy root:root 755
set_owner_mode /opt/scripta/policy/miner-options.json root:root 644
set_owner_mode /etc/systemd/system/blakestream-web-tls.service root:root 644
set_owner_mode /etc/systemd/system/blakestream-device-identity.service root:root 644
set_owner_mode /etc/systemd/system/blakestream-firmware-recovery.service root:root 644
set_owner_mode /etc/systemd/system/cron.service.d root:root 755
set_owner_mode /etc/systemd/system/cron.service.d/blakestream-firmware-recovery.conf root:root 644
set_owner_mode /etc/systemd/system/ssh.service.d root:root 755
set_owner_mode /etc/systemd/system/ssh.service.d/blakestream-device-identity.conf root:root 644
set_owner_mode /opt/scripta/startup/miner-start.sh root:root 755
set_owner_mode /opt/scripta/startup/miner-restart.sh root:root 755
set_owner_mode /opt/scripta/bin/network-settings.py root:root 755
set_owner_mode /opt/scripta/bin/wifi-settings.py root:root 755
set_owner_mode /opt/scripta/bin/diagnostic-log-recorder.py root:root 755
set_owner_mode /opt/scripta/bin/backup-settings.py root:root 755
# Replay a durable restore journal before mining can consume partially restored
# settings. This runs at boot and from the periodic repair job, and takes the
# same private flock as interactive backup operations.
if [ -x /opt/scripta/bin/backup-settings.py ]; then
    printf '{}\n' | /opt/scripta/bin/backup-settings.py recover >/dev/null || {
        echo "backup rollback recovery failed" >&2
        exit 1
    }
fi
for unit in /etc/systemd/system/blakestream-network-* /etc/systemd/system/blakestream-wifi-*; do
    set_owner_mode "$unit" root:root 644
done
set_owner_mode /etc/systemd/system/NetworkManager.service.d/blakestream-wifi-recovery.conf root:root 644

# Firmware (DFU) helper + staged images: root-owned so www-data cannot swap the
# image flashed as root; wrapper must be executable for the sudoers grant.
set_owner_mode /opt/scripta/bin/bkb-firmware.sh root:root 755
set_owner_mode /opt/scripta/firmware root:root 755
set_owner_mode /opt/scripta/firmware/dfu_flash.py root:root 755
set_owner_mode /opt/scripta/firmware/stock.bin root:root 644
set_owner_mode /opt/scripta/firmware/custom.bin root:root 644
set_owner_mode /opt/scripta/firmware/ENCODER_READY root:root 644

# ping needs a raw ICMP socket; this old image ships /bin/ping without setuid or
# file caps, so the web Network probe (www-data) cannot ICMP. setuid is the
# legacy mechanism this kernel supports.
set_owner_mode /bin/ping root:root 4755
set_owner_mode /usr/bin/ping root:root 4755

if [ "$changed" -eq 1 ]; then
    logger -t blakestream-perms "repaired sudo/cron/helper ownership or modes" 2>/dev/null || true
fi

exit 0
