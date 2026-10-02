# BK-B Logic Analyzer Pinout

## Signals and Instruments

These pinouts expose the STM32F407 bridge's SPI buses and board-routing GPIOs.
They identify what each pin does and how to connect a logic analyzer or
oscilloscope. The headers are diagnostic breakouts, not factory-installed
connectors; verify the wiring and connector orientation before connecting.

**SPI SCK is the programming-bus clock, not the ASIC core clock.** None of the
pins below is a verified ASIC core-clock output. Measuring SCK cannot confirm
the miner's nominal 400 MHz or its performance-equivalent MHz estimates.

## Scope and Naming

The STM32 firmware has 12 internal targets, four for each host board:

| Host board | Internal targets | SPI buses |
|---|---:|---|
| Board 0 | 0-3 | SPI1 and SPI3 |
| Board 1 | 4-7 | SPI1 and SPI3 |
| Board 2 | 8-11 | SPI1 and SPI2 |

In this document:

- **Setup** is the firmware table's route/setup GPIO.
- **Restore** is the GPIO restored after the SPI frame; it acts as the
  transaction chip-select path.
- **Selectors 0-7** and **selectors 8-15** use different setup/restore entries.
- These GPIOs are Baikal routing signals. They are not the STM32 hardware NSS
  pins.

