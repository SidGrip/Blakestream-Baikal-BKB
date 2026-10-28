#!/bin/sh
# Prepare the web-writable runtime plus isolated directories used by root
# helpers. This script accepts no arguments and must run as root.

set -eu

[ "$#" -eq 0 ] || { echo "prepare-runtime-dir: arguments are not accepted" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "prepare-runtime-dir: root is required" >&2; exit 1; }

runtime_dir=/run/blakestream
miner_runtime_dir=/run/blakestream-miner
miner_log_dir=/var/log/blakestream-miner
control_runtime_dir=/run/blakestream-control
firmware_runtime_dir=/run/blakestream-firmware
security_runtime_dir=/run/blakestream-security
security_log_dir=/var/log/blakestream-security
firstboot_state_dir=/var/lib/blakestream-firstboot
firstboot_log_dir=/var/log/blakestream-firstboot
identity_state_dir=/var/lib/blakestream-identity
backup_runtime_dir=/run/blakestream-backup
backup_state_dir=/var/lib/blakestream-backups

prepare_sealed_dir() {
    dir="$1"
    owner="$2"
    final_mode="$3"
    if [ -L "$dir" ] || { [ -e "$dir" ] && [ ! -d "$dir" ]; }; then
        echo "ERROR: unsafe privileged runtime path: $dir" >&2
        exit 1
    fi
    if [ ! -d "$dir" ]; then
        mkdir -m 0700 "$dir"
    fi
    current_mode="$(stat -c '%a' "$dir" 2>/dev/null || echo unsafe)"
    case "$current_mode" in *[!0-7]*|'') echo "ERROR: unreadable privileged runtime mode: $dir" >&2; exit 1;; esac
    # The parent of every path in this function is root-owned. Seal the
    # directory inode before inspecting any child, even when an older image
    # left it group/world writable; child hard links are rejected below.
    chown root:root "$dir"
    chmod 0700 "$dir"
    chown "$owner" "$dir"
    chmod "$final_mode" "$dir"
}

sanitize_root_file() {
    path="$1"
    owner="$2"
    mode="$3"
    if [ -L "$path" ] || { [ -e "$path" ] && [ ! -f "$path" ]; }; then
        rm -f -- "$path"
        return
    fi
    [ -f "$path" ] || return 0
    # A root-owned hard link planted before this directory was sealed would
    # make the metadata repair affect an inode outside the runtime tree.
    # Remove the runtime name instead of touching any multiply-linked inode.
    if [ "$(stat -c '%h' "$path" 2>/dev/null || echo unsafe)" != 1 ]; then
        rm -f -- "$path"
        return
    fi
    if [ "$(stat -c '%u' "$path" 2>/dev/null || echo unsafe)" != 0 ]; then
        rm -f -- "$path"
        return
    fi
    chown "$owner" "$path"
    chmod "$mode" "$path"
}

validate_critical_root_file() {
    path="$1"
    mode="$2"
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
        return 0
    fi
    # Absence means no recovery hold. Any existing name must therefore be an
    # unambiguous marker, never something repair may silently discard and
    # reinterpret as absence.
    if [ -L "$path" ] || [ ! -f "$path" ]; then
        echo "ERROR: unsafe critical recovery marker: $path" >&2
        exit 1
    fi
    if [ "$(stat -c '%u:%h' "$path" 2>/dev/null || echo unsafe)" != "0:1" ]; then
        echo "ERROR: unsafe critical recovery marker metadata: $path" >&2
        exit 1
    fi
    chown root:root "$path"
    chmod "$mode" "$path"
}

mkdir -p "$runtime_dir"
chown root:www-data "$runtime_dir"
chmod 0775 "$runtime_dir"

# Privileged writers never use the 0775 web runtime. Web diagnostics receive
# group-read access to files in these sealed (non-group-writable) directories.
prepare_sealed_dir "$miner_runtime_dir" root:www-data 0750
prepare_sealed_dir "$miner_log_dir" root:www-data 0750
prepare_sealed_dir "$control_runtime_dir" root:www-data 0750
prepare_sealed_dir "$firmware_runtime_dir" root:www-data 0750
prepare_sealed_dir "$security_runtime_dir" root:www-data 0750
prepare_sealed_dir "$security_log_dir" root:www-data 0750
prepare_sealed_dir "$firstboot_state_dir" root:www-data 0750
prepare_sealed_dir "$firstboot_log_dir" root:www-data 0750
prepare_sealed_dir "$identity_state_dir" root:root 0700
prepare_sealed_dir "$backup_runtime_dir" root:www-data 0750
prepare_sealed_dir "$backup_state_dir" root:root 0700

