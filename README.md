# Blakestream-GaintB v2.0

Custom controller firmware for the **Baikal BK-B Multi** ("Giant B") ASIC miner — a fork of Baikal's factory image, rebranded for Blakestream and substantially extended with a patched mining client, true per-board pool routing, automatic same-algo failover, hardened launcher, and a security-audited Scripta UI.

If you flash this image onto a BK-B's Orange Pi controller, you get a miner that boots up Blakestream-branded with no pre-set pools — you add your own through the web UI on first login. The dashboard lets you do things the factory firmware never could, like pinning specific hash boards to specific pools and seeing per-board hashrate over time.

---

## ⚠️ Use at your own risk

**This is community-built firmware for a discontinued ASIC. There is no warranty, no support contract, and no vendor to fall back on.** By flashing it, you accept all responsibility for what happens to your hardware and any mining you do with it.

Specifically, you should understand and accept that:

- **The Baikal BK-B Multi is end-of-life.** Baikal Miner shut down in 2018. Replacement parts, official support, and original firmware downloads are no longer available from the manufacturer.
- **You will void any remaining warranty** by flashing third-party firmware. There almost certainly isn't one on a 2018 ASIC, but be explicit: this firmware is not endorsed, signed, or supported by Baikal Miner or any successor.
- **Bricking is possible.** A bad flash, a power cut mid-write, or an unforeseen bug can leave the controller unbootable. Recovery means writing the factory `.img` back to the SD card. **Keep a known-good copy of the original `PiZero_GB_180105_V1.0.img` before you start.**
- **Hashing hardware can fail.** The BKLU boards are 8 years old. Heat, capacitor wear, and PSU stress are real. The temperature watchdog included here helps, but it is no substitute for monitoring your rig and a smoke/fire-safe deployment environment.
- **Mining is financially risky.** Pool downtime, network forks, electricity costs, coin price moves, and pool-side disputes can all reduce or wipe out earnings. None of the changes in this firmware mitigate those risks — they only change *how* the miner is configured.
- **The new code paths in v2 are recent.** Per-ASC routing, the v2 applier, and the failover policy have been validated on a single live device but have not had months of production soak time. You may hit edge cases. If anything feels wrong, roll back to factory firmware and report what you saw.
- **No telemetry, no auto-update.** This firmware does not phone home and does not pull updates. You are responsible for tracking releases manually if you want fixes.

If any of that is unacceptable for your situation — or you don't have a way to recover a bricked Orange Pi — **don't flash this image.** Stick with the factory firmware.

By using this image you agree that the authors and contributors of this project are not liable for any damages, lost coins, hardware failure, electrical hazard, or other consequences of running it.

