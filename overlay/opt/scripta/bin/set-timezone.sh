#!/bin/bash
# set-timezone.sh — copy a zoneinfo file to /etc/localtime.
# Invoked by Scripta f_settings.php via sudo.
#
# Validates that the requested timezone resolves to a real regular file
# under /usr/share/zoneinfo before copying, to prevent path-traversal
# (e.g. "../../etc/shadow" or symlinks pointing outside).

set -eu

TZ_NAME="${1:-}"
if [[ -z "$TZ_NAME" ]]; then
    echo "usage: $0 <timezone>  (e.g. America/Chicago)" >&2
    exit 2
fi

# Reject anything containing .. or starting with /
if [[ "$TZ_NAME" == *..* || "$TZ_NAME" == /* ]]; then
    echo "invalid timezone: $TZ_NAME" >&2
    exit 3
fi

SRC="/usr/share/zoneinfo/$TZ_NAME"
RESOLVED=$(readlink -f "$SRC" 2>/dev/null || echo "")

# Resolved path must exist, be a regular file, and live under /usr/share/zoneinfo
if [[ -z "$RESOLVED" || ! -f "$RESOLVED" ]]; then
    echo "timezone file not found: $SRC" >&2
    exit 4
fi
case "$RESOLVED" in
    /usr/share/zoneinfo/*) : ;;
    *) echo "resolved path escapes zoneinfo: $RESOLVED" >&2; exit 5 ;;
esac

cp -f "$RESOLVED" /etc/localtime
