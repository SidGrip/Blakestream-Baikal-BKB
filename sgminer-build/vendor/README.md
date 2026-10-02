# Bundled sgminer source

`sgminer-baikal.bundle` is a self-contained Git bundle, not a submodule,
Git LFS pointer, or a download link. It contains the complete upstream
source tree and its 11 original commits through:

```text
3cde8deca53f83ac65bc685b988166bd17930753
```

Origin: <https://github.com/cod3gen/sgminer-baikal>.
The original license, copyright notices, and bundled jansson/libusb sources
are preserved inside it. Our changes remain the separate 34-patch series
in `../patches/`. The bundle adds no commits to this repository's own history.

The SHA-256 identity is recorded in [UPSTREAM-PIN](../UPSTREAM-PIN).
[fetch-baseline.sh](../fetch-baseline.sh) verifies it before restoring a
normal source checkout. No upstream repository or internet connection is
needed for that operation. A missing or corrupt bundle fails without an
automatic network fallback.

From the repository root:

```bash
sgminer-build/fetch-baseline.sh
sgminer-build/apply-patches.sh
scripts/qa source-baseline
```

The checkout is written to ignored `sgminer-build/src/` by default. Use
`--src /path/to/source` to choose another location. Dependencies such as the
compiler, container image/packages, and the factory SD image must still be
available separately; this bundle removes the upstream-source dependency,
not every build dependency.

The bundle was generated from the clean, pinned upstream checkout using
`git bundle create /path/to/sgminer-baikal.bundle HEAD`. Only objects reachable
from that upstream commit are included, not local working-tree changes.
Changing the baseline requires regenerating the bundle, reviewing its
provenance, updating the pin and checksum, and rerunning source and build QA.
