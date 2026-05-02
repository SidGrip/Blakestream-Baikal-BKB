# Building Blakestream-GaintB from source

This document covers everything needed to reproduce the published `.img.xz` from a clean clone of this repository, on a Linux host. Tested on Ubuntu 22.04 / 24.04. macOS will not work because `losetup` is Linux-specific.

## What gets built

A 3.9 GB raw `.img` containing the modified Baikal Orange Pi rootfs, then xz-compressed to ~900 MB:

```
build/
├── Blakestream-BKB-v2.1.img         (3.9 GB raw, dd this to an SD card)
├── Blakestream-BKB-v2.1.img.sha256
├── Blakestream-BKB-v2.1.img.xz      (~900 MB, what we publish)
└── Blakestream-BKB-v2.1.img.xz.sha256
```

The image is the **factory** Baikal `PiZero_GB_180105_V1.0.img` with our modifications rsync'd in. We do not redistribute the factory image; you'll fetch it on your first build.

## Host requirements

- Linux x86_64 with **sudo** (the repack step needs root for `losetup` + mounting the loop device).
- ~10 GB free disk: 4 GB for the raw image, 1 GB for the xz, plus temp space.
- Tools: `bash`, `rsync`, `losetup`, `mount`, `partprobe`, `udevadm`, `blkid`, `xz` (`xz-utils`), `sha256sum`, `wget`. Most ship with a base install; on Ubuntu add anything missing with `sudo apt install xz-utils util-linux`.
- For the sgminer ARM rebuild (only needed if you also want to rebuild `sgminer.patched`): **Docker** + qemu-user-static so an `arm32v7/ubuntu:16.04` container can run on your host. `sudo apt install docker.io qemu-user-static binfmt-support`.

## Two build paths

### Path A — Image only (use the pre-built `sgminer.patched` checked in)

This is the common path. The committed binary is a 5.3 MB ARMv7 ELF that has all our patches applied; you just bake it into the image.

```bash
cd Blakestream-Baikal-BKB
./scripts/01-fetch.sh                 # download factory image (~3.9 GB)
./scripts/02-mount.sh                 # mount factory image read-only
./scripts/03-snapshot.sh              # copy mounted rootfs to firmware-modified/rootfs/
./scripts/02-umount.sh                # release the read-only mount
./scripts/04-apply-overlay.sh         # rsync overlay/ over firmware-modified/rootfs/
sudo ./scripts/05-repack.sh           # build the .img (requires root for loop device)
./scripts/06-compress.sh              # xz -T0 -9 → .img.xz (~7 min on 8 cores)
```

If you re-run after editing the overlay, the cycle is:

```bash
sudo chown -R $(whoami):$(whoami) firmware-modified/rootfs   # script 05 chowns to root
rm -f build/Blakestream-BKB-v2.1.img*
./scripts/04-apply-overlay.sh
sudo ./scripts/05-repack.sh
sudo chown $(whoami):$(whoami) build
./scripts/06-compress.sh
```

### Path B — Also rebuild `sgminer.patched` from source

Only needed if you've changed code under `sgminer-build/src/` or one of `sgminer-build/patches/`.

```bash
# 1. Clone upstream sgminer source into sgminer-build/src/ (gitignored).
git clone https://github.com/cod3gen/sgminer-baikal sgminer-build/src
cd sgminer-build/src
git checkout dev

# 2. Apply patches in order. Patches assume CWD is sgminer-build/src.
for p in ../patches/[0-9]*.patch; do
    patch -p1 < "$p"
done
cd ../..

# 3. Build inside an ARMv7 Ubuntu 16.04 container. The repo's pre-built image
#    matches Baikal's libc6/libssl/libudev versions; newer Ubuntu containers
#    will produce a binary that won't run on the device.
docker pull --platform linux/arm/v7 arm32v7/ubuntu:16.04
docker run --rm --platform linux/arm/v7 \
    -v "$PWD/sgminer-build/src:/src" \
    arm32v7/ubuntu:16.04 \
    bash -c '
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            sed coreutils build-essential autoconf automake libtool pkg-config \
            libcurl4-openssl-dev libudev-dev libusb-1.0-0-dev libjansson-dev \
            libncurses5-dev libssl-dev
        for t in sed grep awk mkdir nm; do [ ! -e /usr/bin/$t ] && ln -sf /bin/$t /usr/bin/$t; done
        cd /src
        make CFLAGS="-g -O1 -Wall -DTRUE=1 -DFALSE=0"
    '

# 4. Promote the new binary into the overlay + the tracked artifact.
cp sgminer-build/src/sgminer overlay/opt/scripta/bin/sgminer
cp sgminer-build/src/sgminer sgminer-build/sgminer.patched

# 5. Continue with the Path A image build steps.
./scripts/04-apply-overlay.sh
sudo ./scripts/05-repack.sh
./scripts/06-compress.sh
```

## Source layout

| Path | Purpose |
|------|---------|
| `overlay/` | All files we add or replace in the factory rootfs. The build rsyncs this over the factory tree. |
| `sgminer-build/sgminer.patched` | Pre-built ARMv7 sgminer binary, committed. |
| `sgminer-build/patches/` | Numbered patch series applied on top of upstream `cod3gen/sgminer-baikal` to produce the binary. GPLv3 source-of-truth. |
| `sgminer-build/src/` | Gitignored. Cloned upstream source where patches are applied. |
| `firmware-original/` | Gitignored. Holds the factory `.img` after `01-fetch.sh`. |
| `firmware-modified/rootfs/` | Gitignored. The factory rootfs snapshot with our overlay rsync'd over it. |
| `build/` | Gitignored. Where the output `.img` and `.img.xz` land. |
| `scripts/` | The numbered build scripts. |

## Notes / gotchas

- **`05-repack.sh` rewrites file ownership** for the entire rootfs because the dev-host uid (typically 1000) doesn't match the Pi's uid mapping (1000 = baikal there). It then re-applies a list of "this path needs `www-data`" exceptions. If you add a file that PHP needs to write to, add it to the loop in `05-repack.sh` around line 116.
- **The patch series numbering has a gap** (no `0003`). This is historical — `0003` was an earlier idea that didn't pan out. Patches `0001 0002 0004 0005 0006 0007 0008` is the full applied series.
- **The patch series is intended to apply non-interactively** with `patch -p1` from `sgminer-build/src/`. Before publishing, we smoke-test the full series from the upstream baseline to catch malformed or stale patch artifacts.
- **xz at `-9` can use significant RAM with `-T0`** because it uses all available CPU threads. On smaller hosts, edit `scripts/06-compress.sh` to use a fixed lower thread count such as `xz -T2 -9`; compression time scales roughly linearly.

## Verifying a built image

```bash
sha256sum build/Blakestream-BKB-v2.1.img.xz
# compare against the published value at https://explorer.blakestream.io/baikal-bkb
```

The raw `.img` sha will vary slightly between builds because of `mtime` preservation in some factory directory entries. The xz wrapper is also non-deterministic at `-T0`. If you need byte-identical reproducibility, run the build single-threaded and zero out the few mtime-bearing directory entries before `cp`. Most users don't need this.
