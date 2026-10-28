# Firmware Container QA

From the root of your checkout on a Linux host with Docker:

```bash
scripts/test-firmware-container.sh --build
scripts/qa firmware-container
```

`--build` prepares the legacy PHP/Python dependencies. Test runs require that
image already exists and run offline. `BKB_FIRMWARE_QA_IMAGE` overrides its name.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Isolated legacy runtime with real sudo and screen. |
| `test-container.py` | Real HTTP/job/launcher integration and user-permission assertions. |
| `hardware.py` | File-backed DFU reads/writes, USB enumeration, and fault injection. |
| `sitecustomize.py` | Container-only GPIO trace/redirection and shorter DFU settling delays. |
| `miner.py` | Loopback API substitute launched through the real screen workflow. |

Production helpers are copied unchanged. Only GPIO, USB/DFU transport, and the
miner executable are substituted. The test checks backup/write/readback order
and actual bytes, not an invented success message. Failure cases must retain a
failed result and must not train successful-flash timing.

The container has its own users and writable filesystem. Only `scripts/` and
`overlay/` are mounted read-only; no host hardware devices or networking beyond
loopback are available. Never run the inner test script on a controller. Do not
add privileged mode or extra host mounts. Setuid transitions are intentionally
enabled so real sudo and screen are exercised.

This does not emulate STM32 peripherals, USB reset ioctls, or a full SD image.
PHP's test server replaces lighttpd/CGI, settling time is shortened, and successful
tests do not prove hardware timing, electrical safety, or recovery after power
loss. The legacy container is for isolated testing, not a deployed web service.
The [QA guide](../../QA.md#container-integration) lists coverage and remaining limits.
