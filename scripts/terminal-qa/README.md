# Browser terminal integration check

Run this on a Linux host with Docker after the normal image build has prepared
`firmware-modified/rootfs`. Docker must support `linux/arm/v7` containers. The
test uses the packaged ARM ShellInABox, OpenSSH, lighttpd, PHP, and OpenSSL.
No miner or USB hardware is involved.

From the root of your checkout, import the staged rootfs into a temporary Docker
image, then run the test with only loopback networking and a read-only
test-script mount:

```sh
ROOTFS="${BKB_TERMINAL_QA_ROOTFS:-$PWD/firmware-modified/rootfs}"
QA_IMAGE="${BKB_TERMINAL_QA_IMAGE:-blakestream-terminal-qa:local}"
sudo tar -C "$ROOTFS" -c . |
  docker import --platform linux/arm/v7 - "$QA_IMAGE"
docker run --rm --platform linux/arm/v7 --network none \
  -e BKB_TERMINAL_QA=1 -v "$PWD/scripts/terminal-qa:/qa:ro" \
  --entrypoint /usr/bin/python3 "$QA_IMAGE" /qa/test-container.py
docker image rm "$QA_IMAGE"
```

Set `BKB_TERMINAL_QA_ROOTFS` to use another staged rootfs, or
`BKB_TERMINAL_QA_IMAGE` to choose the temporary image name.

The script refuses to run outside a root Docker container whose only network
interface is loopback. It creates random test passwords inside the disposable
container and checks the loopback listener, HTTP-to-HTTPS redirect, HTTPS page
and assets, successful SSH login/command execution, and rejection of an account
outside the SSH allowlist. The passwords are never printed or retained on the
host. A locally accepted self-signed certificate is used only by the test client.

This is an integration check; boot ordering still depends on the packaged
systemd units and requires a normal target boot for hardware qualification.
