# BK-B STM32 Bridge Flash And Rollback

These are hardware-operation tools, not QA tests. Use a matching V1 host
deployment and stable power. **Backup, verify, and CLI dry-run operations
also pause mining and reset the bridge into ROM DFU**, even when they do not
write application flash. Do not run them during a measurement window.

Prefer the guarded Dev Tools workflow for normal operation. It checks the
staged image and encoder readiness, takes a pre-flash backup, writes the
application image, and read-back-verifies it. It attempts rollback on
failure. Neither backups nor ROM DFU guarantee recovery from power or
hardware faults.

## Developer CLI

`bkb-flash.sh` requires an explicit SSH hostname/address in `MINER` and
root SSH access configured by the operator. It does not embed a password or
disable SSH host-key checking. Test host authentication separately first.
It uses the installed root-owned wrapper/flasher; it never uploads executable
code to `/tmp` or writes identity state itself. It can be invoked from any
working directory.

```sh
export MINER=miner-hostname
/path/to/checkout/firmware-custom/flash/bkb-flash.sh --help

# Pauses mining; reads current flash twice and preserves a local backup.
/path/to/checkout/firmware-custom/flash/bkb-flash.sh backup

# Pauses mining; compares chip readback with the selected image.
/path/to/checkout/firmware-custom/flash/bkb-flash.sh verify custom

# Validation only: compare local and staged image hashes, no DFU or write.
/path/to/checkout/firmware-custom/flash/bkb-flash.sh flash custom

# These explicitly WRITE application flash.
/path/to/checkout/firmware-custom/flash/bkb-flash.sh flash custom --confirm
/path/to/checkout/firmware-custom/flash/bkb-flash.sh rollback --confirm
```

Do not run CLI and web firmware operations concurrently. Preserve the
backups outside the source tree before any subsequent operation. Backup
files are private operational data, not release files.

## Images

| Image | Repository-relative path | MD5 |
| --- | --- | --- |
| V1 | `firmware-custom/build/bkb-stm32f407-flash-clockmod-v1.bin` | `18babe0c4c67d9146e230f9dc423f2f2` |
| Stock | `overlay/opt/scripta/firmware/stock.bin` | `d5f844be2952fd713717a71a67e2be61` |

The V1 release name does not change the image bytes. Verify the local hash
before transfer and the chip readback after a real write. An image hash or
cached identity record alone is not a fresh chip readback.

## Recovery

The flasher uses the factory controller GPIO path: PA18 controls BOOT0
with inverted polarity and PA10 controls NRST. These are Orange Pi GPIO
names, not STM32 package pins. Application USB ID is `0483:5740`; ROM DFU
is `0483:df11`. The write target is the 1 MiB application flash starting
at `0x08000000`, not option bytes or system ROM.

For manual recovery, use the installed canonical
`/opt/scripta/firmware/dfu_flash.py` implementation. Its `forcedfu` and
`restore-gpio` operations are root-only last-resort tools; normal writes use
the guarded Dev Tools/start-job workflow. Retain a verified stock image. Do
not guess GPIO wiring or interrupt an active write.

After recovery, verify all boards are alive, inspect command status, and
start a fresh accepted-work window. Do not infer physical PLL lock from
the bridge status echo.
