# Building Blakestream-GaintB V2.2

The public release is V2.2. Bridge firmware v1 is the experimental STM32
clock patch shipped inside that release, not a separate product version.
Architecture, what the bridge echo does and does not prove, and the hardware
checks that are still open are in [TECHNICAL-GUIDE.md](TECHNICAL-GUIDE.md).
Day-to-day tests are in [QA.md](QA.md). The sgminer checkout and ARM build are
in [sgminer-build/BUILD-SOURCE.md](sgminer-build/BUILD-SOURCE.md).

Scripts locate the repository from their own path. The clone can live in any
directory, including one with spaces, and the commands below can be run from
any working directory.

## Default QA

No root, no network, no miner, no stock STM32 dump, and no sgminer source tree:

```bash
scripts/qa
```

That runs the PHP, Node, and Python contract tests and the Unicorn v1
emulator against the tracked image. Missing `php`, `node`, `python3`, or the
`unicorn` module fails the run. The runner does not install them. Optional
native, driver, firmware-rebuild, and ARM phases are listed as not run. Ask
for them by name when you have their inputs; a missing input then fails
instead of being skipped. See [QA.md](QA.md).

## What is already in the tree

| Path | Role |
| --- | --- |
| `overlay/` | Files copied over the factory rootfs. The image build uses this tree as it is. |
| `sgminer-build/sgminer.patched` and `overlay/opt/scripta/bin/sgminer` | Reference ARMv7 host binary, sha256 `a42166a885922776533ac8a9051104e8758489dbecd791a8cb297840b7c97a4a`. This build includes the restricted B-group API verbs from patches 0036–0037. Do not replace it with a local rebuild unless you are publishing a new binary. |
| `sgminer-build/patches/` | 36 numbered patches, `0001`–`0002` and `0004`–`0037`, on upstream `3cde8deca53f83ac65bc685b988166bd17930753`. `patches/rejected/0003` is not applied. |
| `sgminer-build/vendor/sgminer-baikal.bundle` | Complete pinned upstream source and original history, restored offline after SHA-256 verification. No upstream GitHub checkout is required. |
| `firmware-custom/build/bkb-stm32f407-flash-clockmod-v1.bin` | Reference v1 image, sha256 `d2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a`. |
| `overlay/opt/scripta/firmware/stock.bin` | Included stock rollback image, also usable as the bridge build input. Identity is in `firmware-custom/STOCK-BRIDGE.txt`. |
| `firmware-dump/bkb-stm32f407-flash.bin` | Optional gitignored private stock bridge dump. |
| `firmware-original/`, `firmware-modified/rootfs/`, `build/` | Gitignored factory image, rootfs snapshot, and SD-image output. |

## Rebuild sgminer

Source preparation uses the included Git bundle, not the upstream URL.
It fails on a missing or corrupt bundle without trying the network.

```bash
scripts/qa source-baseline
sgminer-build/fetch-baseline.sh --src /path/to/sgminer-src
sgminer-build/apply-patches.sh --src /path/to/sgminer-src
BKB_SGMINER_SRC=/path/to/sgminer-src scripts/qa native native-tsan driver
```

Set `BKB_SGMINER_SRC` instead of `--src` if you prefer. Host `configure` /
`make` and the Ubuntu 16.04 ARMv7 container build are separate commands in
[sgminer-build/BUILD-SOURCE.md](sgminer-build/BUILD-SOURCE.md). They do not
pull container images unless you pass `--pull` to the ARM script. The ARM
container may still download missing build packages. These commands do not
copy the new binary over the reference.

## Rebuild the v1 bridge image

Use the included stock rollback image, or supply a verified stock dump.
The script checks size, md5, and sha256, then requires `gcc-arm-none-eabi`
and friends on `PATH`. It does not download the dump or install the compiler.

```bash
firmware-custom/build.sh --dump overlay/opt/scripta/firmware/stock.bin \
    --out-dir /path/to/firmware-out v1
```

`--check-dump` stops after the identity check. The reference image was built
with `gcc-arm-none-eabi` 13.2. `scripts/qa firmware-build` writes to
`sgminer-build/build/firmware-rebuild` (or `BKB_FIRMWARE_OUT`) and fails if
the bytes differ from the tracked v1 image. It does not replace that image.

Emulator checks do not need the dump:

```bash
python3 firmware-custom/smoke_assert_v1.py
```

## SD-card image

This path is optional, uses the network and root, and is not part of QA.
Tested mechanics are Linux `losetup` (Ubuntu 22.04 / 24.04). You need roughly
16 GB free, plus `bash`, `rsync`, `losetup`, `mount`, `partprobe`, `udevadm`,
`blkid`, `xz`, `sha256sum`, `curl`, Python 3, `depmod` (kmod), `fsck.fat` (dosfstools), and
`sunxi-fexc` from sunxi-tools 1.4.2 or newer.

