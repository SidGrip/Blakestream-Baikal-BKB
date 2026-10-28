# Scripts

## Running QA

From the repository root, run `scripts/qa`. From another directory, invoke
the script by its full path. It locates the checkout itself, including paths
with spaces; it does not require a particular username, home directory or host.

Default QA runs the PHP, Node and Python contracts plus both firmware emulation
phases. It needs Linux, Bash, Git, standard GNU utilities, PHP CLI, Node.js 18+,
Python 3.8+, OpenSSL, and the Python `unicorn` module. It does not contact a miner, flash
firmware, install dependencies, or require root. Use `BKB_PHP`, `BKB_NODE` and
`BKB_PYTHON` to select interpreters; use absolute paths for relocated runs.
See [QA.md](../QA.md) for dependency setup and coverage limits.

`scripts/qa --describe` summarizes scope; `--help` lists phases and
overrides. Request optional phases explicitly, such as
`scripts/qa network source-baseline`. Missing requested dependencies fail;
an optional phase not run is not a pass. Browser layout and real-hardware
checks are separate from these automated fixtures.

## Image tools

| Script | Purpose |
| --- | --- |
| [01-fetch.sh](01-fetch.sh) | Download and verify the factory SD image. |
| [02-mount.sh](02-mount.sh) | Mount a disposable image copy for inspection. |
| [02-umount.sh](02-umount.sh) | Unmount the inspection copy and release its loop device. |
| [03-snapshot.sh](03-snapshot.sh) | Create reference and working rootfs copies. |
| [04-apply-overlay.sh](04-apply-overlay.sh) | Copy the controller and web overlay into the working rootfs. |
| [05-repack.sh](05-repack.sh) | Assemble the output SD image and checksum. |
| [06-compress.sh](06-compress.sh) | Compress the output image to `.img.xz`. |
| [prune-rootfs.sh](prune-rootfs.sh) | Remove superseded files and private runtime data from a staged image. |
| [prepare-image-network.py](prepare-image-network.py) | Set fresh-image Wi-Fi off while leaving Ethernet networking enabled. |
| [prepare-image-wifi.py](prepare-image-wifi.py) | Enable onboard Wi-Fi discovery and remove the obsolete forced Realtek driver load. |
| [prepare-image-startup.py](prepare-image-startup.py) | Configure boot-time permissions and remove obsolete time/mail startup paths. |

Follow [BUILD.md](../BUILD.md) for the build order and prerequisites.

## Testing and diagnostics

| Script | Purpose |
| --- | --- |
| [qa](qa) | Run automated checks; use `--help` to list phases. |
| [test-web-tls.py](test-web-tls.py) | Generate and validate X.509 v3 HTTPS identities; reject malformed certificates, mismatched keys, unsafe paths, and missing entropy. |
| [terminal-qa](terminal-qa/README.md) | Test HTTPS browser terminal and SSH authentication using the packaged ARM software in an isolated container. |
| [test-clock-history-memory.py](test-clock-history-memory.py) | Compare full-week recorder peak memory in isolated processes and verify lossless RAM/SD writes. |
| [test-bkb-diagnostics-recorder.py](test-bkb-diagnostics-recorder.py) | Verify diagnostic capture, epochs, redaction, atomic retention, subprocess-free timing and full-field 18-hour peak memory against a plain baseline. |
| [test-system-memory.php](test-system-memory.php) | Check legacy-kernel tmpfs accounting and `MemAvailable` handling. |
| [test-hashrate-history.php](test-hashrate-history.php) | Verify lossless streamed chart responses, RAM/SD selection, authentication and bounded full-week PHP memory. |
| [test-clock-history-endpoint.php](test-clock-history-endpoint.php) | Verify streamed clock runs, bounded full-week memory, selective clears, rollback, collector locking and CGI/session behavior. |
| [test-capped-log-writer.py](test-capped-log-writer.py) | Inject log I/O failures and verify bounded retention, safe trimming and single-reader recovery without restarting the producer. |
| [test-duration.js](test-duration.js) | Check uptime formatting across hour, day and long-running boundaries. |
| [test-firmware-container.sh](test-firmware-container.sh) | Run isolated flash, recovery, web-user, sudo, and screen checks. |
| [test-wifi-container.sh](test-wifi-container.sh) | Check NetworkManager profile storage and credentials in an isolated container. |
| `test-*` | Contract fixtures and standalone integration helpers; see QA.md for phase membership. |
| [bkb-clock-trial.py](bkb-clock-trial.py) | Validate schedules, observe mining, run guarded clock-index trials, or restore clock settings. |
| [clock-schedules/](clock-schedules/) | Example bounded clock-index trial schedules. |
| [export-clock-baseline.py](export-clock-baseline.py) | Export completed observations and available measured-power evidence into separate algorithm datasets. |
| [publish-clock-baseline.py](publish-clock-baseline.py) | Produce shared GH/s, estimated MHz, and one average whole-miner watts value per metered index, without private source paths. |
| [render-bkb-logic-pinout.js](render-bkb-logic-pinout.js) | Generate pinout reference graphics. |

Clock-trial `run --execute` and `restore --execute` change a live miner.
The firmware and Wi-Fi container runners are opt-in and require prepared Docker
images. The Wi-Fi runner is standalone, not part of `scripts/qa network`.
See [QA.md](../QA.md) for test coverage and the
[Technical Guide](../TECHNICAL-GUIDE.md) for operating limits and recovery.
