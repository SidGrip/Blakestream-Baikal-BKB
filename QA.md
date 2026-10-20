# QA

Default QA checks the tracked controller sources and the tracked v1 bridge
image. It does not need a stock STM32 dump, a sgminer checkout, a miner, a
RAM disk, root, or network access. Run it from any working directory:

```bash
/path/to/Blakestream-GaintB-v2.2/scripts/qa
```

The repository root is the parent of `scripts/qa`, so the clone path may
contain spaces. Optional phases that you do not name are printed as
`Not run (optional, not a pass)`. They are not counted as successes. A phase
you do name fails if a tool or input it requires is missing. Nothing in this
default runner installs packages, clones git repositories, pulls container
images, flashes firmware, or opens a miner session. The optional
`source-baseline` phase restores temporary local checkouts from the included
bundle with network protocols disabled; it never clones from GitHub.

Use a Linux host with Bash, Git, PHP CLI, Node.js 18+, Python 3.8+, and
standard GNU utilities (`coreutils`, `findutils`, and `grep`). Python needs
`unicorn` for firmware emulation; optional phases list their extra prerequisites
below. No developer-specific checkout, home directory, build-server address, or
ignored local build artifact is required by default QA. Entry-point regressions
also copy the scripts and controller sources into a temporary path containing
spaces and run them from an unrelated working directory.

## Default phases

| Phase | What it runs | Required on the machine |
| --- | --- | --- |
| `contract-php` | memory accounting/API fields, bounded full-week chart/clock responses, selective history clears, accepted-work, dev-activity, startup-count, firmware identity/timing, experimental Index settings, versioned profiles, network grouping, readable clock logs and measured-rate API guards | `php` |
| `contract-node` | uptime boundaries, activity, unified Boards tab, Index controls/profiles, Fan-only labeling, firmware progress/completion, network requests, log-format races and measured-rate storage confirmations | `node` |
| `contract-python` | clock configuration, bounded clock trials, firmware-job recovery guards, synchronized measured rates, persistence/service/image cleanup, recorders (including full-week peak memory and lossless writes), report, wire contract and portable entry points | `python3`, `php`, `node`, and `git` (entry-point checks also run Node fixtures and create a local Git fixture) |
| `emulation-v1` | `firmware-custom/smoke_assert_v1.py` on the tracked v1 image | `python3` and the `unicorn` module (2.1.4 is the version this tree was exercised with) |
| `emulation-active-slots` | inactive-slot selection, active failures and readiness gates on the same V1 image | `python3` and `unicorn` |

