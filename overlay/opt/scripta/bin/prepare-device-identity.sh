#!/bin/sh
# Establish device-local entropy and SSH host identity before network services.
# Compatible with the controller's systemd 229 / Linux 3.4 userspace.
set -eu

umask 077

ssh_dir=/etc/ssh
tls_pem=/etc/lighttpd/certs/lighttpd.pem
random_seed=/var/lib/systemd/random-seed
entropy_ready=/run/blakestream-identity-entropy.ready
state_dir=/var/lib/blakestream-identity
provisioned=$state_dir/entropy-provisioned
entropy_helper=/opt/scripta/bin/seed-device-entropy.py
key_types='rsa dsa ecdsa ed25519'

fail() {
    echo "device identity: $*" >&2
    exit 1
}

[ -d "$ssh_dir" ] && [ ! -L "$ssh_dir" ] || fail "unsafe SSH configuration directory"
[ "$(stat -c '%u' "$ssh_dir" 2>/dev/null || echo unsafe)" = 0 ] || fail "SSH directory is not root-owned"
ssh_mode="$(stat -c '%a' "$ssh_dir" 2>/dev/null || echo unsafe)"
case "$ssh_mode" in *[!0-7]*|'') fail "SSH directory mode is unreadable";; esac
[ $((0$ssh_mode & 0022)) -eq 0 ] || fail "SSH directory is writable by non-root"

[ -d "$state_dir" ] && [ ! -L "$state_dir" ] || fail "unsafe identity state directory"
[ "$(stat -c '%u:%a' "$state_dir" 2>/dev/null || echo unsafe)" = '0:700' ] ||
    fail "identity state directory is not root-only"
[ -x "$entropy_helper" ] && [ ! -L "$entropy_helper" ] || fail "entropy helper is unavailable"

provisioned_ok=0
if [ -f "$provisioned" ] && [ ! -L "$provisioned" ] &&
   [ "$(stat -c '%u:%a' "$provisioned" 2>/dev/null || echo unsafe)" = '0:600' ] &&
   [ -f "$random_seed" ] && [ ! -L "$random_seed" ] &&
   [ "$(stat -c '%u' "$random_seed" 2>/dev/null || echo unsafe)" = 0 ] &&
   [ "$(stat -c '%s' "$random_seed" 2>/dev/null || echo 0)" -ge 32 ]; then
    provisioned_ok=1
fi

# Without the root-only marker, every inherited identity is potentially cloned.
# Remove it before the blocking entropy barrier rather than accepting a key that
# merely parses correctly.
if [ "$provisioned_ok" -ne 1 ]; then
    for key_type in $key_types; do
        key="$ssh_dir/ssh_host_${key_type}_key"
        pub="$key.pub"
        [ ! -d "$key" ] && [ ! -d "$pub" ] || fail "directory at SSH host-key path"
        rm -f -- "$key" "$pub"
    done
    [ ! -d "$tls_pem" ] || fail "directory at web TLS identity path"
    rm -f -- "$tls_pem"
    [ ! -d "$random_seed" ] || fail "directory at random-seed path"
    if [ -L "$random_seed" ] || { [ -e "$random_seed" ] && [ ! -f "$random_seed" ]; } ||
       { [ -f "$random_seed" ] && [ "$(stat -c '%u' "$random_seed" 2>/dev/null || echo unsafe)" != 0 ]; }; then
        rm -f -- "$random_seed"
    fi
    /usr/bin/python -E -s "$entropy_helper" || fail "first-boot entropy provisioning failed"
fi

for key_type in $key_types; do
    key="$ssh_dir/ssh_host_${key_type}_key"
    pub="$key.pub"
    [ ! -L "$key" ] && [ ! -L "$pub" ] || fail "symlinked SSH host-key path"
    if [ -e "$key" ]; then
        [ -f "$key" ] || fail "SSH host key is not a regular file: $key"
        [ "$(stat -c '%u' "$key" 2>/dev/null || echo unsafe)" = 0 ] || fail "SSH host key is not root-owned: $key"
        if ! /usr/bin/ssh-keygen -y -f "$key" >/dev/null 2>&1; then
            rm -f -- "$key" "$pub"
        fi
    fi
done

rm -f -- "$entropy_ready"
/usr/bin/ssh-keygen -A >/dev/null 2>&1 || fail "SSH host-key generation failed"

for key_type in $key_types; do
    key="$ssh_dir/ssh_host_${key_type}_key"
    pub="$key.pub"
    [ -f "$key" ] && [ ! -L "$key" ] || fail "missing SSH host key: $key"
    /usr/bin/ssh-keygen -y -f "$key" >/dev/null 2>&1 || fail "invalid generated SSH host key: $key"
    chown root:root "$key"
    chmod 0600 "$key"
    if [ -e "$pub" ]; then
        [ -f "$pub" ] && [ ! -L "$pub" ] || fail "unsafe SSH public-key path: $pub"
        chown root:root "$pub"
        chmod 0644 "$pub"
    fi
done

[ -f "$random_seed" ] && [ ! -L "$random_seed" ] || fail "fresh random seed is missing"
chown root:root "$random_seed"
chmod 0600 "$random_seed"
[ -f "$provisioned" ] && [ ! -L "$provisioned" ] || fail "entropy provisioning marker is missing"

printf '%s\n' 'kernel entropy initialized for device identity generation' > "$entropy_ready"
chown root:root "$entropy_ready"
chmod 0600 "$entropy_ready"

exit 0
