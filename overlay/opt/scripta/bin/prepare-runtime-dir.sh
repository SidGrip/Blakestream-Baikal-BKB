#!/bin/sh
# Prepare tmpfs runtime storage used by www-data recorders and Dev Tools.
# This script accepts no arguments and only touches /run/blakestream.

set -eu

runtime_dir=/run/blakestream

mkdir -p "$runtime_dir"
chown root:www-data "$runtime_dir"
chmod 0775 "$runtime_dir"

# If a recorder is killed during an atomic JSON write, its temporary file can
# remain in tmpfs and slowly consume the controller's limited RAM.
find "$runtime_dir" -maxdepth 1 -type f -name '*.tmp' -mmin +10 -exec rm -f {} \; 2>/dev/null || true