# A previous development image may have used weaker directory permissions.
# Remove unsafe known entries before restoring group read/execute access.
for protected_path in \
    "$miner_runtime_dir/scripta-runtime-miner.conf" \
    "$miner_runtime_dir/scripta-runtime-poolmap.json" \
    "$miner_runtime_dir/miner-api-hang.count" \
    "$miner_runtime_dir/sgminer.log" \
    "$miner_log_dir/sgminer.log" \
    "$control_runtime_dir/fan-control-state.json" \
    "$control_runtime_dir/temp-state.json" \
    "$firmware_runtime_dir/bkb-firmware-progress.log" \
    "$firmware_runtime_dir/bkb-firmware.lock" \
    "$firmware_runtime_dir/bkb-firmware.pid" \
    "$firmware_runtime_dir/bkb-firmware-meta.env" \
    "$firmware_runtime_dir/bkb-firmware-result.env" \
    "$firmware_runtime_dir/bkb-firmware-phase" \
    "$firmware_runtime_dir/bkb-firmware-abort" \
    "$security_log_dir/ssh.log" \
    "$firstboot_state_dir/result.json" \
    "$firstboot_log_dir/firmware-firstboot.log"
do
    sanitize_root_file "$protected_path" root:www-data 0640
done
sanitize_root_file "$firmware_runtime_dir/bkb-firmware-result-applied" root:www-data 0660
sanitize_root_file "$firmware_runtime_dir/bkb-firmware-start.lock" root:root 0600
sanitize_root_file "$miner_runtime_dir/sgminer.log.lock" root:root 0600
sanitize_root_file "$miner_log_dir/sgminer.log.lock" root:root 0600
sanitize_root_file "$control_runtime_dir/fan-controller.lock" root:root 0600
sanitize_root_file "$security_runtime_dir/sshd-journal.cursor" root:root 0600
sanitize_root_file "$security_log_dir/ssh.log.lock" root:root 0600
sanitize_root_file "$firstboot_state_dir/firmware-firstboot.pending" root:root 0644
validate_critical_root_file "$firstboot_state_dir/firmware-recovery-required" 0600
sanitize_root_file "$identity_state_dir/entropy-provisioned" root:root 0600
sanitize_root_file "$backup_runtime_dir/backup-export.zip" root:www-data 0640
sanitize_root_file "$backup_runtime_dir/backup-settings.lock" root:root 0600

# If a recorder is killed during an atomic JSON write, its temporary file can
# remain in tmpfs and slowly consume the controller's limited RAM.
find "$runtime_dir" -maxdepth 1 -type f -name '*.tmp' -mmin +10 -exec rm -f {} \; 2>/dev/null || true
find "$miner_runtime_dir" -maxdepth 1 -type f -name '.*.tmp.*' -mmin +10 -exec rm -f {} \; 2>/dev/null || true
find "$firmware_runtime_dir" -maxdepth 1 -type f -name '.*.tmp.*' -mmin +10 -exec rm -f {} \; 2>/dev/null || true
find "$security_runtime_dir" -maxdepth 1 -type f -name '.sshd-*' -mmin +10 -exec rm -f {} \; 2>/dev/null || true

# Remove exact legacy names from the world-writable /tmp location. rm removes
# a malicious symlink itself and never follows it to its target.
for legacy_path in \
    /tmp/scripta-runtime-miner.conf \
    /tmp/scripta-runtime-poolmap.json \
    /tmp/scripta-runtime-miner.conf.tmp \
    /tmp/scripta-runtime-poolmap.json.tmp \
    /tmp/build-miner-runtime.err
do
    if [ -e "$legacy_path" ] || [ -L "$legacy_path" ]; then
        rm -f -- "$legacy_path"
    fi
done

# Never migrate or read these former privileged outputs from the web-writable
# runtime. Unlinking an exact name removes a planted symlink itself.
for legacy_path in \
    /run/blakestream/sgminer.log \
    /run/blakestream/fan-control-state.json \
    /run/blakestream/fan-controller.lock \
    /run/blakestream/temp-state.json \
    /run/blakestream/sshd-journal.cursor \
    /run/blakestream/bkb-firmware-progress.log \
    /run/blakestream/bkb-firmware.lock \
    /run/blakestream/bkb-firmware.pid \
    /run/blakestream/bkb-firmware-meta.env \
    /run/blakestream/bkb-firmware-result.env \
    /run/blakestream/bkb-firmware-phase \
    /run/blakestream/bkb-firmware-abort \
    /run/blakestream/bkb-firmware-result-applied
do
    if [ -e "$legacy_path" ] || [ -L "$legacy_path" ]; then
        rm -f -- "$legacy_path"
    fi
done
