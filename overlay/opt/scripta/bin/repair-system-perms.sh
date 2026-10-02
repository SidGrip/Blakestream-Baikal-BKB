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

ensure_blakestream_sudoers() {
    file=/etc/sudoers.d/blakestream-www
    tmp=

    if [ -f "$file" ] && grep -q '/opt/scripta/startup/miner-restart.sh' "$file" \
        && grep -q '/opt/scripta/bin/prepare-runtime-dir.sh' "$file" \
        && grep -q '/opt/scripta/bin/bkb-firmware.sh' "$file" \
        && grep -q '/opt/scripta/bin/network-settings.py rollback' "$file" \
        && grep -q '/opt/scripta/bin/network-settings.py pending' "$file"; then
        return 0
    fi

    tmp="$(mktemp /etc/sudoers.d/blakestream-www.tmp.XXXXXX)"
    cat > "$tmp" <<'EOF'
# Blakestream-GaintB: allow lighttpd's www-data user to run only the narrow
# root helpers needed by the web UI and recorder runtime setup.
#
# Scope: explicit commands below only. Nothing else.

www-data ALL=(root) NOPASSWD: /opt/scripta/startup/miner-start.sh
www-data ALL=(root) NOPASSWD: /opt/scripta/startup/miner-restart.sh
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/prepare-runtime-dir.sh
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/bkb-firmware.sh
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/network-settings.py status, /opt/scripta/bin/network-settings.py apply, /opt/scripta/bin/network-settings.py confirm, /opt/scripta/bin/network-settings.py rollback
www-data ALL=(root) NOPASSWD: /opt/scripta/bin/network-settings.py pending
EOF
    chown root:root "$tmp"
    chmod 0440 "$tmp"

    if command -v visudo >/dev/null 2>&1; then
        visudo -cf "$tmp" >/dev/null 2>&1 || {
            rm -f "$tmp"
            return 1
        }
    fi

    mv "$tmp" "$file"
    note_change
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

ensure_blakestream_sudoers || true
set_owner_mode /etc/sudoers.d/blakestream-www root:root 440
set_owner_mode /etc/sudoers.d/blakestream-wifi root:root 440

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
set_owner_mode /run/blakestream/temp-state.json root:www-data 664
set_owner_mode /opt/scripta/etc/uipasswd www-data:www-data 600

# Clear stale firmware-operation locks after crashes or power loss. Active DFU
# jobs keep a live PID; old locks without a process must not block future reads
# or recovery flashes forever.
if [ -f /run/blakestream/bkb-firmware.lock ]; then
    fw_pid="$(head -n 1 /run/blakestream/bkb-firmware.pid 2>/dev/null | tr -dc '0-9' || true)"
    fw_mtime="$(stat -c '%Y' /run/blakestream/bkb-firmware.lock 2>/dev/null || echo 0)"
    fw_age="$(($(date +%s) - fw_mtime))"
    if { [ -z "$fw_pid" ] || [ ! -d "/proc/$fw_pid" ]; } && [ "$fw_age" -gt 1200 ]; then
        rm -f /run/blakestream/bkb-firmware.lock \
              /run/blakestream/bkb-firmware.pid \
              /run/blakestream/bkb-firmware-meta.env \
              /run/blakestream/bkb-firmware-phase \
              /run/blakestream/bkb-firmware-abort 2>/dev/null || true
        note_change
    fi
fi

set_owner_mode /opt/scripta/bin/repair-system-perms.sh root:root 755
set_owner_mode /opt/scripta/startup/miner-start.sh root:root 755
set_owner_mode /opt/scripta/startup/miner-restart.sh root:root 755
set_owner_mode /opt/scripta/bin/prepare-runtime-dir.sh root:root 755
set_owner_mode /opt/scripta/bin/network-settings.py root:root 755
set_owner_mode /opt/scripta/bin/wifi-settings.py root:root 755
set_owner_mode /opt/scripta/bin/diagnostic-log-recorder.py root:root 755
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
