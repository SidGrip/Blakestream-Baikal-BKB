# BK-B Bridge Firmware V1

The bridge firmware release is **V1**. The controller software release is
**Blakestream-GaintB V2.2**. This repository contains the current firmware and
stock rollback image; historical experimental images remain in the original
development archive.

## Command Contract

- Board-specific `dest=0` commands select one host board, ID 0 through 2.
- Bridge-global `dest=1` commands prevalidate all three board states and all
  target readiness records before programming.
- Each selected board must be in host state 4 or 5, with all four target
  readiness bytes equal to 3. Accepted clock indices are 8 through 32.
- The stock parameter block is `{index,1,1,6,7,7,0,0}`.
- Targets and selectors are processed in ascending order. Selector 0 uses
  the requested clock index. Selectors 1 through 15 follow the stock fixed
  register path. Each transaction gets the stock post-call delay.
- Each board has 4 targets times 16 selectors, or 64 slots. Clock commands
  follow stock work eligibility: states 2 and 3 receive a sender transaction,
  inactive slots are skipped. The per-slot delay is retained. Healthy boards
  receive 64 transactions, or 192 for a healthy global request.
- A board's echo changes only when all its active-slot transactions succeed. Global
  command verification requires successful completion and readback from
  every selected board. Neither echo nor ACK proves ASIC PLL lock.
- Clock code 0 is fan/temperature-only and does not program PLLs.
- Stock cutoff, fan clamping, and ACK behavior are retained.

There are no hidden per-board clock corrections or diagnostic reset passes.
The host exposes Index 8..32, with the nominal 400 MHz default at Index 30.
Physical clock calibration and stock-equivalence testing remain
separate from software verification.

### Why The Range Ends At 32

V1 explicitly rejects nonzero indices below 8 or above 32 before programming
any selected target (`patch_set_option_v1.S`). This is a software contract, not
a measured physical ASIC frequency limit. The stock sender indexes four-byte
clock records at flash offset `0x41AC` (RAM address `0x2000000C`). Index 32 is
`02 10 f0 09`, the last recognizable entry of that table. The next bytes
at would-be indices 33 and 34 are `91 17 ff ff` and `e7 19 00 08`, not additional
validated clock records. Raising a maximum alone would feed those bytes to the
sender as parameters. Higher settings require investigation of the register
encoding and deliberately validated new table entries, host support, and a
separate firmware build/verification; a clean index-32 screen does not supply
that evidence. No higher-index firmware has been built or flashed for this UI.

## Artifacts

| Image | Location | MD5 |
| --- | --- | --- |
| V1 | `build/bkb-stm32f407-flash-clockmod-v1.bin` | `18babe0c4c67d9146e230f9dc423f2f2` |
| Staged V1 | `../overlay/opt/scripta/firmware/custom.bin` | `18babe0c4c67d9146e230f9dc423f2f2` |
| Stock | `../overlay/opt/scripta/firmware/stock.bin` | `d5f844be2952fd713717a71a67e2be61` |

V1 SHA256: `d2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a`.
Recognition requires the verified hash, not just a release label.

## Verification And Build

From the repository root:

```sh
./scripts/qa emulation-v1 emulation-active-slots
BKB_STOCK_DUMP="$PWD/overlay/opt/scripta/firmware/stock.bin" \
  ./scripts/qa firmware-build
```

The emulator exercises ordering, frames, delays, readiness, transport failures,
echo gating, stock ACK shape, and fan-only commands. No miner is contacted.
The firmware build requires a verified stock image and ARM bare-metal tools.
Outputs are separate from the staged image; successful compilation does not
automatically flash or promote a binary. See [BUILD.md](../BUILD.md).

## Flashing And Rollback

Use the guarded Dev Tools firmware workflow with a matching host binary.
Keep stable power, preserve the pre-flash backup, perform the dry run, and
require readback verification. Stock rollback is a separate explicit flash.
The workflow reduces risk but cannot guarantee recovery from hardware faults
or interrupted power. See [flash/README.md](flash/README.md).

## Evidence

Private diagnostic records are kept locally, outside the published tree.
The observed startup count deficit followed a physical board during a swap.
Replacing it restored
64 startup successes on all three boards. These observations do not establish
physical MHz or stock-equivalent accepted-work rates.

Optional probing details are in
[BKB-LOGIC-ANALYZER-PINOUT.md](../BKB-LOGIC-ANALYZER-PINOUT.md).