The package references below are for the STM32F407VET6 in the LQFP100 package,
viewed from the top. Physical pin numbers and SPI alternate functions match the
[official STM32F405/407 datasheet](https://www.st.com/resource/en/datasheet/dm00037051.pdf).

## Connector Numbering

Use a straight 2x8 female socket header with the socket openings facing the
controller board's front edge. The tables use the following **custom position
numbering**, viewed looking into the socket openings:

```text
pin-1 mark
   v
+----+----+----+----+----+----+----+----+
|  1 |  2 |  3 |  4 |  5 |  6 |  7 |  8 |
+----+----+----+----+----+----+----+----+
|  9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
+----+----+----+----+----+----+----+----+
```

This is not conventional IDC odd/even numbering. The fabricator must preserve
the position layout above and add a visible pin-1 mark. If standard odd/even
connector numbering is required, translate the positions before assembly and
update both ends of the harness consistently.

## Primary Header: Board 2

This header exposes Board 2's two SPI buses and eight route/control signals.

| Position | Analyzer | MCU net | LQFP100 pin | Captured function |
|---:|---|---|---:|---|
| 1 | D0 | PA5 | 30 | SPI1 SCK |
| 2 | D1 | PA7 | 32 | SPI1 MOSI |
| 3 | D2 | PA6 | 31 | SPI1 MISO |
| 4 | D3 | PB13 | 52 | SPI2 SCK |
| 5 | D4 | PB15 | 54 | SPI2 MOSI |
| 6 | D5 | PB14 | 53 | SPI2 MISO |
| 7 | D6 | PD14 | 61 | Targets 8/10 setup, selectors 0-7 |
| 8 | D7 | PD15 | 62 | Targets 8/10 setup, selectors 8-15 |
| 9 | D8 | PC6 | 63 | Targets 9/11 setup, selectors 0-7 |
| 10 | D9 | PC7 | 64 | Targets 9/11 setup, selectors 8-15 |
| 11 | D10 | PD4 | 85 | Targets 8/10 restore, selectors 0-7 |
| 12 | D11 | PD5 | 86 | Targets 8/10 restore, selectors 8-15 |
| 13 | D12 | PD6 | 87 | Targets 9/11 restore, selectors 0-7 |
| 14 | D13 | PD7 | 88 | Targets 9/11 restore, selectors 8-15 |
| 15 | GND | Board GND | - | Analyzer ground reference |
| 16 | GND | Board GND | - | Second ground reference |

Recommended header label:

```text
BK-B BOARD 2 DIAGNOSTIC
```

## Optional Header: Board 0

Board 0 shares SPI1 with every host board. It uses SPI3 instead of Board 2's
SPI2 and has eight different control GPIOs.

| Position | Analyzer | MCU net | LQFP100 pin | Captured function |
|---:|---|---|---:|---|
| 1 | D0 | PA5 | 30 | SPI1 SCK, shared |
| 2 | D1 | PA7 | 32 | SPI1 MOSI, shared |
| 3 | D2 | PA6 | 31 | SPI1 MISO, shared |
| 4 | D3 | PC10 | 78 | SPI3 SCK, shared with Board 1 |
| 5 | D4 | PC12 | 80 | SPI3 MOSI, shared with Board 1 |
| 6 | D5 | PC11 | 79 | SPI3 MISO, shared with Board 1 |
| 7 | D6 | PE5 | 4 | Targets 0/2 setup, selectors 0-7 |
| 8 | D7 | PE4 | 3 | Targets 0/2 setup, selectors 8-15 |
| 9 | D8 | PE3 | 2 | Targets 1/3 setup, selectors 0-7 |
| 10 | D9 | PE2 | 1 | Targets 1/3 setup, selectors 8-15 |
| 11 | D10 | PE6 | 5 | Targets 0/2 restore, selectors 0-7 |
| 12 | D11 | PC13 | 7 | Targets 0/2 restore, selectors 8-15 |
| 13 | D12 | PC14 | 8 | Targets 1/3 restore, selectors 0-7 |
| 14 | D13 | PC15 | 9 | Targets 1/3 restore, selectors 8-15 |
| 15 | GND | Board GND | - | Analyzer ground reference |
| 16 | GND | Board GND | - | Second ground reference |

Recommended header label:

```text
BK-B BOARD 0 DIAGNOSTIC
```

## Optional Header: Board 1

Board 1 shares both SPI1 and SPI3 with the Board 0 header. Its eight control
GPIOs are unique.

| Position | Analyzer | MCU net | LQFP100 pin | Captured function |
|---:|---|---|---:|---|
| 1 | D0 | PA5 | 30 | SPI1 SCK, shared |
| 2 | D1 | PA7 | 32 | SPI1 MOSI, shared |
| 3 | D2 | PA6 | 31 | SPI1 MISO, shared |
| 4 | D3 | PC10 | 78 | SPI3 SCK, shared with Board 0 |
| 5 | D4 | PC12 | 80 | SPI3 MOSI, shared with Board 0 |
| 6 | D5 | PC11 | 79 | SPI3 MISO, shared with Board 0 |
| 7 | D6 | PE15 | 46 | Targets 4/6 setup, selectors 0-7 |
| 8 | D7 | PE14 | 45 | Targets 4/6 setup, selectors 8-15 |
| 9 | D8 | PE13 | 44 | Targets 5/7 setup, selectors 0-7 |
| 10 | D9 | PE12 | 43 | Targets 5/7 setup, selectors 8-15 |
| 11 | D10 | PC4 | 33 | Targets 4/6 restore, selectors 0-7 |
| 12 | D11 | PC5 | 34 | Targets 4/6 restore, selectors 8-15 |
| 13 | D12 | PB0 | 35 | Targets 5/7 restore, selectors 0-7 |
| 14 | D13 | PB1 | 36 | Targets 5/7 restore, selectors 8-15 |
| 15 | GND | Board GND | - | Analyzer ground reference |
| 16 | GND | Board GND | - | Second ground reference |

Recommended header label:

```text
BK-B BOARD 1 DIAGNOSTIC
```

## Shared Versus New Connections

Installing only the Board 2 header requires 14 signal nets and board ground.

Adding Board 0 requires 11 new unique signal taps:

- three SPI3 signals
- eight Board 0 control GPIOs
- SPI1 and ground are shared with the Board 2 installation

Adding Board 1 after Board 0 requires eight new unique signal taps:

- eight Board 1 control GPIOs
- SPI1, SPI3, and ground are shared with the other headers

Therefore, adding both optional headers after Board 2 requires **19 additional
unique signal connections**. The two physical headers still have 14 signal
contacts each; shared signals must be fanned out to the corresponding contacts.

```text
Unique nets for all three board headers

SPI1                         3
SPI2                         3
SPI3                         3
Board 0 control GPIOs        8
Board 1 control GPIOs        8
Board 2 control GPIOs        8
                            --
Total unique signal nets    33
```

A Logic Pro 16 can capture one 14-signal board header per run. It cannot capture
all 33 unique signals simultaneously.

## Single 2x17 Full-Breakout Header

A single 2x17 header has 34 contacts, so it can expose all 33 unique controller
signals plus one board-ground contact. This is the cleanest permanent option
when access to all three host boards is desired.

The header does not allow a Logic Pro 16 to capture all 33 signals at once. It
only makes every signal accessible. Each test still connects the analyzer to
the 14 signals for one host board.

Use a keyed or clearly marked straight 34-position connector. The table below
uses standard dual-row numbering:

```text
Viewed looking into the socket openings

pin-1 mark
   v
row A:  1   3   5   7   9  11  13  15  17  19  21  23  25  27  29  31  33
row B:  2   4   6   8  10  12  14  16  18  20  22  24  26  28  30  32  34
```

Confirm the selected connector manufacturer's mating-face drawing before
assembly. A rear solder-side view is mirrored.

### Full-breakout pinout

| Header pin | MCU net | LQFP100 pin | Function |
|---:|---|---:|---|
| 1 | PA5 | 30 | SPI1 SCK |
| 2 | PA7 | 32 | SPI1 MOSI |
| 3 | PA6 | 31 | SPI1 MISO |
| 4 | PB13 | 52 | SPI2 SCK |
| 5 | PB15 | 54 | SPI2 MOSI |
| 6 | PB14 | 53 | SPI2 MISO |
| 7 | PC10 | 78 | SPI3 SCK |
| 8 | PC12 | 80 | SPI3 MOSI |
| 9 | PC11 | 79 | SPI3 MISO |
| 10 | PE5 | 4 | Board 0 targets 0/2 setup, selectors 0-7 |
| 11 | PE4 | 3 | Board 0 targets 0/2 setup, selectors 8-15 |
| 12 | PE3 | 2 | Board 0 targets 1/3 setup, selectors 0-7 |
| 13 | PE2 | 1 | Board 0 targets 1/3 setup, selectors 8-15 |
| 14 | PE6 | 5 | Board 0 targets 0/2 restore, selectors 0-7 |
| 15 | PC13 | 7 | Board 0 targets 0/2 restore, selectors 8-15 |
| 16 | PC14 | 8 | Board 0 targets 1/3 restore, selectors 0-7 |
| 17 | PC15 | 9 | Board 0 targets 1/3 restore, selectors 8-15 |
| 18 | PE15 | 46 | Board 1 targets 4/6 setup, selectors 0-7 |
| 19 | PE14 | 45 | Board 1 targets 4/6 setup, selectors 8-15 |
| 20 | PE13 | 44 | Board 1 targets 5/7 setup, selectors 0-7 |
| 21 | PE12 | 43 | Board 1 targets 5/7 setup, selectors 8-15 |
| 22 | PC4 | 33 | Board 1 targets 4/6 restore, selectors 0-7 |
| 23 | PC5 | 34 | Board 1 targets 4/6 restore, selectors 8-15 |
| 24 | PB0 | 35 | Board 1 targets 5/7 restore, selectors 0-7 |
| 25 | PB1 | 36 | Board 1 targets 5/7 restore, selectors 8-15 |
| 26 | PD14 | 61 | Board 2 targets 8/10 setup, selectors 0-7 |
| 27 | PD15 | 62 | Board 2 targets 8/10 setup, selectors 8-15 |
| 28 | PC6 | 63 | Board 2 targets 9/11 setup, selectors 0-7 |
| 29 | PC7 | 64 | Board 2 targets 9/11 setup, selectors 8-15 |
| 30 | PD4 | 85 | Board 2 targets 8/10 restore, selectors 0-7 |
| 31 | PD5 | 86 | Board 2 targets 8/10 restore, selectors 8-15 |
| 32 | PD6 | 87 | Board 2 targets 9/11 restore, selectors 0-7 |
| 33 | PD7 | 88 | Board 2 targets 9/11 restore, selectors 8-15 |
| 34 | Board GND | - | Analyzer ground reference |

### Analyzer hookup from the 2x17 header

| Capture | SPI1 D0-D2 | Secondary SPI D3-D5 | Control D6-D13 |
|---|---|---|---|
| Board 0 | pins 1-3 | pins 7-9, SPI3 | pins 10-17 |
| Board 1 | pins 1-3 | pins 7-9, SPI3 | pins 18-25 |
| Board 2 | pins 1-3 | pins 4-6, SPI2 | pins 26-33 |

Connect a Saleae ground lead to pin 34 or, preferably, to a separate verified
ground point close to the signals being measured. Install at least two external
ground loops near opposite ends of the 2x17 header so the analyzer ground lead
can remain short. These ground loops are additional to the 34 connector pins.

Recommended header label:

```text
BK-B CONTROLLER DIAGNOSTIC - 33 SIGNALS + GND
```

## Compact Two-Header Alternative

Two 2x8 connectors provide 32 contacts. The complete controller breakout has
33 unique signal nets, so moving ground off the connectors is still one signal
position short. Do not silently omit that signal.

The recommended compact installation uses:

- **Connector A:** the complete 14-signal Board 2 capture plus SPI3 SCK/MOSI
- **Connector B:** all 16 Board 0 and Board 1 control signals
- **TP1:** one separate, labeled SPI3 MISO test pin
- **GND1/GND2:** separate verified board-ground test loops or pins

This keeps the primary Board 2 capture entirely on one connector and avoids
duplicating shared SPI nets across multiple headers.

### Connector A - buses and complete Board 2 capture

| Position | MCU net | LQFP100 pin | Function |
|---:|---|---:|---|
| A1 | PA5 | 30 | SPI1 SCK |
| A2 | PA7 | 32 | SPI1 MOSI |
| A3 | PA6 | 31 | SPI1 MISO |
| A4 | PB13 | 52 | SPI2 SCK |
| A5 | PB15 | 54 | SPI2 MOSI |
| A6 | PB14 | 53 | SPI2 MISO |
| A7 | PD14 | 61 | Board 2 targets 8/10 setup, selectors 0-7 |
| A8 | PD15 | 62 | Board 2 targets 8/10 setup, selectors 8-15 |
| A9 | PC6 | 63 | Board 2 targets 9/11 setup, selectors 0-7 |
| A10 | PC7 | 64 | Board 2 targets 9/11 setup, selectors 8-15 |
| A11 | PD4 | 85 | Board 2 targets 8/10 restore, selectors 0-7 |
| A12 | PD5 | 86 | Board 2 targets 8/10 restore, selectors 8-15 |
| A13 | PD6 | 87 | Board 2 targets 9/11 restore, selectors 0-7 |
| A14 | PD7 | 88 | Board 2 targets 9/11 restore, selectors 8-15 |
| A15 | PC10 | 78 | SPI3 SCK |
| A16 | PC12 | 80 | SPI3 MOSI |

### Connector B - Board 0 and Board 1 controls

| Position | MCU net | LQFP100 pin | Function |
|---:|---|---:|---|
| B1 | PE5 | 4 | Board 0 targets 0/2 setup, selectors 0-7 |
| B2 | PE4 | 3 | Board 0 targets 0/2 setup, selectors 8-15 |
| B3 | PE3 | 2 | Board 0 targets 1/3 setup, selectors 0-7 |
| B4 | PE2 | 1 | Board 0 targets 1/3 setup, selectors 8-15 |
| B5 | PE6 | 5 | Board 0 targets 0/2 restore, selectors 0-7 |
| B6 | PC13 | 7 | Board 0 targets 0/2 restore, selectors 8-15 |
| B7 | PC14 | 8 | Board 0 targets 1/3 restore, selectors 0-7 |
| B8 | PC15 | 9 | Board 0 targets 1/3 restore, selectors 8-15 |
| B9 | PE15 | 46 | Board 1 targets 4/6 setup, selectors 0-7 |
| B10 | PE14 | 45 | Board 1 targets 4/6 setup, selectors 8-15 |
| B11 | PE13 | 44 | Board 1 targets 5/7 setup, selectors 0-7 |
| B12 | PE12 | 43 | Board 1 targets 5/7 setup, selectors 8-15 |
| B13 | PC4 | 33 | Board 1 targets 4/6 restore, selectors 0-7 |
| B14 | PC5 | 34 | Board 1 targets 4/6 restore, selectors 8-15 |
| B15 | PB0 | 35 | Board 1 targets 5/7 restore, selectors 0-7 |
| B16 | PB1 | 36 | Board 1 targets 5/7 restore, selectors 8-15 |

### Separate test points

| Test point | MCU net | LQFP100 pin | Function |
|---|---|---:|---|
| TP1 | PC11 | 79 | SPI3 MISO |
| GND1 | Board GND | - | Analyzer ground reference near Connector A |
| GND2 | Board GND | - | Analyzer ground reference near Connector B |

Use a shrouded or clearly marked insulated post for TP1 so it cannot be confused
with ground. GND1 and GND2 must be continuity-verified board ground.

### Analyzer hookup by board

| Capture | D0-D2 | D3-D5 | D6-D13 | Ground |
|---|---|---|---|---|
| Board 2 | A1-A3 | A4-A6 | A7-A14 | GND1 |
| Board 0 | A1-A3 | A15, A16, TP1 | B1-B8 | GND1 or GND2 |
| Board 1 | A1-A3 | A15, A16, TP1 | B9-B16 | GND1 or GND2 |

For every capture, D0-D2 are SCK/MOSI/MISO for SPI1. D3-D5 are
SCK/MOSI/MISO for the board's secondary SPI bus. D6-D13 are that board's eight
setup/restore GPIOs.

The compact arrangement is electrically complete, but Board 0 and Board 1
captures require leads from both connectors plus TP1. The three-header design
is more convenient; the compact design uses fewer connectors and fewer shared
signal stubs.

## Logic Analyzer Connection

Use the channel assignments for the chosen header above. Connect only digital
inputs and a verified board-ground reference; do not drive a signal or attach
an analyzer power output. Make or move connections with the miner powered off.
Check the live logic level against the analyzer's input rating and threshold
before capture.

For each board, D0-D2 are SPI1 SCK/MOSI/MISO and D3-D5 are its secondary SPI
SCK/MOSI/MISO. D6-D13 record the setup/restore GPIOs. Use two SPI decoders and
retain the routing channels to identify the selected target and selector group;
these control signals are not conventional STM32 hardware NSS.

Sample SPI at least four times faster than the observed SCK rate, within the
analyzer's limits at the selected channel count. Start capture before the
clock command or startup sequence. See the
[Saleae sampling guidance](https://www.saleae.com/support/logic-software/capturing-data/what-sample-rate-is-required)
and [SPI decoder guide](https://www.saleae.com/support/protocol-analyzers/analyzer-user-guides/using-spi).

## Oscilloscope Connection and MHz

For a **bus-clock** measurement, connect a suitable high-impedance probe to:

| Signal | MCU net / package pin | 2x8 position | 2x17 pin | Compact header |
|---|---|---:|---:|---|
| SPI1 SCK | PA5 / 30 | 1 on any board header | 1 | A1 |
| SPI2 SCK, Board 2 | PB13 / 52 | 4 on Board 2 | 4 | A4 |
| SPI3 SCK, Boards 0/1 | PC10 / 78 | 4 on Board 0/1 | 7 | A15 |
| Ground reference | Verified board GND | 15 or 16 | 34 | GND1 or GND2 |

Measure the period between adjacent clock edges inside an active SPI burst,
not the idle gap between transactions. For a period in nanoseconds,
`frequency_MHz = 1000 / period_ns`. Record it as **SPI bus MHz**.

Keep the probe's ground connection short and use an appropriately rated probe
and scope. A mains-earthed scope's ground clip must connect only to an
earth-compatible circuit ground, never to a signal or power rail. Do not defeat
the scope's protective earth; use a suitably rated differential or isolated
measurement arrangement when ground compatibility is uncertain.
See [Tektronix probe and grounding guidance](https://www.tek.com/en/documents/whitepaper/abcs-probes-primer).

To measure **ASIC core MHz**, first identify a documented ASIC clock output or
test point and its signal type, voltage, and any divider. No such point or
divider has been verified in this pinout. The STM32 oscillator, SPI SCK, clock
index, bridge echo, and accepted GH/s are not substitutes. An identified
ASIC clock requires a probe and scope with adequate bandwidth and sample rate,
short low-loading connections, and a verified relationship to the core clock.
Until that is established, keep ASIC MHz labeled as an estimate.

## Evidence and Source of Truth

This mapping is based on three independent sources:

1. The official STM32F407 LQFP100 package and alternate-function tables.
2. The retained stock bridge image, md5
   `d5f844be2952fd713717a71a67e2be61`.
3. The stock 12-entry routing table at `0x08003EAC` and unchanged frame path:
   - `0x08001790`: restore table selection
   - `0x080017B4`: setup table selection
   - `0x080018BC`: target table lookup and frame builder
   - `0x08001A64`: stock clock-table sender

The local decoder is in
[`firmware-custom/emulate_smoke.py`](firmware-custom/emulate_smoke.py), and the
firmware analysis is in
[`firmware-custom/README.md`](firmware-custom/README.md).

Some older development notes used internal target numbers as if they were host
board IDs and consequently described the bus routing incorrectly. This document
supersedes those historical shorthand descriptions for logic-analyzer wiring.
