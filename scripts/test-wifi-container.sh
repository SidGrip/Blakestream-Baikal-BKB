#!/usr/bin/env bash
# Profile persistence checks on a private NetworkManager bus, without a radio.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMAGE=${BKB_WIFI_QA_IMAGE:-blakestream/wifi-qa:local}
docker image inspect "$IMAGE" >/dev/null
docker run --rm --network=none --cap-add=NET_ADMIN --cpus=2 --memory=512m \
    -v "$ROOT:/repo:ro" "$IMAGE" bash -euc '
mkdir -p /run/dbus /run/NetworkManager
mkdir -p /etc/NetworkManager/conf.d
cp /repo/overlay/etc/NetworkManager/conf.d/20-blakestream-dns.conf /etc/NetworkManager/conf.d/
dbus-daemon --system --fork
NetworkManager --no-daemon >/tmp/nm.log 2>&1 &
nm=$!
trap '\''kill "$nm" 2>/dev/null || true'\'' EXIT
for attempt in $(seq 1 50); do
    if nmcli general status >/dev/null 2>&1; then break; fi
    sleep 0.2
done
python3 /repo/scripts/test-wifi-container.py
'