This project is licensed under **GPLv3**. The licence ([full text](https://www.gnu.org/licenses/gpl-3.0.html)) explicitly states there is **no warranty**:

> THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM "AS IS" WITHOUT WARRANTY OF ANY KIND.

---

## 🔑 Default credentials (CHANGE IMMEDIATELY)

This image keeps the **factory Baikal default credentials** unchanged. They are well-known and trivially scannable on any network. Anyone with LAN access to a freshly-flashed device can log in as root.

| Service | Username | Default password | How to change |
|---|---|---|---|
| Web UI (Scripta) | n/a | **`baikal`** | Settings tab → "Web password" field → click **Save password** |
| SSH | `root` | **`baikal`** | SSH in once and run `passwd`. (You can also do this from the Terminal tab inside the dashboard.) |

**On a fresh flash, the very first thing you should do is change BOTH passwords**, before connecting the device to any network you don't fully control. If you skip this step, treat the device as compromised by default.

We chose to keep the factory defaults rather than rotate them so:
- Documentation stays simple (no per-image generated password to look up).
- Existing Baikal users get an experience they recognise.
- There's no false sense of security from a "different but still hardcoded" password.

---

## What's different vs the original Baikal firmware

|  | Original Baikal firmware | Blakestream-GaintB v2.0 |
|---|---|---|
| **Out of the box** | Pre-set for old Baikal default pools (Decred, Sia, LBRY, Pascal, Vcash) | Starts empty — you add your own Blakecoin pool and worker name on first login |
| **Mine each of the 3 boards to a different pool** | No — all 3 boards share one pool | Yes — pick any pool per board |
| **Backup pool if your main pool dies** | No — it just keeps retrying the dead pool forever | Yes — pick a backup per board, automatic switch when your main pool dies, automatic switch back when it recovers |
| **Idle button actually stops the board** | No — the board kept running quietly underneath | Yes |
| **Auto-shutdown if a board overheats** | No | Yes — turns off above 80 °C, turns back on once it cools to 70 °C |
| **Live hashrate graph per board** | One chart for all 3 boards combined | One chart per board, with Hour / Day / Week views |
| **Pool performance history** | No | Yes — accepted vs rejected shares per pool over the last hour, day, or week |
| **Save/restore your settings** | Read-only; can't remove the junk presets it ships with | Name, rename, delete backups; junk presets hidden |
| **Light & dark theme** | Baikal blue/grey only | Toggle in the header, choice remembered in your browser |
| **Built-in auto-updater that could overwrite files** | Present | Removed for safety |

---

## Hardware

The **BK-B Multi** is a discontinued multi-algorithm ASIC supporting Blake256R14, **Blake256R8** (what Blakecoin uses), Blake2B, Lbry, and Pascal. It carries 3 BKLU hash boards exposed through a single STM32F407 USB bridge — meaning **one** sgminer instance sees all 3 boards as ASCs `0`/`1`/`2`.

The controller is an **Orange Pi Zero**-class SBC running Armbian/Ubuntu 16.04 (armhf, kernel 3.4.39 Allwinner sun8i). Software stack:

- **Scripta** — web management UI by Lateral Factory (GPLv3)
- **sgminer-baikal** — fork of sgminer with the Baikal ASIC USB driver
- **lighttpd + PHP-FastCGI** — web server, docroot `/var/www/`

The factory kernel, ASIC USB drivers, and bootloader are unchanged; everything we modify is in userspace.

---

## Install

### 1. Download

Project info & download links: **https://explorer.blakestream.io/baikal-bkb**

Direct download (~900 MB compressed):

```
https://bootstrap.blakestream.io/firmware/Blakestream-GaintB-v2.0.img.xz
```

Most flashing tools (BalenaEtcher, Raspberry Pi Imager) accept the `.xz` directly without manual decompression. If you want to verify before flashing:

```
sha256sum Blakestream-GaintB-v2.0.img.xz
# compare against the published sha listed on explorer.blakestream.io/baikal-bkb
```

### 2. Flash

You need an SD card **≥ 8 GB** and a card reader.

**Option A — BalenaEtcher / Raspberry Pi Imager (graphical, easiest)**
- Open Etcher → "Flash from file" → pick `Blakestream-GaintB-v2.0.img.xz` → select SD card → Flash. Etcher handles `.xz` decompression automatically.

**Option B — `dd` on Linux/macOS (command line)**
```
xz -d Blakestream-GaintB-v2.0.img.xz
sudo dd if=Blakestream-GaintB-v2.0.img of=/dev/sdX bs=4M status=progress conv=fsync
sync
```
Replace `/dev/sdX` with your actual SD card device (`lsblk` to find it). Triple-check the device path before running — `dd` will silently overwrite anything you point it at.

### 3. Boot

1. Insert the flashed SD card into the BK-B's Orange Pi controller slot.
2. Power on the BK-B (PSU + ASIC power).
3. Wait ~60 seconds for the device to come up and grab a DHCP lease.
4. Find its IP on your LAN — check your router's DHCP leases page, or run `arp -a` / `nmap -sn 192.168.1.0/24`. The device's hostname is `blakestream-gaintb`.

### 4. First login & setup

1. Browse to `http://<device-ip>/`.
2. Log in with **`baikal`** (factory default).
3. **Change the password immediately** — Settings tab → Web password fields → Save password.
4. **Change the SSH password** — Terminal tab (inside the dashboard) → run `passwd` → set a new one.
5. Set your **timezone** — Settings tab → Timezone dropdown → click Update. This drives both the device clock and every timestamp shown on the dashboard.
6. Add your **pool** — Miner tab → "Add Pool" → fill in the URL (`stratum+tcp://<host>:<port>`), worker name, password, algorithm (`blake256r8` for Blakecoin). Save.
7. Assign **boards to the pool** — Status tab → for each board (0/1/2), pick the pool from the dropdown → Apply. You can pin all three to the same pool, or mix-and-match across multiple pools you've added.
8. Watch the per-board hashrate cards on the Status tab — within a couple of minutes you should see ~60 GH/s per active board.

### 5. (Optional) Failover

To enable same-algo automatic failover: add at least 2 pools in the same algorithm category. If a pinned pool dies, the affected board reroutes to the least-loaded same-algo pool with the lowest priority. When the primary recovers, the board auto-restores. The dashboard's `Failover Active` column shows you when a board is on a backup.

### 6. Rolling back

If anything breaks, write the original `PiZero_GB_180105_V1.0.img` factory image back to the SD card with the same `dd`/Etcher steps. Power-cycle the BK-B. You're back to stock.

---

## Using the dashboard day to day

**Live hashrate per board** — the Status tab shows three cards, one per board. Click Hour / Day / Week to change the time range. Numbers update every minute. If the charts are slowing your browser down you can turn them off in Settings → "Charts" (the miner keeps running and recording in the background).

**Heat protection** — if a board climbs over 80 °C the firmware shuts it off automatically and a red bar appears in the dashboard. It comes back on once it cools to 70 °C. (Thresholds are not currently adjustable.)

**Pool History tab** — see how each pool has performed (accepted vs rejected shares) over the last hour, day, or week.

**Restart the miner** — Status tab → click **restart miner**. Use this after changing pool assignments, or if anything looks stuck.

**Backups** — Backup tab → type a name → click Save. This snapshots all your saved pools, board assignments, and Scripta settings. One-click restore. Take one before making changes you might want to undo.

---

## If something goes wrong

| What you see | Most likely cause | What to do |
|---|---|---|
| Dashboard says **Miner DOWN** but boards feel warm | The mining process died or hasn't started yet | Click **restart miner** on the Status tab. If still down after a minute, reboot the device. |
| One board shows **0 GH/s** | The board overheated and got auto-disabled, OR its pool (and any backup) is unreachable | Check the temperature column. Check the pool is online. Try assigning that board to a different pool. |
| **Charts flat at zero** | Just booted (no data collected yet), or charts are turned off in Settings | Give it 5 minutes after boot. Check Settings → Charts is on. |
| **Web UI password lost** | n/a | Log in over SSH (`ssh root@<device-ip>`, password `baikal` if never changed) and reset it. Open a terminal and follow the comments in `/opt/scripta/etc/uipasswd`. |
| **Can't find the device on the network** | DHCP didn't give it an address yet, or you're on the wrong subnet | Wait a minute after power-on. Check your router's DHCP leases page for hostname `blakestream-gaintb`. |

---

## Source code & rebuilding from source

This firmware is GPLv3. Full source for every modified component is in the public repository alongside this README. If you want to rebuild the image yourself (verify the binary, modify it, port to a different Baikal model), the build steps and source layout live in [`BUILD.md`](BUILD.md).

In short: clone the repo, fetch the upstream `sgminer-baikal` source, apply the numbered patches in `sgminer-build/patches/`, then run the `01-fetch.sh` … `06-compress.sh` script chain. You'll need a Linux host with `losetup`, `xz`, `rsync`, and `sudo`.

---

## Licensing & attribution

This project incorporates and modifies the following GPLv3 software, with full source preserved:

- **Scripta** by **Lateral Factory** — https://www.latera.lt/scripta/
- **sgminer-baikal** — https://github.com/baikalminer (and community forks)
- **Baikal multi-algo-miner** Orange Pi controller bits — https://github.com/baikalminer/multi-algo-miner

All modifications are released under **GPLv3**. Modified Scripta files are in `overlay/var/www/` and `overlay/opt/scripta/`. sgminer modifications are in `sgminer-build/patches/` as a numbered patch series. Original `LICENSE` files are preserved everywhere.

This is an independent community project. Not affiliated with or endorsed by Lateral Factory, Baikal Miner, or any successor entity.
