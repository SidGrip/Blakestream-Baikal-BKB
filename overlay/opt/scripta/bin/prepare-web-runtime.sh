#!/bin/sh
# Prepare lighttpd/PHP state without following web-controlled paths as root.
set -eu

[ "$#" -eq 0 ] || { echo "prepare-web-runtime: arguments are not accepted" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "prepare-web-runtime: root is required" >&2; exit 1; }

socket_dir=/run/lighttpd
web_dir=/run/blakestream-web

if [ -L /tmp ] || [ ! -d /tmp ]; then
    echo "prepare-web-runtime: unsafe /tmp" >&2
    exit 1
fi
chown root:root /tmp
chmod 1777 /tmp

# The FastCGI socket directory is a top-level child of root-owned /run. The
# service account may write inside it, but root never follows any of its
# children during preparation.
if [ -L "$socket_dir" ] || { [ -e "$socket_dir" ] && [ ! -d "$socket_dir" ]; }; then
    echo "prepare-web-runtime: unsafe FastCGI socket directory" >&2
    exit 1
fi
if [ ! -d "$socket_dir" ]; then
    mkdir -m 0755 "$socket_dir"
fi
chown www-data:www-data "$socket_dir"
chmod 0755 "$socket_dir"

# Executable service setup happens only beneath a root-owned, non-writable
# parent. Recreate the volatile children so legacy symlinks and nested objects
# are removed without any recursive chown through attacker-controlled paths.
if [ -L "$web_dir" ] || { [ -e "$web_dir" ] && [ ! -d "$web_dir" ]; }; then
    echo "prepare-web-runtime: unsafe web runtime directory" >&2
    exit 1
fi
if [ ! -d "$web_dir" ]; then
    mkdir -m 0700 "$web_dir"
fi
web_mode="$(stat -c '%a' "$web_dir" 2>/dev/null || echo unsafe)"
case "$web_mode" in *[!0-7]*|'') echo "prepare-web-runtime: unreadable web runtime mode" >&2; exit 1;; esac
[ $((0$web_mode & 0002)) -eq 0 ] || {
    echo "prepare-web-runtime: world-writable web runtime directory" >&2
    exit 1
}
chown root:root "$web_dir"
chmod 0700 "$web_dir"
chown root:www-data "$web_dir"

for name in compress uploads php-sessions; do
    child="$web_dir/$name"
    if [ -e "$child" ] || [ -L "$child" ]; then
        rm -rf -- "$child"
    fi
    mkdir -m 0700 "$child"
    chown www-data:www-data "$child"
done

access_log="$web_dir/access.log"
if [ -L "$access_log" ] || { [ -e "$access_log" ] && [ ! -f "$access_log" ]; }; then
    rm -f -- "$access_log"
fi
if [ ! -f "$access_log" ]; then
    : > "$access_log"
fi
chown www-data:www-data "$access_log"
chmod 0640 "$access_log"
chmod 0750 "$web_dir"

exit 0