`01-fetch.sh` downloads the factory `PiZero_GB_180105_V1.0.img` (about 3.9 GB)
to `firmware-original/` or to `BKB_FACTORY_IMG`. Mount points default to
`/mnt/baikal-boot-ro`, `/mnt/baikal-ro`, and `/mnt/baikal-rw`. The fetcher
requires both the expected byte count and SHA-256
`de3b514d890c9581d1fe22e90574ae534c2d19d3bdee6a33db0a5df7a37447be`.
An existing input with a mismatched size or hash is not overwritten. Override
mount points with `BKB_MNT_BOOT`, `BKB_MNT_ROOT`, and `BKB_MNT_RW`. Image output defaults to
`build/` and can be moved with `BKB_IMAGE_OUT`.

The output is `Blakestream-GaintB-v2.2.img`, its `.img.xz` compressed copy,
and a `.sha256` file for each. Checksum entries contain filenames, not paths
on the build machine. Verify with `sha256sum -c` inside the output directory.
The repacker refuses an existing output and uses `.img.partial` until the
filesystem has been cleanly unmounted. Use a fresh output directory for a
new build; never flash a partial file. QA logs under `build/qa-*` are not
SD-card images.

The repacker repairs inherited FAT metadata on the output copy and checks the
boot filesystem before and after updating its files. The factory input stays
unchanged; a failed filesystem check stops publication.

The repacker enables onboard Wi-Fi discovery with `wifi_used=1` in the boot
partition's `script.bin`. It verifies the decoded round trip and preserves all
other boot settings. Rootfs preparation removes the factory's forced `8189es`
module load so the unused Realtek driver cannot disturb the XR819 SDIO bus.
The repacker also rebuilds the kernel module index. This does not
enable the radio: the rootfs NetworkManager default remains Off, and saved
wireless credentials and local transaction state are removed from the image.

Startup uses `systemd-timesyncd` for network time. Legacy NTP hooks and the
unused local sendmail service are disabled; Scripta SMTP alerts remain unchanged.
An early permission-repair service prepares screen before cron and interactive
services start, including when no pools are assigned.

Inspection uses a disposable copy under ignored `tmp/`: the factory ext4
filesystem needs journal recovery even when mounted read-only. Recovery must
not alter the verified input image. `02-umount.sh` removes the inspection copy
after unmounting and detaching its loop device.

```bash
scripts/01-fetch.sh
sudo --preserve-env=PATH,BKB_FACTORY_IMG,BKB_MNT_BOOT,BKB_MNT_ROOT scripts/02-mount.sh
sudo --preserve-env=PATH,BKB_FACTORY_IMG,BKB_MNT_BOOT,BKB_MNT_ROOT scripts/03-snapshot.sh
sudo --preserve-env=PATH,BKB_MNT_BOOT,BKB_MNT_ROOT scripts/02-umount.sh
scripts/04-apply-overlay.sh
sudo --preserve-env=PATH,BKB_FACTORY_IMG,BKB_IMAGE_OUT,BKB_MNT_RW scripts/05-repack.sh
scripts/06-compress.sh
```

The image is the factory SD image plus `overlay/`. It picks up whatever
`sgminer` and bridge image are already in the overlay. Rebuilding those
binaries does not change the image until you deliberately put the new files
in the overlay.

`05-repack.sh` rewrites rootfs ownership for the controller and then restores
the `www-data` exceptions. If you add a path PHP must write, update that list
in `05-repack.sh`. Compression defaults to two threads and preset 9. Set
`BKB_XZ_THREADS` and `BKB_XZ_PRESET` to bound CPU/memory use; preset 6 is faster.
The scripts preserve the factory input, do not overwrite existing artifacts,
and remove only the explicit superseded paths in `scripts/prune-rootfs.sh`.

Published compressed-image checksums, when you are comparing a release
artifact, are published with the release. Raw `.img` bytes are not a stable
checksum across hosts because some directory mtimes from the factory image
are preserved.

## Patch series, short form

| Patches | Role |
| --- | --- |
| 0001, 0002, 0004, 0005 | Do not stall or silently refill a disabled board. 0003 is rejected. |
| 0006–0016 | Routing, pool backup flag, BK-B default clock, reporting. |
| 0017–0029 | Live fan, clock diagnostics, retry, per-board clocks. |
| 0030 | Global versus individual clock generation. |
| 0031 | Optional bridge-global `dest=1` transport. |
| 0032 | Public 400 maps to clock index 30. Display echo stays a separate table. |
| 0033 | Host diagnostic counters and trace, included from `driver-baikalu.c`. |
| 0034 | Opt-in independent clock indices, strict configuration and explicit command readback. |
| 0035 | Consistent clock-index API and configuration names; numeric values and wire behavior unchanged. |
| 0036 | Removes the root miner API's arbitrary-path `save` command from dispatch. |
| 0037 | Replaces generic loopback `ascset` access with exact trace and 20–100% fan verbs. |

`apply-patches.sh` is the command that applies them. Do not hand-merge the
series into one diff.
