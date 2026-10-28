#!/usr/bin/env bash
# Reject webroot entries that a privileged ownership/mode normalization must
# never touch. In particular, chmod on a hardlink would chmod the other name's
# inode as well.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: validate-webroot-tree.sh WEBROOT" >&2
    exit 2
fi

WEBROOT=$1
if [[ ! -d "$WEBROOT" || -L "$WEBROOT" ]]; then
    echo "unsafe webroot: missing, non-directory, or symlinked root" >&2
    exit 1
fi

root_dev=$(stat -c '%d' -- "$WEBROOT") || exit 1
while IFS= read -r -d '' entry; do
    if [[ -L "$entry" ]]; then
        printf 'unsafe webroot: symlink entry %q\n' "$entry" >&2
        exit 1
    fi
    entry_dev=$(stat -c '%d' -- "$entry") || exit 1
    if [[ "$entry_dev" != "$root_dev" ]]; then
        printf 'unsafe webroot: cross-device entry %q\n' "$entry" >&2
        exit 1
    fi
    if [[ -d "$entry" ]]; then
        continue
    fi
    if [[ -f "$entry" ]]; then
        links=$(stat -c '%h' -- "$entry") || exit 1
        if [[ "$links" = 1 ]]; then
            continue
        fi
        printf 'unsafe webroot: hardlinked file %q\n' "$entry" >&2
        exit 1
    fi
    printf 'unsafe webroot: special entry %q\n' "$entry" >&2
    exit 1
done < <(find -P "$WEBROOT" -xdev -print0)
