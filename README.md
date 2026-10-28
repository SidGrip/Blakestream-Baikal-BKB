# Blakestream-GaintB v2.2

Custom Orange Pi controller software for the **Baikal BK-B Multi (Giant B)**,
with per-board pool routing, same-algorithm failover, adjustable Index controls,
accepted-work monitoring, and a light/dark Scripta dashboard.

Website: [Blakestream](https://blakestream.io/)

**[Download Blakestream-GaintB-v2.2.img.xz](https://github.com/SidGrip/Blakestream-Baikal-BKB/releases/download/v2.2/Blakestream-GaintB-v2.2.img.xz)**

Controller release **V2.2** and optional STM32 bridge firmware **V1** have
separate version numbers. Writing the SD image does not flash the bridge.
The image starts without configured pools; add your own pool and worker.

- [Installation](#install)
- [Dashboard and settings](#using-the-dashboard)
- [Adjustable clocks](#adjustable-clocks)
- [Technical guide](TECHNICAL-GUIDE.md)
- [Build instructions](BUILD.md) and [QA coverage](QA.md)

## Use at your own risk

This is independent community software for aging hardware. It is not endorsed
or supported by Baikal Miner and comes with **no warranty**. Flashing, changing
clocks, overheating, power interruptions, or software faults can cause data loss,
hardware damage, or an unbootable device. Temperature controls and rollback
mechanisms reduce some risks; they cannot guarantee recovery or electrical safety.

Keep a known-good factory [PiZero_GB_180105_V1.0.img](https://github.com/SidGrip/Blakestream-Baikal-BKB/releases/download/stock-180105-v1.0/PiZero_GB_180105_V1.0.img.xz), a spare SD card, and a
recovery plan before making changes. An SD rollback does not undo an STM32
bridge flash. See [Bridge firmware and recovery](#bridge-firmware-and-recovery).
Short lab tests are not long-term hardware qualification or guaranteed mining
performance. Mining costs and returns remain your responsibility.

This image inherits an old controller OS and known factory credentials. Keep
the dashboard, SSH, and miner API on a trusted network, not exposed to the
Internet. Automatic software updates have been removed; updates are manual.
The project is licensed under [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html),
including its warranty disclaimer and limitations of liability.

## Features

- **Per-board pools and failover:** assign each board a primary and optional
  same-algorithm backup pool; idle boards without silently refilling their work.
- **Adjustable Clocks & Fan:** Global or Individual Index selection, compact Default buttons,
  read-only estimated GH/s and MHz, shared temperature limits, and fan controls.
- **Measured work:** separate modeled Estimate from Accepted 15m/1h rates,
  with per-board graphs and share/error counters. Index tests keep graphs visible.
- **Network settings:** Dynamic (DHCP) or Static IPv4 with timed confirmation
  and automatic rollback of unconfirmed changes.
- **Application backups:** settings, pools, clock profiles, baselines, personal
  measurements, and chart/event history, with import/export and guarded restore.
- **Diagnostics:** board health, logs, measured rates, pool network probes,
  recorder controls, and guarded firmware tools under Dev Tools.
- **Patched sgminer:** fixes for upstream debug-buffer leaks, board routing,
  clock coordination, and diagnostics. These fixes do not guarantee freedom
  from other memory-pressure or runtime faults.

## Hardware

The BK-B Multi has three BKLU hash boards behind one STM32F407 USB bridge.
One sgminer process sees logical boards **0 / 1 / 2**. The controller is an
Orange Pi Zero-class SBC using the factory Armbian/Ubuntu 16.04 armhf base.
The factory kernel and bootloader are retained; the SD overlay changes userspace.

The hardware supports Blake256R14, Blake256R8, Blake2B, Lbry, and Pascal.
The bundled clock-performance examples are for **`blake256r8` only**. They
must not be reused as measured results for other algorithms.

## Install

### 1. Download

Direct download: **[Blakestream-GaintB-v2.2.img.xz](https://github.com/SidGrip/Blakestream-Baikal-BKB/releases/download/v2.2/Blakestream-GaintB-v2.2.img.xz)**.

Before flashing, compare the file's SHA-256 with the
[checksum supplied with the v2.2 release](https://github.com/SidGrip/Blakestream-Baikal-BKB/releases/download/v2.2/Blakestream-GaintB-v2.2.img.xz.sha256):

```bash
sha256sum Blakestream-GaintB-v2.2.img.xz
```

Computing a hash alone does not verify the file; compare it with the expected
release hash. Source and build details are in [BUILD.md](BUILD.md).

### 2. Flash

You need an SD card of **at least 8 GB** and a card reader.

**Option A — [BalenaEtcher](https://etcher.balena.io/) / [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (graphical)**

- In Etcher, choose **Flash from file**, select
  `Blakestream-GaintB-v2.2.img.xz`, select the SD card, then **Flash**.
- In Raspberry Pi Imager, choose **Use Custom**, select the downloaded image,
  select the SD card, and write it. Skip Raspberry Pi OS customization; configure
  the BK-B through its own dashboard after boot.
- If your writer does not accept `.xz`, decompress the file first. Wait for the
  writer's verification to finish, then safely eject the card.

**Option B — `dd` on Linux (command line)**

Identify the SD card with `lsblk` and unmount its mounted partitions first.
**The following command erases the entire target device. Verify the device path,
not a partition path, before running it.**

```bash
xz -dk Blakestream-GaintB-v2.2.img.xz
sudo dd if=Blakestream-GaintB-v2.2.img of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

Replace `/dev/sdX` with the SD card's actual device. Never use a system or backup
drive as the target. Safely eject the card after writing completes.

### 3. Boot

1. With the BK-B powered off, insert the flashed SD card into its Orange Pi
   controller slot.
2. Connect Ethernet and power on the BK-B. Allow it to boot and obtain a DHCP
   lease; first-boot firmware detection and identity generation can take longer
   than a normal restart.
3. Find hostname `blakestream-gaintb` in your router's DHCP leases, or use
   `arp -a` from another computer on the same LAN to locate its address.

Writing this SD image does not flash the STM32 bridge. Keep Ethernet connected
for initial setup; a fresh image starts with Wi-Fi off.

### 4. First login & setup

1. Browse to `https://<device-ip>/` on your trusted LAN. The controller creates
   a unique self-signed certificate on first boot, so the browser cannot
   automatically verify the miner's identity and displays a warning. HTTPS still
   encrypts passwords and dashboard traffic, making it safer than plain HTTP even
   with that warning. Verify the certificate on first connection; removing the
   warning requires a trusted certificate matching the hostname or IP you use.
   Port 80 redirects to HTTPS and never serves the dashboard.
2. Log in with **`baikal`**, the factory default web password.
3. Change the web password under **Settings > Scripta > Web password**, using
   **Save password**.
4. Change the Linux password through SSH or the dashboard's **Terminal** tab:
   log in as `root`, then run `passwd`. The browser terminal uses the dashboard's
   HTTPS connection and asks for your Linux SSH username and password. Its backend
   listens only on localhost; port 4200 is not exposed to the LAN.
5. Under **Settings > Scripta**, choose the display timezone and **Save Settings**.
   The controller's system clock, logs, and scheduled jobs remain in UTC.
6. Under **Miner > Pools**, add the pool URL (`stratum+tcp://<host>:<port>`), worker,
   password, and algorithm (`blake256r8` for Blakecoin), then **Save Pool**.
7. On **Status**, assign each board (0/1/2) its primary pool and click
   **Apply & restart**. All boards can share a pool or use different configured
   pools.
8. Check startup chip counts, temperatures, and advancing accepted shares.
   Accepted-rate displays need at least five observed minutes and sufficient
   accepted work after settling. They are not instant clock measurements.

The image inherits the factory credentials:

| Service | Username | Default password |
| --- | --- | --- |
| Web UI | Password-only login | `baikal` |
| SSH / browser Terminal | `root` | `baikal` |

The web password is separate from Linux credentials. The legacy PHP/Expect
password helper is not shipped; use `passwd` through SSH or Terminal. Run it as
the account whose password you intend to change, or use the appropriate
administrator command for another account. Changing the web password does not
secure every OS account. Do not leave factory credentials in use.

### 5. (Optional) Failover — give each board a backup pool

Failover is per-board: each board has one primary and an optional same-algorithm
backup pool. It switches to the backup when the primary is unavailable and
returns when the primary recovers.

1. Under **Miner > Pools**, add at least two pools using the same algorithm.
2. On **Status**, switch the pool-column selector from **MAIN POOL** to
   **FAILOVER**.
3. Choose a backup for each board, then **Apply & restart**. Leave its backup
   unset if you do not want one.

The selector only offers pools matching that board's primary algorithm. If
neither configured route is usable, the board stops hashing. Check board
failover indicators and **Failover History** rather than treating a pool's
connection status as proof that a particular board is using it.

### 6. Rolling back

Export your application settings before replacing the SD card. Power off the
BK-B, remove the card, and use a card reader to write a known-good
[PiZero_GB_180105_V1.0.img](https://github.com/SidGrip/Blakestream-Baikal-BKB/releases/download/stock-180105-v1.0/PiZero_GB_180105_V1.0.img.xz) factory image with the same image-writer steps. Insert
the card into the controller again, then power on the miner.

An SD rollback replaces the controller software only. If you flashed custom
STM32 bridge firmware, rewriting the SD card does **not** restore that bridge to
stock. Use the separate guarded **Revert to Stock** operation while the controller
is working, or follow [Bridge firmware and recovery](#bridge-firmware-and-recovery).

## Using the dashboard

**Miner** has **Pools / Clocks & Fan / SGMINER** submenus. **Settings** has
**Scripta / Mining / Network / Wi-Fi**; remote-control settings remain under Mining.
Opening Miner starts at Pools; opening Settings starts at Scripta.
Switching these submenus does not save or discard the current form draft.
Explicitly reloading settings or leaving the page can discard unsaved edits.

**Status:** per-board charts, pool assignments, chip counts, temperatures, and
mining counters. Click **Clock / Index** to change that column's display. Estimate
is distinct from measured Accepted 15m/1h. A partial-window tooltip reports how
much observed time contributed to the rate. Rejected and HW Errors show counts,
with percentages on hover or keyboard focus. The theme toggle is in the header.
Dark mode is the first-visit default; a saved light-mode choice is retained.
The icon beside Logout identifies the miner's default-route connection as
Ethernet or Wi-Fi; it is not an Internet or pool-health test.

**History:** Pool History, Failover History, and Clock History retain their
separate views. Graph interval and point-count controls are under Settings >
Scripta.

**Network:** Settings > Network shows the current mode, address/prefix, gateway,
and DNS. Dynamic (DHCP) is the initial choice without a saved profile; a router
reservation is still DHCP. Static exposes editable address, prefix, gateway,
and DNS fields. After **Apply Network Settings**, reconnect as needed and choose
**Keep Settings** within three minutes, or **Revert**. Unconfirmed changes roll
back automatically. Logging in does not confirm a change; a pending transaction
redirects a successful login to Network for explicit confirmation.

**Wi-Fi:** Settings > Wi-Fi has a radio **Off / On** control, network scanning,
manual SSID entry, and saved-network removal. A fresh image starts **Off**;
the selected radio state survives reboot. Open and WPA/WPA2-PSK networks are
supported. Wi-Fi settings do not change the
wired connection. Wireless passwords are not included in application backups.

**Thermal protection:** Clocks & Fan has shared **Recover Temp / Cutoff Temp**
defaults of **40 / 55 C**, applied through the miner's temperature controls. A separate host
watchdog polls every 30 seconds, with defaults of disable at **80 C** and recover
at **70 C** in `/opt/scripta/etc/temp-config.json`. It changes the board mask and
restarts the miner, temporarily interrupting the other boards too. The two sets
of limits are not the same setting; changing the clock form does not edit the
watchdog file. Neither mechanism replaces working cooling or operator monitoring.

**Dev Tools:** enable it under Settings > Scripta. In Logs, use the Measured Rates
row's **view** action for personal measurements; viewing another log returns to
the text-log viewer. Measured Rates defaults to RAM, with optional SD storage;
both use a 2 MiB database limit. The Board selector filters All, 0, 1, or 2.
Download report exports a readable text table; JSON exports the summary. Both
downloads follow the selected board. The history list scrolls within a bounded
area. Write buffer commits pending measurements to the selected storage, not a
download. RAM history is lost on reboot. Board replaced is beside each board
heading under **Dev Tools > Boards** and requires confirmation before clearing
that board's measured history.

### Backups

On **Backup**, enter a name and click **Backup**, or use **Export current
configuration** for a download. Saved archives can be restored, exported,
imported, renamed, or deleted.

Backups include application settings, the web password hash, pools and assignments,
clock profiles, fan/temperature settings, public and personal baseline data,
measured rates, and supported chart/event history. **Keep exports private:**
they contain credentials. Export a copy off the miner before replacing its SD card.

This is an application backup, not a disk image. It excludes OS identity,
firmware binaries and verification records, raw diagnostic logs, and pending
network changes. Restore restarts the miner after success. Restored network data
is staged for **Settings > Network > Review Restored Network**, not applied
automatically. Review, Apply, and Keep Settings remain separate steps.
See [Application Backups](TECHNICAL-GUIDE.md#application-backups) for limits and
recovery behavior.

## Adjustable clocks

Enable **Adjustable Clocks** under Settings > Scripta to use the guarded custom
bridge path, then open **Miner > Clocks & Fan**. Only **Index** is editable.
**Est. GH/s** and **Est. MHz** are read-only previews beside it.

- **Global:** one Index selection for all three boards. Estimated GH/s is the
  combined total, with a per-board average; it is not a measured all-board result.
- **Individual:** a separate Index input and estimated GH/s/MHz for each board.
- **Default:** stages **Index 30 / nominal 400 MHz** for that scope. It does not
  apply until **Save Clock Settings** is confirmed.
- **Supported input range:** Index **8..32** on verified V1 firmware.

Non-default selections use the raw-index path. Non-default Global sends the
same setting through individual board commands; it does not use the separate
bridge-global transport. Changing a draft or selecting Default does not change
the running clock. The save button's amber pulse marks unsaved clock changes.

The bundled `blake256r8` examples cover Index 8..32 with a completed one-hour
measurement on all three boards at each index, plus opening and closing
Index 30 references. The baseline table includes average whole-miner watts.
This is limited screening, not qualification
of every board. Compatible examples are shared across the UI, not represented
as measurements of the user's hardware. Missing compatible data shows
**Unavailable**. Personal measurement history is stored separately.

**MHz estimates are performance-equivalent, not measured frequency.** The
nominal 400 MHz default is an anchor, not independent frequency verification.
Accepted work, errors, temperature, and operating context are recorded; these
short trials do not establish a universal efficiency ranking, long-term stability, or safe
overclocking. See the [technical guide](TECHNICAL-GUIDE.md#linked-clock-controls)
for calculation, provenance, and validation limits, or the
[Index baseline table](CLOCK-BASELINE.md) for the recorded GH/s and estimated MHz.

### Bridge firmware and recovery

The SD image and STM32 firmware are separate:

| Component | Update method | Recovery |
| --- | --- | --- |
| Orange Pi controller | Write the SD image | Rewrite a known-good SD image |
| STM32F407 USB bridge | Explicit, confirmed DFU flash | Guarded stock revert or manual ROM DFU recovery |

**On the first boot of a freshly written SD image, a read-only, double-read
check identifies the installed bridge before the web UI and miner watchdog
start.** Verified current Custom V1 enables Adjustable Clocks at the normal
400 MHz / Index 30 default. Stock leaves Adjustable Clocks disabled. Auto Fan
and Dev Tools retain their factory defaults. This check never flashes firmware.
Completed checks do not repeat on later boots, so saved settings are preserved.
Unknown firmware or an ordinary failed check leaves clocks disabled; the result
is retained in `/var/lib/blakestream-firstboot/result.json` and the read log in
`/var/log/blakestream-firstboot/firmware-firstboot.log`. Failed first-boot
GPIO/application cleanup retains its pending and boot-local recovery state.
A separate persistent firmware-write recovery marker prevents mining and
requires a successful DFU hold before cron starts.

First-time custom bridge installation requires a confirmed flash. Subsequent
Adjustable Clocks enable/disable changes are software-only; **Revert to Stock**
is a separate flash operation. Guards take a pre-flash backup, verify readback,
and attempt rollback on mismatch. They cannot guarantee recovery from a power
failure or hardware fault.

Allow about **170 seconds** for a flash, including safety backups, writing,
readback verification, and restarting mining. Dev Tools shows elapsed and
estimated total time, starting with a **164-second lab baseline** and learning
separate estimates from successful stock and V1 flashes on your miner.
The estimate is not a timeout; **do not power off** while
the operation is running.

Writes target application flash, not the ROM bootloader or option bytes.
Manual recovery may require access to BOOT0/NRST and working USB hardware.
Read [the flashing and recovery guide](firmware-custom/flash/README.md) before
flashing. Replacing the SD image alone does **not** restore stock bridge firmware.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Miner DOWN | Inspect launcher/miner logs and pool DNS before restarting; repeated failures need diagnosis, not repeated blind restarts. |
| One board has no accepted work | Check enabled state, chip counts, temperature, pool assignment, and primary/backup availability. |
| Accepted rate or Index-test graph is empty | Allow settling and at least five observed minutes; check recorder status, API availability, and context changes under Dev Tools. |
| Cannot find the controller | Check Ethernet, router DHCP leases, subnet, and any pending static-change rollback. |
| Forgotten web password | Use an existing authorized SSH session/account or a known-good recovery backup; do not assume factory credentials still work. |

## Source and rebuilding

This repository contains the controller/web overlay, bundled upstream host-miner
source, a numbered patch series, and custom bridge patch source. See
[BUILD.md](BUILD.md) for prerequisites, image assembly, and separate host/bridge
build recipes, and [QA.md](QA.md) for tests and hardware-validation limits.
The [scripts index](scripts/README.md) lists the available tools. The
[Technical Guide](TECHNICAL-GUIDE.md) covers controller behavior and recovery.

The host miner is based on
[cod3gen/sgminer-baikal](https://github.com/cod3gen/sgminer-baikal), pinned to
`3cde8deca53f83ac65bc685b988166bd17930753` in
[UPSTREAM-PIN](sgminer-build/UPSTREAM-PIN), with 36 applied patches. The complete
upstream source is included as a verified [Git bundle](sgminer-build/vendor/README.md)
for offline source preparation. Build tools, packages, and the factory SD image
are separate prerequisites.

The inherited version string `sgminer 5.6.2-BK_cod3gen_MOD` does not mean the
Blakestream patches are absent. Build provenance is documented in
[BUILD-SOURCE.md](sgminer-build/BUILD-SOURCE.md). Image assembly copies the current
overlay, including its supplied binaries; it does not rebuild sgminer or STM32
firmware automatically. Hardware signals are documented in the
[logic-analyzer pinout](BKB-LOGIC-ANALYZER-PINOUT.md).

## Licensing and attribution

This project incorporates and modifies GPLv3 software:

- **Scripta** by **Lateral Factory**: https://www.latera.lt/scripta/
- **sgminer-baikal**, the cod3gen community host-miner baseline:
  https://github.com/cod3gen/sgminer-baikal
- **Baikal multi-algo-miner** controller software:
  https://github.com/baikalminer/multi-algo-miner

Modifications are released under **GPLv3**. Scripta/controller changes are in
`overlay/var/www/` and `overlay/opt/scripta/`; sgminer changes are in
`sgminer-build/patches/`. Preserve upstream license and copyright notices.
This is an independent project, not affiliated with or endorsed by those authors
or organizations.

Thanks to [BlueDragon747](https://github.com/BlueDragon747) for his help with this project.
