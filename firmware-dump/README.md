# BK-B STM32F407 bridge firmware dump

Read-only dump of the Baikal BK-B USB bridge MCU, taken to settle whether the
`SET_OPTION` clock byte ever reaches hardware (it doesn't — see `../firmware-custom/README.md`).

## Artifact
- `bkb-stm32f407-flash.bin` — 1,048,576 bytes (full STM32F407VG flash), md5
  `d5f844be2952fd713717a71a67e2be61`. Vendor firmware; **git-ignored** (reproducible below).
- `reftools.py` — capstone (Thumb-2 M-class) load/disasm/xref helpers. Base `0x08000000`.
- `strings.txt` — `strings -t x` index (git-ignored).

## How it was dumped (read-only, reversible)
The bridge is an STM32F407VG, USB `0483:5740`. The factory `update_fw.py` enters the
ST system-DFU bootloader over GPIO — `PA18` = BOOT0 select (inverted: 0 → bootloader),
`PA10` = NRST — then `dfu-util -D` to *write*. We use the same entry but `-U` (read),
which never writes flash (no brick path; power-cycle is the fallback).

On the modified image `PA18`/`PA10` are `gpio_sw cfg=0` (inputs) and must be set to
output (`cfg=1`) before driving. Procedure on the miner:
1. stop sgminer
2. `cfg=1` on PA18/PA10; drive `PA18=0`, pulse `PA10` (0→1) → reset into bootloader
3. `USBDEVFS_RESET` ioctl on the device to force re-enumeration as DFU (`0483:df11`)
4. `dfu-util -a 0 -d 0483:df11 -U flash.bin -s 0x08000000:0x100000`
5. `PA18=1`, pulse `PA10` → boot back to app; restore `cfg=0`; restart miner

DFU layout confirms the part: `@Internal Flash /0x08000000/04*016Kg,01*064Kg,07*128Kg`
(= 1 MB). Not read-protected (RDP off): the upload returns a valid Cortex-M image.

## Key addresses (verified)
- Command dispatch table `0x08003D64` `{name_ptr, handler_ptr}` indexed by cmd 1..7.
- `SET_OPTION` (cmd 3) handler `0x08000CEC` — reads only `[r0,#6]` (cutoff) and
  `[r0,#7]` (fan); **never** reads `[r0,#4]` (clock) or `[r0,#5]` (algo).
- `GET_INFO` (cmd 2) `0x08000950` — clock byte = `MHz_table[0x08003D22][idx] >> 1`.
- Clock index field `struct+0x302` (per-board state base `0x20000E18`, stride `0x374`)
  seeded once to `30` at init `0x08000F86` → `table[30]=480` → reported `240`/`0xF0`.
- Fan PWM: `0x08000CB0` → `0x08002860` → TIM2 (`0x40000000`) CCR2.
- No I²C; RCC/PLL `0x40023800` only in boot SystemInit, unreachable from any command.

The stock runtime handler drops the clock byte and does not run the startup
clock-table programming sequence. V1 adds that path; the table index is not a
measurement of physical ASIC frequency.
