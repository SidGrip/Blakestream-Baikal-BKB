# sgminer source checkout, patches, and builds

This is the portable recipe for the host miner used by Blakestream-GaintB V2.2.
It does not flash a controller, install packages, or replace the tracked
reference binary.

The tracked reference binary is both of these files, and they match:

```text
sgminer-build/sgminer.patched
overlay/opt/scripta/bin/sgminer
sha256 a42166a885922776533ac8a9051104e8758489dbecd791a8cb297840b7c97a4a
```

A rebuild writes a new binary only where you point `--out` or `make`. Copying
it over either reference file is a separate, intentional release step.

## Pin

`UPSTREAM-PIN` selects upstream [cod3gen/sgminer-baikal](https://github.com/cod3gen/sgminer-baikal):

```text
3cde8deca53f83ac65bc685b988166bd17930753
```

That is the full hash of the `3cde8de` commit (`Update README.md`, 2018-08-15).
Later local commits are not the baseline. The 36 numbered patches in
`patches/` are the whole series from that commit. `patches/rejected/0003-*.patch`
is historical and must not be applied. There is no `0003` in the active series.

The complete baseline is included in
[vendor/sgminer-baikal.bundle](vendor/sgminer-baikal.bundle), with its original
history and license notices. It is not a submodule or LFS pointer. Builds do
not depend on the upstream GitHub repository remaining available.

## Restore and patch

From any working directory, including one whose path contains spaces:

```bash
/path/to/Blakestream-GaintB-v2.2/sgminer-build/fetch-baseline.sh --src "$HOME/src/sgminer-baikal"
/path/to/Blakestream-GaintB-v2.2/sgminer-build/apply-patches.sh --src "$HOME/src/sgminer-baikal"
```

`fetch-baseline.sh` verifies the included bundle's SHA-256 from `UPSTREAM-PIN`,
restores it locally without network access, and detaches HEAD at the pin. It
fails if the bundle is missing or corrupt; it never falls back to GitHub. It
refuses to reset or delete a checkout that is already there but is not that
commit. `apply-patches.sh` requires a clean tree at that commit, applies each
patch with `patch --batch --forward -F0 -p1`, and fails on fuzz, rejects, or a
checksum mismatch against `PATCHED-SHA256SUMS`. A second run on a tree it
already patched exits 0. Neither script is invoked by default QA.

An external source is used only if explicitly selected with `--url` or
`BKB_SGMINER_URL`. That override still requires the same pinned commit.
`scripts/qa source-baseline` tests offline restoration, all 36 patches,
idempotence, and missing/corrupt-bundle failures in temporary directories.
Compiler packages, container images, and the factory SD image are separate
prerequisites; having the source bundle does not cache those dependencies.

Default destination, if you omit `--src`, is `sgminer-build/src` inside the
clone (`BKB_SGMINER_SRC` overrides it). That directory is gitignored.

## Native diagnostic tests

These do not configure or link a full `sgminer`. They need `gcc` and, for the
ThreadSanitizer phase on x86_64, `setarch` from util-linux. The driver harness
also needs libcurl development headers (`libcurl4-openssl-dev` on Debian/Ubuntu).
They fail if the patched tree is missing.

```bash
sgminer-build/test-native.sh helpers
sgminer-build/test-native.sh tsan
sgminer-build/test-native.sh driver
```

`helpers` covers `tests/test-baikal-diag.c`, `tests/test-baikal-diag-driver.c`,
and `scripts/test-bkb-diag-concurrency.c` with ASan and UBSan. `driver` compiles
`scripts/test-bkb-driver-io.c`, which includes the patched `driver-baikalu.c`,
against vendored libusb and jansson headers. The tiny `config.h` used for that
harness is written under the output directory only. It is not a substitute for
the real configure step below.

`scripts/qa native`, `scripts/qa native-tsan`, and `scripts/qa driver` call the
same script. See [QA.md](../QA.md).

## Host configure and build

Declared host packages (Debian/Ubuntu names). Install them yourself:

```text
build-essential autoconf automake libtool pkg-config
libcurl4-openssl-dev libssl-dev libudev-dev libncurses-dev
```

Jansson and libusb ship inside the upstream tree. Then:

```bash
sgminer-build/configure-host.sh --src "$BKB_SGMINER_SRC"
make -C "$BKB_SGMINER_SRC"
```

`configure-host.sh` runs `autoreconf -fi` and `./configure --enable-baikal`.
The pin does not contain a pre-generated `configure` script. The script does
not run a package manager. Put the resulting binary where you want it; do not
overwrite the reference paths above unless you mean to publish a new binary.

## ARMv7 build

The controller userspace matches Ubuntu 16.04 armhf (glibc 2.23, OpenSSL 1.0.2).
Build in that container, not with a newer host cross-compiler, when you want a
binary that can run on the device.

```bash
sgminer-build/build-arm.sh --src "$BKB_SGMINER_SRC" --out "$HOME/bkb-arm"
```

The default image is `arm32v7/ubuntu:16.04`. If it is not already local, the
script fails until you pass `--pull`. Docker must already be installed, and
ARMv7 containers must execute on the host. On an x86 build machine this
requires an administrator-configured QEMU/binfmt handler. The script checks
execution and fails with a prerequisite message; it does not register one.
`--tests-only` skips the full `sgminer` link and still runs the diagnostic
tests inside the container. A full run configures with the same
`autoreconf -fi && ./configure --enable-baikal` sequence, copies `sgminer` to
the output directory, checks that it is an ARM ELF, and runs `--version`.

Packages are installed only inside the container, and only when that image
does not already have a compiler and the curl, OpenSSL, and udev development
files. Nothing is registered on the host, and the reference binaries are not
replaced. `scripts/qa arm-compat` is this full command. It is not part of the
default QA run.

`bkb-diag-arm-xenial:20260928`, if you still have that local image, can be
selected with `--image`. It is not required and is not downloaded.

## What this recipe does not do

- It does not apply `patches/rejected/`.
- It does not flatten the patch series into one diff.
- It does not download the factory SD image or the STM32 dump.
- It does not talk to a miner.