Install unicorn yourself if the import fails. The runner will not pip-install it.
For an isolated environment, run from the repository root (requires Python venv
support):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'unicorn==2.1.4'
BKB_PYTHON="$PWD/.venv/bin/python" scripts/qa
```

The v1 image used by default is
`firmware-custom/build/bkb-stm32f407-flash-clockmod-v1.bin`
(`sha256 d2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a`).
Default `emulation-active-slots` also verifies inactive-slot skipping, strict
active-chip failures, unchanged chip states, and readiness gating on this image.
Override it with `BKB_V1_IMAGE` or by passing that path to
`smoke_assert_v1.py`. A missing image or a missing unicorn module fails either
requested emulation phase, including `emulation-active-slots`.
Rebuilding that image needs the stock dump and is a different
phase.

## Optional phases

The clock-selector contract uses **Index** in the UI, `clock_index` in data and
scripts, and `RequestedClockIndex` / `SentClockIndex` in the miner API. The
default suite includes `test-clock-index-names.py`, `test-clock-index-ui.js`,
and `test-clock-index-settings.php` to keep these layers consistent. The driver
harness checks the same 8..32 bounds, nominal-400/index-30 default, readback,
and fan-only wire code zero against the complete patch series.

```bash
scripts/qa source-baseline
scripts/qa network
scripts/qa native native-tsan driver
scripts/qa host          # default phases, plus native and native-tsan
scripts/qa all           # host, plus driver
scripts/qa firmware-build
scripts/qa arm-compat
scripts/qa firmware-container
```

| Phase | Fails when |
| --- | --- |
| `network` | `python3-dbus` is unavailable in the selected Python, or offline network guards, filesystem checks or real D-Bus marshalling tests fail; never contacts a bus or miner |
| `source-baseline` | included source bundle missing/corrupt, source or patch mismatch, or missing `python3`, `git`, `patch`, or `sha256sum` |
| `native` | `gcc` is missing, or `BKB_SGMINER_SRC` / `sgminer-build/src` has no patched `baikal-diag` sources |
| `native-tsan` | same source requirement; on x86_64, `setarch` is also required |
| `driver` | same source requirement plus libcurl development headers; compiles `scripts/test-bkb-driver-io.c` |
| `firmware-build` | stock dump missing or not the identity in `firmware-custom/STOCK-BRIDGE.txt`; ARM bare-metal gcc, `python3`, or binutils missing; rebuilt bytes differ from the tracked v1 image |
| `arm-compat` | docker missing, image missing (unless you passed `--pull` to `build-arm.sh` yourself), or the patched tree is missing |
| `firmware-container` | Docker or the prepared firmware-QA image is missing, or flash/recovery/user-permission checks fail; no real hardware is used |

Prepare the patched tree before `native`, `driver`, or `arm-compat`. Those
phases do not clone or patch on their own:

```bash
sgminer-build/fetch-baseline.sh
sgminer-build/apply-patches.sh
scripts/qa native native-tsan driver
```

`fetch-baseline.sh` restores the checksum-verified bundle from this repo by
default, without contacting GitHub. An external source requires an explicit
`--url` or `BKB_SGMINER_URL` override. Build tool and package prerequisites
still apply.

`firmware-build` looks at `BKB_STOCK_DUMP` or
`firmware-dump/bkb-stm32f407-flash.bin`. The dump is gitignored. The included
rollback image can be used with
`BKB_STOCK_DUMP="$PWD/overlay/opt/scripta/firmware/stock.bin" scripts/qa firmware-build`
from the repository root. The phase
writes under `BKB_FIRMWARE_OUT` or `sgminer-build/build/firmware-rebuild` and
does not replace the tracked image. Byte identity with the tracked v1 image
was produced with `gcc-arm-none-eabi` 13.2. Another compiler can fail this
phase even when the splice logic is fine; the script prints both hashes.

`arm-compat` is `sgminer-build/build-arm.sh`. See
[sgminer-build/BUILD-SOURCE.md](sgminer-build/BUILD-SOURCE.md). It can apt-get
inside the container. It does not apt-get on the host and does not pull an
image unless that script is given `--pull`.

## Overrides

| Variable | Use |
| --- | --- |
| `BKB_PHP`, `BKB_NODE`, `BKB_PYTHON` | Interpreters for contract, network, source-baseline and emulation phases. A path that is not executable fails the phase. |
| `BKB_SGMINER_SRC` | Patched sgminer tree for native, driver, and ARM phases. |
| `BKB_QA_OUT` | Where native test binaries are written. |
| `BKB_V1_IMAGE` | Firmware image for both `emulation-v1` and `emulation-active-slots`. |
| `BKB_STOCK_DUMP`, `BKB_FIRMWARE_OUT` | Stock dump and rebuild directory. |
| `BKB_ARM_IMAGE`, `BKB_ARM_OUTPUT` | ARM compatibility container image and output directory. |
| `BKB_FIRMWARE_QA_IMAGE` | Prepared firmware integration container image. |
| `BKB_FACTORY_IMG`, `BKB_IMAGE_OUT`, `BKB_MNT_BOOT`, `BKB_MNT_ROOT`, `BKB_MNT_RW` | Optional image-build paths. Not used by QA. |

Device paths such as `/opt/scripta` and `/run/blakestream` stay as they are.
They are paths on the controller, not on the build machine.
Use absolute interpreter paths when launching from another working directory.
Python contract subprocesses reuse the selected interpreter, including image
cleanup and the fake Docker fixture. The optional firmware rebuild separately
requires `python3` and the ARM tools on `PATH`; container phases use the runtimes
inside their prepared images.

## Image build

`scripts/01-fetch.sh` through `scripts/06-compress.sh` build a flashable SD
image. They are not part of QA. Fetching the factory image uses the network.
Mounting and repacking need root and a loop device. Details are in
[BUILD.md](BUILD.md).

## UI and settings coverage

The PHP and Node contract phases check authentication, CSRF, strict input
validation, request failures, stale responses, duplicate submissions, and
controller teardown. Miner and Settings tab changes must preserve drafts
without saving settings or sending miner commands.

Clock tests cover Index 8..32 validation, Index 30 defaults, saved profiles, separate
Global/Individual drafts, explicit confirmation, and restoration. Non-default
Global expands into equal individual commands; UI scope metadata never reaches
sgminer. Estimates are read-only, context-checked, and separate from sent-command
confirmation and personal measurement history. During an API outage, compatible
saved pool assignments retain the published lab estimates without implying live
command confirmation; unsupported algorithms still show no estimate.

Presentation checks cover compact tooltips, dismissal after activation,
custom-clock graphs without test badges, percentage-only reject/HW tooltips, exclusive measured-rate/text
log views, matching Index and estimate frames, and the amber save-button pulse.
The clock warning uses two centered lines; stock firmware or disabled Adjustable
Clocks shows Fan only.

Dashboard clock/rate checks cover all Index 8..32 baseline rows, verified sent-index
selection, personal-history priority, normal model preservation and idle boards.
Clock/Index and Estimate keep their headings with no value tooltips. Card headlines
use the same rate as the table while accepted-work windows and chart histories
retain their measured data.

Browser verification covers desktop and narrow mobile layouts in both themes,
including tooltip bounds, dialog fit, keyboard focus, retained drafts, and no
unintended settings writes. Node controller fixtures do not replace browser
layout checks.

Dev Tools shows compact, problem-only collector warnings above the Logs storage
table and uses one terminal for logs, state snapshots, and recorder output.
Network/Wi-Fi, system, cooling, and web-error logs each have a RAM/SD selector
(RAM by default), a 1 MiB cap that retains the newest 512 KiB when trimming,
and credential filtering. The
diagnostic collector runs as `www-data` at seconds 10 and 40, independently of
mining. `test-diagnostic-log-recorder.py` checks routing, quiet runs, fan/cutoff
transitions, redaction, bounded writes, cursors, and failure retention;
`test-diagnostic-logs.php` checks destination changes, copy-before-delete,
locking, clearing, and invalid inputs. New diagnostic files and cursor state
are removed when preparing a clean SD image.
Readable-log fixtures cover helper JSON summaries, unchanged countdowns and
results across collector runs, error recurrence after recovery, routine rollback
poll suppression, network-state wording, and one-time RAM/SD log conversion.
Existing timestamps, real failures, redaction, and the journal cursor survive
conversion; placeholder adapter fields must never become adapter-status events.
`test-dev-logs.js` covers failed/stalled collectors, scheduler failure, health
request errors/recovery, refreshes, view switching, late responses, and cleanup.
`test-dev-storage.php` ensures all recorder histories have storage rows, including
the Clock History RAM buffer when present. Opening Logs never runs a recorder
or collects a snapshot. Browser checks cover snapshot/Copy, View switching,
diagnostics downloads, warnings, and desktop/mobile table containment.
History rows expose View and Clear without a Health column or manual play
buttons. Healthy/idle collectors produce no warning. Template tests guard
against reintroducing the removed column or buttons.

## Network coverage

Header connection tests cover wired/wireless default routes, route metrics,
disconnected interfaces, IPv6 fallback and unavailable status. The bordered
indicator has no click/pressed state or keyboard tab stop. Style checks retain
general section comments without inline declaration commentary. Theme and tab
tests cover first-visit dark mode, saved light mode, Pools/Scripta entry defaults,
and explicit settings links. Browser checks exercise both themes at desktop,
tablet and mobile widths, including diagnostics containment and log scrollbars.

Network contracts distinguish saved DHCP profiles, including router-reserved
addresses, from controller-configured Static profiles. Current Connection
follows the observed address, prefix, gateway, and DNS; only Static exposes
editable address fields. Applying DHCP must not reuse static draft values.

The optional `network` phase tests transaction guards, filesystem boundaries,
actual D-Bus binding types, monotonic expiry, rollback retries, daemon identity,
pending changes, and confirmation address checks. It does not contact a real
system bus or change networking. Select a Python with `dbus` installed using
`BKB_PYTHON`.

Wi-Fi contracts cover radio defaults and persistence, disabled/hardware-blocked
guards, absent adapters, SSID/password validation, secret handling, connection
rollback, Ethernet preservation, POST/CSRF checks and narrow sudo permissions.
Credential tests cover the standard WPA derivation vector, exact SSID bytes,
derived-only handoff/profile writes, key redaction on errors and conversion of
saved passphrases without activating or changing a connection.
Saved-network reconnect tests cover UUID-only requests, unchanged credentials and
profiles, unsupported/non-Wi-Fi rejection, and rollback preserving saved networks.
Scan tests cover radio-startup races, automatic discovery results, rapid repeats,
shared cooldowns, expiry, rebooted/legacy cache state, and NetworkManager scan
refusals without masking unrelated errors. UI checks cover Off/startup hiding
network controls, unavailable adapters staying scan-disabled after the countdown,
failed requests and connection acknowledgements preserving displayed radio state,
one-click reconnect from discovered/saved networks without resending a password,
timer cleanup, and matching
Graph interval hover/selection colors in both themes.
Run the real profile/keyfile checks separately in a disposable container:

```sh
docker build -t blakestream/wifi-qa:local scripts/wifi-qa
bash scripts/test-wifi-container.sh
```

The container also checks that the packaged DNS override selects connection-
provided DNS instead of the legacy NetworkManager dnsmasq proxy.
The container has a private system bus, no network and no physical radio.
These use simulated hardware; actual association, RF blocking and adapter
compatibility must be checked separately on a miner with a supported adapter.

Web-authentication contracts check bcrypt-only verification, fresh-image default
login, password changes, malformed and oversized inputs, write failures and
password-free audit events. They also check legacy log redaction while retaining
attempt records. The firmware container uses bcrypt credentials and checks that
flashing preserves them.

Isolated NetworkManager 1.2.6 integration has covered static/DHCP activation and
persistence, address-bound confirmation, explicit/deadline rollback,
daemon-restart recovery after partial persistence, and restoration of a
disconnected interface. It used a simulated supervisor, so it does not validate
the installed systemd rollback timer.

Hardware checks have covered static/DHCP activation, static confirmation, manual
rollback with retry after a transient D-Bus timeout, NetworkManager restart with
saved DHCP, DNS resolution after cache clearing, and renewed accepted work
after miner restart. With Ethernet and Wi-Fi both connected, direct connection-
provided DNS resolved pool hosts and mining recovered. A global Index 8 trial
produced accepted shares, then global Index 30 was restored and verified on every
board. The hardware deadline trial was interrupted and is not a
pass. Physical cable/power-loss recovery and long-run stability remain untested.

## Clock trials and baseline data

`test-bkb-clock-trial.py` injects time, API responses, options, and restart
operations. It covers complete windows, startup settling, independent board
epochs, malformed schedules, STOP/signal/deadline cancellation, changed
identities/routes/counters, health failures, partial writes, unavailable storage,
and recovery that preserves unrelated settings.

The `logged-budget-10-v1` policy logs counts below ten and stops at ten total HW
errors across the whole leg, including warm-up and all boards. Tests cover the
9/10 boundary, unchanged older policies, and the independent USB, reject, chip,
thermal, and acceptance guards. A failed window remains failed.

Transport fixtures cover NUL framing without EOF, split replies, one bounded
connect/send/read deadline, trickle timeouts, byte limits, malformed JSON,
stage-specific failure details, and unchanged restoration behavior. Controller
memory tests check bounded log retention and staggered maintenance. The
board-diagnostics test uses a full-field, three-board 18-hour fixture, generated
in a separate process so fixture allocation cannot inflate the measured child
RSS. The measurement runner also starts in a fresh interpreter to exclude
allocator memory retained by earlier functional tests. Its children use fork
rather than vfork, which can carry the parent's RSS high-water mark across
exec on Linux. It compares plain and
compact collection, requires at least a 20% peak
memory reduction and a compact peak below 65,000 KiB, and checks identical
retained data. A combined-process fixture loads, evaluates rates and rewrites a
full clock week, then records diagnostics; it requires lossless output and a
peak below 90,000 KiB and no more than 32 MiB above the clock phase. Clock-only
fixtures are also generated outside the measurement parent. These host RSS
limits are regression guards, not an ARM device memory guarantee. Tests also
cover nested counter maps, unknown fields, failed
later write batches, nonfinite values, and a cached, subprocess-free Python 2
monotonic clock with uptime fallback.

`test-hashrate-history.php` compares streamed chart responses byte-for-byte with
the prior transformation, including accepted-work gaps, pool names, ordering,
RAM/SD selection, authentication, malformed input and atomic file replacement.
An isolated full-week PHP process checks that every sample survives without
expanding the complete history into nested arrays. The optional `--compare`
mode accepts a captured history file and prior endpoint for a real-data
comparison; these private fixtures are not required by default QA.

`test-clock-history-endpoint.php` compares clock runs and selective clears with
the prior array transformation, including interval/profile/run selection,
unknown fields, equal timestamps, ordering and RAM/SD routing. Generated week
fixtures check bounded memory and lossless output. Staging failures, failed
commits, corrupt input and collector-lock conflicts must preserve existing
data. CGI checks run when `php-cgi` is available beside the selected PHP binary;
they check headers, authentication, output buffering and early session release.
The optional `--compare CLOCK_JSON PRIOR_ENDPOINT_PHP` mode also checks real
captured data without accessing a miner.

`test-capped-log-writer.py` injects read, allocation, open, partial-write and trim
failures. It checks bounded pending bytes, the file-size cap, atomic tail
replacement, external file replacement, and recovery through a real Linux pipe.
Competing recoveries and existing readers must not create a second consumer;
failed process inspection must leave the producer alone. No miner or network
access is needed.

Persistent output-failure fixtures verify that capture keeps draining the pipe,
counts only unwritten log bytes, rate-limits warnings, and writes a recovery
marker within the normal cap. A producer writes more than the pipe capacity
while the destination fails, proving storage failure does not leave it blocked.

Baseline tests cover canonical algorithm names, separate algorithm datasets,
accepted-difficulty/time weighting, duplicate suppression, identity boundaries,
same-board reference matching, and exclusion of failed/incomplete rates. Public
projection tests retain measurement provenance while removing private source
paths. MHz estimates never become verified physical-frequency measurements.

Schedules can be validated without hardware or writes:

```bash
python3 scripts/bkb-clock-trial.py validate scripts/clock-schedules/rotation-screen.json
```

The runner itself is not part of default QA. Hardware `run --execute` and
`restore --execute` require explicit authorization, root, a pinned host hash,
and a recoverable controller. See the [bounded-trial guide](TECHNICAL-GUIDE.md#bounded-hardware-trial).

Completed exploratory observations cover all 25 clock indices from 8 through 32.
The [baseline table](CLOCK-BASELINE.md) is a shared example, not every-board
qualification, measured ASIC frequency, or a guarantee of stability or efficiency.
Detailed run history and failed attempts are retained locally outside published
documentation.

## Measured rates and backups

Manual fan checks enforce 40-100% through the slider, typed entry, save endpoint,
generic miner options and runtime generation. Automatic control separately
allows 20% while retaining a 40% default. Curve fixtures check the shipped
40%/36 C, 75%/43 C and 100%/50 C points, editable percentage drafts, and 20 C
minimum target/midpoint temperatures. Recover/cutoff fields default to 40/55 C
and accept at most two digits. Fan pending tests cover saved requests
before cron, repeated status polls, missing acknowledgements, timeout without
false completion, and confirmation of the actual requested percentage.

Session-tab tests cover navigation, refresh, new logins, blocked storage,
temporary API loss and real miner process changes. Header tests cover estimate,
valid Accepted 15m, then a full Accepted 1h window, including valid zero rates.
Chart readers and recorders both default to RAM when the setting is absent;
interface changes do not erase stored chart samples or sgminer share counters.

Measured-rate fixtures cover the RAM default, preservation of saved SD choices,
reduction, storage, service recovery, atomic publication, directory-sync failure,
interrupted reset, and replay. Readable-download tests cover list-equivalent
columns, ordering, units, timezone selection, empty/stale data, omitted private
fields, unchanged All-board JSON downloads, strict board filters and matching
filtered text/JSON results. UI contracts check bounded scrolling, header
filtering, separate buffer-write/report-download controls, and board replacement
confirmation using the Boards token without visiting Logs first. Image
cleanup removes personal records without removing the shared baseline.
Fresh-image fixtures verify live Accepted 15m/1h windows without a bridge
verification record, while persistent history remains empty. Verification
starts a new context without backfill; snapshot, reset and gap guards still apply.
Reconnect regressions preserve mature live windows across a same-pool route
epoch change and a brief missing sample, without adding either transition to
verified history. Fresh service/status reads retain rates on both RAM and SD;
boot, clock, pool/worker, counter and long-gap boundaries still reset them.
Firmware/restart fixtures retain live intervals at an unchanged requested index
without subtracting counters across processes or backfilling verified history.
They cover unconfirmed readback, normal 400 versus explicit Index 30, changed
clock/algorithm/chips/installation, counter resets, outage recovery, monotonic
aging, RAM-window upgrades and fresh service reads on both RAM and SD.
Controller smoke checks have covered authenticated reads, mutation guards,
five-minute SD persistence, and SD/RAM migration without losing saved work or
duration.

Backup tests cover bounded collection, checksums, full round trips, large
history files, rejected paths and duplicates, short writes, ownership, symlink
ancestors, concurrent staging cleanup, failed-write rollback, and interrupted
restore recovery. They verify that Wi-Fi credentials are excluded and saved
Ethernet settings are staged for review, without activating or reloading a
connection. Pending Ethernet transactions contribute the saved pre-change
profile rather than an unconfirmed candidate. UI tests prohibit download after
failed snapshot, restart after failed restore, duplicate submissions, and
timer-based import completion.

Hardware backup creation, export, import, rename, and deletion have been checked.
An exported archive containing configuration, private baselines, measured-rate
storage, and chart histories was restored into an isolated fixture and compared
with its source. Restored network settings remained staged; live counters were
discarded. Restoring over the running miner was not tested.

## Firmware coverage

| Test | Coverage |
| --- | --- |
| `test-firmware-firstboot.py` | First-install stock/Custom V1 settings reconciliation, unknown/read failures, busy-operation guards, bounded read-only verification and recovery, and preservation of later user choices. |
| `test-firmware-identity.php` | Firmware identity and capability guards. |
| `test-firmware-runtime.php` | Missing/corrupt history, first-run estimates, separate stock/V1 timing, legacy migration, and invalid samples. |
| `test-firmware-failure.php` | Failed-flash clock cleanup, no timing learning from failures, idempotence, and retryable incomplete cleanup. |
| `test-firmware-job.py` | Verified stock/V1 completion, delayed USB snapshot, restart failure, missing verification marker, flash failure, and capability activation failure. |
| `test-firmware-progress-ui.js` | Progress, resumed jobs, elapsed-time handling, exceeded estimates, restart display, completion, and failures. |

The job test executes the extracted shell job function with fake dependencies.
It does not run the complete DFU transport or prove that bytes reached hardware.
PHP/Node fixtures likewise exercise their contracts without a real web server
or controller. Unicorn checks the V1 patch's CPU behavior, not USB peripherals
or physical flashing.

Fresh installations use a **164-second** stock/V1 flash baseline and a
**45-second** read-only verification baseline. Successful local operations
update their respective timing records. Failed jobs must not train estimates;
exceeding an estimate must not be treated as an automatic failure.

An authorized stock-revert hardware smoke check completed in **164 seconds**,
including matching backups, write/readback verification, USB return, and miner
restart. The API was ready seven seconds into restart. Readback SHA-256 was:

`bc19e0ff3b416a8aa21f47ae3b6f4e19c3c975ea7c1315718d28b10650532e7d`

The check verified STOCK identity, clocks disabled, and removal of the V1
capability marker. A fifteen-minute follow-up retained 64/64 chips on every
board, advancing accepted work, the same miner process, and no API failures,
rejects, or HW errors. This is a restart-health check, not long-term reliability
or a new Index baseline. A hardware test result does not assert the miner's
present live state.

### Container integration

Prepare the container explicitly on a build machine, then run the optional phase:

```bash
scripts/test-firmware-container.sh --build
scripts/qa firmware-container
```

Building downloads legacy dependencies from the signed Debian archive; normal
runs do not build, install packages, pull images, or access the network.

The harness runs the actual PHP 5.6 login/firmware endpoints as `www-data`, real
sudo rules, complete firmware wrapper and Python 2.7 DFU helper, permission
repair, runtime-config builder, miner launcher, and GNU screen. USB/DFU uses a
file-backed flash image, GPIO accesses are redirected and checked for a
configured reset pulse, hardware settling delays are shortened, and a loopback
API process replaces the miner executable.

Cases cover stock/V1 writes, read-only verification and cancellation, invalid
images, backup/read failures, write and readback failures, verified rollback,
failed rollback, delayed USB return, restart failure, duplicate requests,
reconnection, live-lock preservation, stale locks, and permission repair.
Assertions check flash bytes and backup order, failure/timing state, helper
ownership, protected paths, web configuration access, unauthorized sudo denial,
firmware audit entries, and isolated root/user screen sessions across supported
screen permission modes. Both the packaged screen cleanup script and launcher
fallback are exercised. First-launch permission checks also run without any
assigned pools and confirm repeated repair preserves live root/user sessions.
Failed recovery must leave the miner stopped. Job-start
and cancellation replies remain readable when sudo emits a hostname warning;
missing or malformed helper JSON remains an error.

Only `scripts/` and `overlay/` are mounted, both read-only. Tests have no hardware
devices, host ports, credentials, Docker socket, or network access outside
loopback. Container-local root and a
limited capability set are required to test real ownership and setuid behavior;
`--privileged` is not used. `no-new-privileges` is deliberately not set because
it would prevent testing sudo/screen privilege transitions. The runner refuses
to execute outside its marked, network-isolated container and cleans it up on exit.

This is a software integration model, not an Orange Pi/STM32 emulator. The web
server is PHP's test server, not deployed lighttpd/CGI. It does not validate the
assembled SD image, kernel/USB drivers or USB reset ioctls, real flash duration,
physical power loss, or every interruption/storage fault. The legacy runtime is
for isolated testing, not deployment as a web service. Retain a supervised hardware
flash/readback/recovery check and a separate SD-image boot check. See the
[harness notes](scripts/firmware-qa/README.md) for boundaries.

## Source, packaging, and entry points

`test-sgminer-source.py` checks offline bundle restoration, the 34-patch series,
resulting hashes, repeat invocation, relocation, spaced paths, explicit source
overrides, and rejection of unrelated checkouts or corrupt bundles. Network
protocols are disabled during these tests.

`test-image-packaging.py` uses a tiny filesystem and fake compressor to check
cleanup, unrelated-file preservation, output/checksum names, and bounded
compression arguments. It does not mount, build, or boot a real SD image.

Default `contract-python` runs `test-image-startup.py`, which checks preservation
of hardware boot commands, removal of obsolete time/mail paths, the early
permission service, and Ethernet initialization before NetworkManager's carrier
probe. Default `contract-php` runs `test-connection-status.php`, including both
`wireless` and `phy80211` sysfs layouts. These are source/fixture checks, not
firmware-container cases or a real image boot.

`test-qa-entrypoints.py` checks help/description from another directory and
failure exits for missing tools, source, images, or an invalid stock dump.
It also checks a relocated checkout, missing `dirname`, interpreter paths with
spaces, a failing `python3` on `PATH` with a working `BKB_PYTHON`, and temporary
directory cleanup. Selected interpreters must reach child fixtures, not just
the top-level runner.
The invalid dump must fail before the cross compiler starts. Default contract
checks must not invoke SSH, sudo, package installation, Docker, or DFU.

A full image build and boot test, actual systemd network deadline recovery,
physical frequency measurement, matched stock/V1 performance comparison, and
long-duration hardware qualification remain separate from passing default QA.
