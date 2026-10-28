# Blakestream-GaintB V2.2 technical guide

V2.2 is the controller release: the Orange Pi image, sgminer host, and web UI.
Bridge firmware V1 is the first release of the STM32 patch for clock
commands. It is not a second product version. A bridge echo, a synthetic
hashrate, or a successful `SET_OPTION` ACK does not prove that an ASIC PLL
locked at a measured frequency.

Detailed diagnostic plans, results, and original captures are kept locally
and are not included in this repository. See [QA.md](QA.md) for the tests
available in a clone.
Controlled accepted-work results on V1 are summarized below. A stock-versus-V1
comparison on the same hardware and physical frequency calibration remain
pending.

## Current status

Completed trials cover **Index 8..32**, with a full one-hour observation on all
three boards at each index and opening/closing Index 30 references.
The bundled `blake256r8` examples use these
observations; they are not qualified presets or physical frequency calibration.
See [Accepted-work validation](#accepted-work-validation) for coverage,
calculation methods, and limits. Detailed trial history remains local.

Current clock controls edit Index only. GH/s and MHz are read-only estimates;
Global extrapolates a three-board total from the shared per-board example.
Index 30 retains the nominal 400 MHz anchor. Whole-miner wall power was measured
with Eyedro; physical ASIC frequency and per-board power were not measured.
Stock-versus-V1 equivalence, qualification of other boards, and long-term
stability remain open validation work.

## How the pieces fit

```text
UI / miner.options.json
        |
        v
build-miner-runtime.py          /opt/scripta/etc/*.json
        |                         /opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY
        v
sgminer (patched host)          one USB bridge, three logical boards
        |
        v
STM32 bridge firmware           stock, or custom v1
        |
        v
three hash boards               four internal targets each, 16 selectors
```

`/opt/scripta` and `/run/blakestream` are paths on the controller. Build and
QA scripts on a development machine do not rewrite them.

The host is one sgminer process. The BK-B exposes three boards through one
STM32 USB bridge, so pool routing is not three separate USB devices. The
runtime builder writes a sgminer config plus a pool-map sidecar. Patches
`0001`, `0002`, `0004`, and `0005` keep a disabled board from stalling the other boards or
from being refilled forever. Later patches add routing, the clock command,
diagnostics, and the global coordinator. The series is applied as 36 separate
patches on upstream `3cde8deca53f83ac65bc685b988166bd17930753`. Patch `0003`
was rejected and is not applied. The upstream source is included as a
self-contained Git bundle; source preparation restores it offline instead
of downloading from GitHub. See [sgminer-build/BUILD-SOURCE.md](sgminer-build/BUILD-SOURCE.md).

The driver's MH/s telemetry and the dashboard's **Model** estimate are not
share-based measurements. For blake256r8 the driver computes them from the
configured clock, the reported chip count, and a factory scale factor (`2190`
in `baikal_hash_done`). That is why boards can show the same modeled MH/s while
their accepted-work rates differ. Accepted 15m/1h use measured accepted work;
the clock editor's separate lab estimates come from completed trial windows.

## Configuration tabs

Miner uses **Pools / Clocks & Fan / SGMINER**. Pools contains the catalog and
pool editor; Clocks & Fan contains clock controls, profiles and fan controls;
SGMINER contains advanced options and the existing miner restart commands.
With stock firmware or Adjustable Clocks disabled, **Clocks & Fan** becomes
**Fan** and hides clock controls and profiles.
The first visit after login starts at Pools. Miner, Settings and Dev Tools
remember their selected subsections in session storage until logout or an
observed miner process restart. Refresh and network handoff preserve selections;
an unavailable API is not treated as a new process. The storage key uses an
independent login nonce, not the PHP authentication session ID.
Subsections use `ng-show`, keeping the forms mounted: changing tabs neither
discards drafts nor saves settings or restarts the miner. Leaving the page or
explicitly reloading settings still discards unsaved edits.

Settings uses **Scripta / Mining / Network / Wi-Fi** with the same tab styling and
starts at Scripta. Explicit Network reconnect links still open Network.
Existing forms stay mounted across tab changes. Remote
control and e-mail alerts remain under Mining.

The theme defaults to dark when `localStorage.bsTheme` is absent, invalid or
unavailable. An explicit light choice is retained. The root and body theme are
applied before page content so controls and scrollbars follow the same theme.
The header connection icon uses the existing system-statistics poll. It reads
the lowest-metric usable default route (IPv4, then IPv6) and identifies Wi-Fi
from either sysfs `wireless` or `phy80211`. A route is not proof of Internet or pool availability. Read failures
show unknown rather than leaving a stale connected indicator. The bordered icon
is status-only: it has no click action, button highlight or keyboard tab stop.

### Network settings

The Network form supports IPv4 DHCP or static addressing on wired interfaces.
DHCP is the initial choice when no saved profile exists; existing saved static
profiles retain their mode. Current address, prefix, gateway and DNS come from
NetworkManager, not the form draft. A router DHCP reservation remains Dynamic:
the mode comes from the miner's IPv4 configuration method, not whether the
address is stable or the DHCP profile is saved. Current Connection shows the
mode, IP, prefix, gateway and DNS; Dynamic does not duplicate these below the
selector. Only Static shows editable IPv4 fields and writes fixed values.
Polling refreshes Current Connection without overwriting unsaved Static edits.
Static settings require an address, prefix
1-30, a different gateway in the same subnet and one to three DNS addresses.

`f_network.php` requires a logged-in session and CSRF-protected POST for writes.
It forwards only allowed fields to the root-owned `network-settings.py` helper.
The systemd apply worker creates an unsaved, non-autoconnecting candidate profile
without overwriting the original. The rollback timer must be active before
activation. Keep Settings validates the candidate and the web server's reached
address, then saves the candidate to the SD card. The browser never confirms
automatically. After successful login, `js/network-login.js` checks for a pending
transaction and redirects to Settings > Network for explicit confirmation.
Logging in is not confirmation; Keep Settings is on the Network page, not an
unauthenticated login-page action or a dashboard-wide overlay.
Revert or a 180-second monotonic deadline restores the original
profile and active/disconnected state. A boot-ID change also expires a pending
transaction. The journal and lock live under `/var/lib/blakestream-network`.

Confirmation retires the unchanged previous UI-owned `blakestream-xxxxxxxx`
profile after persisting and checking the candidate. Externally named or changed
profiles are not deleted. The original snapshot remains available for rollback
if confirmation cannot finish. This prevents an old UI-created static profile
from taking over DHCP after a NetworkManager restart.
If persistence or retirement fails, confirmation attempts rollback immediately;
the existing timer retries a rollback that cannot complete.

The NetworkManager service also starts `blakestream-network-reconcile.service`.
Its root-only `restore-confirmed` operation can reactivate the saved, last
confirmed profile after a daemon or boot identity change. It skips pending
transactions, disabled autoconnect and a different saved active profile, and
refuses missing or externally changed address configuration. It does not invent
an address, overwrite a profile or turn DHCP into static. Transactions ending
in rollback are not covered by this confirmed-profile hook. The worker has a
35-second process deadline and at most three starts in five minutes.

Failed rollback remains pending and is retried by the independent timer. The
helper resolves profiles by UUID and pins each invocation to one D-Bus daemon
owner, preventing stale object paths from changing unrelated connections after
a restart. External profile changes stop automatic restoration instead of being
overwritten. API processes have a 12-second deadline, workers 35 seconds and
individual D-Bus calls at most two seconds. These guards cannot recover from a
failed SD card, a stopped recovery service or a physically disconnected cable.

The implementation targets NetworkManager 1.2.6, Python 3.5 and dbus-python 1.2.
It does not depend on the newer NetworkManager checkpoint API. Legacy IPv4 wire
values use native network-order integers and `aau` address arrays. D-Bus variants
use typed values with `variant_level`. See the [D-Bus binding reference](https://dbus.freedesktop.org/doc/dbus-python/tutorial.html)
and [NetworkManager connection API](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.Settings.Connection.html).
The legacy `Unsaved` property includes runtime changes to existing profiles, so
saved-profile detection checks keyfile UUIDs rather than treating that flag as
proof that no file exists.

An authenticated `pending` read inspects only the transaction journal. It neither
contacts NetworkManager nor changes or confirms a transaction, allowing the UI
to discover pending work even when NetworkManager is busy. Browser read errors
preserve the last displayed interface, disable confirmation until fresh status
arrives, and leave explicit recovery available. Detailed errors remain in the
API response and service journal.

### Wi-Fi settings

Settings > Wi-Fi uses a separate authenticated `f_wifi.php` endpoint and
root-owned `wifi-settings.py` helper. Mutations require POST and a session CSRF
token. The sudo rule allows only status, pending, scan, connect, disconnect,
forget and radio verbs; worker and boot operations are not web-callable.
Passwords pass through stdin, never command arguments or status responses.
WPA/WPA2 passphrases are converted to the SSID-specific 256-bit PSK before the
temporary root-only handoff or NetworkManager profile is written. Saved keys
remain in NetworkManager's root-only keyfiles, outside the web tree and
application backup format. A derived PSK still grants access to that network;
it is not encrypted storage. Open networks do not store a password.

Fresh images remove saved wireless profiles and local Wi-Fi transaction state.
The boot configuration permits onboard discovery (`wifi_used=1`); a controller
still using the factory `wifi_used=0` cannot expose the onboard adapter until
that boot setting is changed and the controller restarted. Kernel module
indexes are rebuilt during image packaging.
The image NetworkManager state sets `WirelessEnabled=false` while keeping
`NetworkingEnabled=true`. `blakestream-wifi-default.service` runs before
NetworkManager; the radio service reapplies the persisted choice afterward.
Explicit On/Off choices live in `/var/lib/blakestream-wifi/radio.json` and are
preserved on upgrades. Off changes the software radio state, not just the
connection. Scan/connect cannot enable it implicitly; hardware blocks cannot
be overridden by this control.

Off hides network and credential controls; the selected radio button shows the
state without a separate banner. On/Off uses the Graph interval control's
shared selected, hover and focus styling in both themes.
Scan disables immediately and shows a compact retry countdown. A 15-second
cooldown is shared across browser sessions using boot-scoped monotonic scan
metadata. NetworkManager's already-scanning/recent-scan refusals retain available
results and start a new retry window instead of reporting a service failure.
The countdown is a retry window, not a promise that a background scan has ended.
Fresh scan results filter access points by NetworkManager's boot-time `LastSeen`
value; a stale, stronger duplicate cannot hide a newly observed network. Later
status polls retain that scan boundary instead of restoring old cached entries.

The interface supports one managed Wi-Fi adapter, open and WPA/WPA2-PSK networks,
and hidden SSIDs. It does not add drivers or support WEP, enterprise or WPA3.
New wireless profiles use DHCP and a route metric of 600, leaving ordinary
Ethernet routes preferred without editing them. Custom route metrics can change
that preference. The header indicator reports the observed default route.
The metric uses the signed 64-bit type required by the
[NetworkManager 1.2.6 settings implementation](https://github.com/NetworkManager/NetworkManager/blob/1.2.6/libnm-core/nm-setting-ip-config.c).

The NetworkManager startup drop-in brings `eth0` administratively up before the
daemon probes it. The legacy `sunxi_geth` driver rejects carrier ioctls while
down, causing NetworkManager 1.2.6 to cache cable detection as unsupported and
retain Ethernet routes after unplugging. Initializing the interface first lets
NetworkManager handle cable removal and Wi-Fi routing normally, including a
start with no Ethernet cable. This does not change profiles or route metrics,
disable reverse-path filtering, or unload either network driver.

Separately, `90-blakestream-dual-network.conf` sets IPv4 reverse-path validation
to loose mode (`rp_filter=2`) on `eth0` and `wlan0`. Validation remains enabled,
but no longer requires traffic to arrive through the preferred outbound
interface. This permits access through either interface's address when both
share a subnet, while normal route metrics still prefer Ethernet. It does not
bridge the interfaces or give them one shared IP address.

Connection changes allow 60 seconds for association and addressing, inside an
80-second worker budget with a separate 90-second recovery deadline. The timer
checks every 15 seconds. Ethernet stays enabled throughout; the helper never
disconnects the wired interface to try Wi-Fi. A working cable and wired network
are required for wired fallback. This is not a test of upstream Internet access.
Failure or interrupted application attempts to restore the previous saved
wireless connection. This cannot restore a removed adapter, lost access point,
or unreachable network. Keep Ethernet available during initial Wi-Fi setup.
Actual wireless association and radio hardware require on-device validation;
container profile and recovery tests are not RF tests.

NetworkManager uses `dns=default` so the system resolver receives the DNS
servers supplied by active connections without a second per-interface DNS
proxy. The legacy dnsmasq plugin can stall lookups when Ethernet and Wi-Fi
advertise the same DNS server on this controller. The override leaves saved
DHCP/static DNS choices intact and requires no public DNS server. See the
[NetworkManager 1.2.6 configuration reference](https://github.com/NetworkManager/NetworkManager/blob/1.2.6/man/NetworkManager.conf.xml).

### Web password and login audit

The web login stores a salted bcrypt hash at cost 10 in
`/opt/scripta/etc/uipasswd`, readable and writable only by `www-data`.
The default password remains `baikal`; changing it is optional. Passwords longer
than 72 bytes or containing NUL are rejected instead of silently truncated.
Only bcrypt hashes are accepted; old MD5 password files are not supported.
Application backups preserve the current hash, not the entered password.
Password changes use a POST body, never a URL query string in browser or access
logs. The server rejects password changes submitted through GET.

Successful and failed login events record the time and sanitized source/browser
metadata, never the supplied password. Existing password fields can be redacted
from the active security log without deleting the attempt records. Redaction
does not securely erase older SD-card blocks or copies outside that log.
The dashboard is served over HTTPS; port 80 only redirects to HTTPS. First boot
generates a device-specific X.509 v3 server certificate after entropy is ready.
Initial identity provisioning waits for credited kernel entropy from
`/dev/random`; a cold headless boot can delay SSH and HTTPS until enough entropy
is available. Valid existing identities are retained. The default certificate is
self-signed and names the controller hostname, not its DHCP address. HTTPS encrypts traffic,
but browsers cannot authenticate this identity by default and can continue to
show **Not secure** after a local exception. A trusted certificate matching the
access hostname or IP is required to remove that warning. Keep the interface on
a trusted network.

### Browser terminal

The dashboard's **Terminal** tab opens `/terminal/` on the same HTTPS port 443.
Lighttpd proxies only that path to ShellInABox at `127.0.0.1:4200`; port 4200
does not listen on the LAN. `blakestream-terminal.service` runs the daemon as
the unprivileged `shellinabox` user, with
`--service=/terminal:SSH:127.0.0.1`. It opens a local SSH connection and requires
the Linux account's credentials. The shipped SSH policy permits `root` only.

The dashboard password and Linux password are separate. Direct visits to
`/terminal/` use the SSH login prompt rather than the PHP dashboard session.
Changing the web password does not change this login. The old SysV
`shellinabox.service` remains masked; the dedicated terminal service is enabled
instead, preserving the HTTPS route and loopback-only backend.

## Clock coordinator

**Index** is the compact UI label for a clock-table index. Runtime fields and
scripts use `clock_index` (or `ClockIndex` in the existing API naming style),
with `clock_indices` for lists. An index selects a table entry; it is neither
MHz nor proof that a physical PLL locked. Patch `0035` renames the host options
and API fields without changing clock-table values, command bytes, fan-only
code zero, or the nominal-400 default. Earlier patches retain their historical
names; active code and script entry points use the new terminology.

The public clock table currently has one entry: 400. Anything else in
`baikal-options` is replaced with 400. The host encodes public 400 MHz as clock
index 30. Index 30 is the factory startup index and nominal reference, not a
completed frequency calibration. Raw-index overrides use the separate
experimental path described below.

`GET_INFO` still reports through the stock bridge display table. Index 30
echoes 480 on that table. Index 22 echoes 400. The host keeps that mapping
explicit so a display echo can confirm the command path without being shown
as the physical clock. `CommandVerified` requires a successful host ACK and
matching GET_INFO echo for the applied clock command/generation. It does not
mean the PLL was measured or prove that every ASIC locked.

Global mode is the default. Individual mode is selected when
`baikal-clock-mode` is `individual`, or when older per-board clock settings
exist and no mode was saved. In normal global mode the runtime does not pass
per-board clocks into sgminer. Normal individual-mode MHz settings must each
be a validated clock (today, only 400) or the runtime refuses to launch.
Explicit clock-index settings instead require the verified experimental path;
non-default Global in the UI also uses that individual-mode runtime path.

### Linked clock controls

Clock settings expose **Global (all boards) / Individual (per board)** with an
editable **Index** first, followed by **Est. GH/s** and **Est. MHz** in a
separately bordered read-only group. The Index input has its own matching compact
bordered group. Unsaved clock edits use the save button's amber pulse, without
an additional Unsaved settings label. Board qualification sublines are omitted;
measurement provenance and limitations remain in this guide and the baseline
data, not in estimate tooltips.
Changing Index refreshes those labels locally; only confirming Save Clock Settings
applies the requested index. Global previews the same value on each board;
Individual keeps separate values. Invalid or blank Index inputs cannot be applied.
There is no Normal/Custom selector. Estimates are not reverse-mapped into clock
commands: noisy, non-monotonic rates do not define an unambiguous MHz/GH/s input.

Each heading has a compact **Default** button. It selects that scope and stages
Index 30 / 400 MHz, without saving, resetting the other scope's draft, or
changing temperature/fan settings. The individual button resets all three board
drafts. Scope switches and Default clicks never send clock commands by themselves.
The backend experimental option is enabled automatically for non-default indices
and removed when the selected values are all 30; firmware identity and confirmation
checks remain in force. Reading saved settings does not rewrite them.

The public/default pair is **400 nominal MHz / Index 30**. Both Default buttons
select index 30, never index 22. The firmware GET_INFO echo of 480 for index 30
is not used as a public MHz label. Other indices 8..32 remain editable and
saveable through Index. Compatible completed lab observations supply approximate
performance-equivalent MHz and accepted GH/s; missing or incompatible data
shows **Unavailable**, without inventing a frequency or blocking a valid Index.
Saved profiles retain their canonical indices, without reinterpretation.
The 400 anchor is a factory/default label, not measured frequency; no linear
clock index conversion is inferred. Merely opening this UI or selecting Default
does not change the running operating point. Host Clock/Estimate telemetry
retains its legacy model.

Non-default Global expands a single choice to three equal individual indices. It
uses the existing per-board command path, not the experimental bridge-global
transport. Actual API/runtime `ClockMode` therefore remains `individual`.
Optional `blakestream-clock-scope` (`global` or `individual`) remembers the UI
selection separately and is stripped before creating sgminer configuration.
Legacy options without it retain their existing mode. Unequal indices cannot
be saved in non-default Global scope. No firmware, driver, normal-clock mapping, or live
clock value is changed by this UI deployment.

When downgrading to code predating these controls, restore the matching saved
options backup or remove `blakestream-clock-scope` first. Older runtime builders
do not know to strip that UI-only key.

Requested values remain separate from sent-command confirmation. Shared example
rates are not measurements of the user's hardware. Underlying evidence retains
the contributing board identities and limited test coverage, without lab slot
labels in the clock controls. Accepted 15m/1h remain measured-work displays.

`scripts/publish-clock-baseline.py` produces the small bundled
`overlay/opt/scripta/etc/clock-baseline.json` from a completed private export.
It selects explicit matching measured binary identities and blake256r8 only, pools
accepted difficulty over device time per Index, and preserves measured board IDs,
error counts and contributing tuples. The public projection strips private
source paths; the original per-board/full-tuple evidence remains separate.
Index 30's shared rate uses completed all30 references, not control-board samples
from mixed settings. The current dataset uses all three boards at the same index
for every measured window. These remain transferable lab examples, not
hardware-specific predictions or qualifications.

For non-default indices, shared estimated MHz is `400 * pooled GH/s / reference
GH/s`, using a time-weighted nominal Index 30 reference on Custom V1 from each
contributing physical board. Missing same-board references leave MHz unavailable.
Global displays three times the shared per-board rate plus that per-board average. This total
is an extrapolation, not a measured all-board result. Personal measured-rate
history, normal-mode model estimates and accepted 15m/1h values are not overwritten.
The PHP reader caps the file at 64 KiB and checks its schema; live compatibility
uses the driver Algo (not a pool-label override), reported host SHA256, 64 chips
and a verified firmware hash matching the dataset or its explicit
`compatible_bridge_chip_md5` list. The current measurements use release V1
directly. Original measurement hashes are preserved, not rewritten to match
another reference binary in the source tree.
This compatibility applies only to shared estimates, not personal verified
history or live accepted-work measurements. A 56/64 startup count remains
56/64; the shared healthy-board estimate is not a guarantee for that board.
When live telemetry is absent or stale,
the editor can still show the published lab reference for a saved pool using
the baseline algorithm. It does not claim a live rate or confirmed command.
Incompatible live data and unsupported saved algorithms remain unavailable.
These display-only values do not dirty, save or apply
clock settings, and updating the baseline does not reinterpret saved profiles.

The **Est. GH/s** and **Est. MHz** readouts have no explanatory hover/focus
tooltips. Their interpretation is documented here: these are exploratory
`blake256r8` lab examples, not live hashrate, measured physical frequency, or
qualified presets. In `clock-baseline.json`, each Index row retains
`measured_boards` (the contributing board IDs), `accepted_shares` (the count
across contributing windows), `board_windows` (the number of board observation
windows), and `hw_errors` (their recorded hardware-error count). These counts
describe the lab evidence, not the user's current miner. Missing compatible
evidence displays **Unavailable**; Index 30 still retains its nominal 400 MHz label.

Apply-clock confirmations keep each board row to its previous/requested Index and
available GH/s/MHz estimates. Command-confirmation and qualification wording is
not repeated in each row. The shared warning separately states that MHz is
performance-equivalent rather than measured frequency, and that instability or
hardware damage is possible. Completed Index 32 hashing tests do not verify its
physical frequency or qualify every board for that setting. The standalone
sent-command status and backend confirmation checks remain unchanged.

Browser checks cover Global/Individual previews and Default at desktop/mobile
widths without applying settings. See [QA.md](QA.md) for software and browser
coverage; these checks do not establish an SD-image build or a hardware trial.

### Experimental indices

The opt-in experimental path accepts one integer clock index per board,
8 through 32. It is separate from the normal 400 clock-table entry, not a
separate Normal/Custom UI selector. Patch `0034` adds the experimental path;
patch `0035` gives its options their current names:
`baikal-experimental-clock-index` (JSON boolean) and `baikal-board-clock-index`
(a canonical string such as `22:30:30`). Both require an explicitly saved
`baikal-clock-mode` of `individual`. Non-default Global settings use this path with
equal indices; true global transport still cannot use this override.
The runtime and settings endpoint also require Adjustable Clocks enabled
and a saved verification record with a recognized V1 identity/hash pair from
the firmware allowlist. An artifact
on disk alone does not establish that the bridge was flashed successfully.

Editing clock fields stages experimental inputs locally; only confirming Save
Clock Settings applies them. Fields load from saved canonical indices, defaulting
to Index 30 for legacy normal settings. Matching indices initialize the Global
draft; otherwise it starts at Index 30. Scope changes retain separate drafts rather
than replacing them with telemetry. The UI shows the last
sent index and whether its command was confirmed separately from the requested
inputs; stale/unavailable telemetry is not shown as confirmed. Confirmation
previews every board's previous command and requested index. All normal and
experimental saves and profile applications require confirmation.
Cancel sends nothing. Selecting Default and saving removes both
experimental keys. Firmware verification/revert paths clear
saved experimental overrides when leaving the current custom firmware.
Normal 400 still encodes index 30; this feature does not restore the older
arithmetic conversion that encoded a 400 label as index 22.

Profiles store `version: 2`, `mapping: "bkb-v1-clock-index-1"`, a boolean
`experimental`, and three canonical `clock_indices`, in addition to the existing
mode, temperature and fan fields. Optional `scope` preserves the UI's global or
individual selection; absent scope defaults to the profile's actual mode.
Experimental profiles require actual individual mode, including non-default Global
profiles with three equal indices. Scope is not an instruction to change the
transport. Unknown scope and non-default Global profiles with unequal indices fail.
Normal profiles store `[30,30,30]`; older MHz-only profiles remain normal and are
normalized on load without writing the file. Unknown versions/mappings and invalid
indices are rejected, not clamped. Saving a profile alone does not apply it.

The bundled examples cover all 25 indices from 8 through 32, based on completed
trial observations. The clock controls do not display per-board
Untested/Preliminary qualification labels. Board IDs, durations, and errors stay
in the evidence; sharing an example does not qualify another board or establish
electrical safety. No UI action automatically sweeps or tunes clocks.

The human-readable [Index baseline table](CLOCK-BASELINE.md) lists the shipped
accepted GH/s, performance-equivalent MHz estimates, and one average whole-miner
power value per index. It is derived from
`clock-baseline.json`; MHz values are not independent frequency measurements.

### Devices display

The Devices **Clock / Index** heading toggles with a rotate icon and retains the
display preference in browser storage only. Index shows the reported sent index,
never a requested value substituted for missing telemetry. Clock preserves the
normal MHz label; custom index 30 shows 400 MHz and other supported indices use
the baseline's performance-equivalent MHz for the confirmed sent index. These
are not measured frequencies. Missing or incompatible data shows a dash.
Clock/Index and Estimate retain their existing headings and show values without
explanatory tooltips. Source/history metadata remains in the status API.
Accepted-window coverage is available on hover or keyboard focus without
permanent second lines. These
tooltips fit their text on one line without a fixed empty width. A partial
15-minute/1-hour window is still identified in those details; the underlying rate
calculation and collection thresholds have not changed.
Rejected and HW Errors also display only counts, with percentages on hover or
keyboard focus for each board and the totals. The existing total-share denominator
is preserved; percentages show `n/a` until that denominator is positive.
Accepted and error-detail tooltips use focusable `bs-tip` styling in both themes,
rather than native browser `title` popups. Clock/Index and Estimate have no
tooltips. The clock toggle has an accessible name identifying the display mode.

### Personal Measured Rates

The minute recorder stores a separate Measured Rates dataset, independent of
raw clock logs and their storage setting. RAM is the default for new datasets;
existing saved SD selections are preserved. Dev Tools -> Logs
provides a confirmed SD/RAM migration, Write buffer, readable text and JSON
downloads, and Clear All Data. Per-slot Board replaced controls are in
Dev Tools -> Boards, beside each board heading, with a board-specific
confirmation. RAM selection survives reboot; its history
does not. Moving a dataset copies and verifies it before retiring the original.
An error after pointer publication keeps the copies available for recovery;
the next successful read resolves the active target rather than deleting the
possibly published copy.
Clearing it also resets live accepted-rate windows, not miner counters or clocks.
Replacing a board resets only that slot's history and installation identity.

Each observation brackets `devs` and `pools` with two `stats` reads. Only
co-snapshotted `DiagAccepted`, `DiagDiffAccepted`, `DiagRejected`, `DiagHwErrors`
and `DiagDeviceElapsed` are used for work deltas. Invalid snapshots, pending or
unconfirmed clock commands, disabled boards, counter resets, process/epoch
changes, clock discontinuities and gaps over 120 seconds restart verified
history collection. Live-window reconnect handling is described below.
Two observed minutes of settling precede recording after a context change.
No raw log is backfilled into this dataset.

Context includes board installation/slot, startup and total chips, algorithm,
running host binary hash, saved bridge-verification identity, normal/experimental
mode, all three sent clock indices, mapping version, cooling configuration and a
salted pool/worker revision. Worker names and credentials are not exported.
The bridge identity is a saved verification record, not a fresh flash readback.
Without that record, valid counter intervals still feed the live Accepted 15m/1h
windows, but are not saved as Measured Rates history or used for personal estimates.
Verification starts a new history context; earlier live intervals are not backfilled.
Board replacement cannot be detected reliably from chip count; use the explicit
reset control even when both boards report 64/64.

Rates use `sum(difficulty) * 2^32 / sum(observed seconds) / 1e9`.
Accepted 15m and 1h show actual observed coverage, not a claimed full window.
At least five observed minutes and 20 accepted shares are required; a valid
five-minute zero-work window reports zero. They are rolling live measurements,
separate from the normal clock-model Estimate and historical personal average.
An sgminer restart or bridge-firmware change can retain recent live intervals
within the same controller boot when the requested clock index, board identity,
chip counts, algorithm, host binary and pool/worker revision still match.
Normal 400 and explicit Index 30 are the same requested profile. This does not
confirm an unacknowledged clock command. A different acknowledged index prevents
retention, and verified history always starts its own new settled context.
The first post-restart snapshot anchors new counters; no delta crosses a restart.
Live intervals age by controller monotonic time, not reset miner uptime. Restart
gaps are excluded from observed coverage, and unavailable boards publish no live
rate. Compatibility is rechecked after an outage of at most 15 minutes; expired
15m/1h intervals are never reused to fill missing time.
History includes rejected shares and hardware errors; they are not silently
filtered out. Temperatures/fan readings are time-weighted sampled observations,
not continuous extrema.

An estimate can be labeled **Your history** only with a matching current context,
at least six observed hours, 10,000 accepted shares and evidence of fixed
difficulty 32 throughout. Otherwise history is **Early history**. The current
miner pool API reports last-share difficulty but not minimum/maximum difficulty;
that does **not** prove fixed difficulty. Consequently current live captures
remain Early history and do not replace Estimate, regardless of runtime. Normal
mode retains **Model**; custom clocks fall back to the matching **Lab baseline**
when qualified personal history is unavailable. Historical
averages do not establish physical MHz, power, safety, or stock equivalence.

Storage uses Python's standard SQLite module, bounded to seven days of hourly
aggregates and 64 contexts with the same 2 MiB database target in RAM and on SD.
A transaction's temporary
rollback journal can add up to roughly another database-sized allocation.
Migration temporarily keeps both databases; allow additional space for copying
and compaction rather than treating 2 MiB as a total filesystem quota.
SD batches flush about every five minutes; Write buffer and the clean-shutdown
service also request a flush. Sudden power loss can lose the pending RAM batch.
RAM pending is capped at 128 intervals/256 KiB and runtime state at 256 KiB.
Full/unwritable/corrupt storage is visible as an error; dropped intervals are
counted, never presented as saved. Save time, buffered count and storage errors
appear under Dev Tools > Logs > Measured Rates, not on the main dashboard.
Devices shows only the hashrates and error counts. Estimate provenance stays in
the status API. Accepted-window coverage appears only while a window is
partial: `8.0 / 15 min sampled (partial window)` means eight minutes contributed
to that rate, not fifteen. A complete window needs no extra tooltip.
Utility is accepted shares per minute, not a clock index; its display is rounded
to two decimals. Rejected and HW error hover/focus details show only percentages.
Compact headings and count-only error cells keep all device
columns visible at desktop widths, with horizontal scrolling on narrow screens.
Utility, navigation and Pools table tooltips share the same styling.
Measured Rates opens only through its storage row's **view** action in Dev Tools
Logs; selecting a text log switches back to the terminal viewer.
The list has a bounded scrolling area with sticky column headers. Download
report exports the same columns, newest first, as an aligned plain-text table
in the configured display timezone (UTC when system time is selected). JSON
retains the machine-readable summary. The Board header selector offers All,
0, 1, and 2; both downloads follow that selection. Filtered reports identify the
board, and JSON limits contexts and board maps to that board while retaining
shared storage metadata. Clear All Data still clears the entire dataset,
regardless of the view filter. Both downloads include the latest
published snapshot without changing storage or clearing history. Write buffer
commits pending intervals to the selected RAM/SD dataset; it is not a download.

The durable pointer and private salt are in
`/opt/scripta/var/measured-rates-control.json`. The active generation's
`measured-rates-<generation>.sqlite` is in that directory or `/run/blakestream`.
Pending/live state and public UI caches remain in `/run/blakestream`. Generation
IDs, shared locks and transaction sequence watermarks prevent stale pending
data from being replayed after a clear or committed twice after a crash.
Image staging removes these personal files and identities before release.
Clear All RAM saves pending SD measurements first, then resets live windows;
Clear All on the active dataset's side removes that dataset. Raw diagnostic
logs retain their separate existing controls.

`ExperimentalClock` and `RequestedClockIndex` identify the active mode in the
API. `RequestedClockIndex` is zero in normal mode; `SentClockIndex` still reports
the actual command index. Custom clocks use the matching shared lab rate and
performance-equivalent MHz instead of the legacy model values. Qualified
personal history takes precedence for hashrate, not MHz. Index 30 retains its
nominal 400 label. Baseline lookup requires compatible live hardware/software
and a verified sent command; it never substitutes a requested index. The
clock editor retains its separately labeled lab reference values. Older API MH/s fields remain for
compatibility and are not calibration evidence. Process, board-epoch,
generation and route changes break counter-delta continuity; verified history
must settle again. Recent live intervals can survive only the compatible
restart/reconnect cases described above. Changed clock, host, board or pool
identity is not treated as continuous accepted work.

Per-board cards show the same current hashrate as the Devices Estimate column,
without a test badge. Idle/offline boards show zero, and unavailable rates show
a dash. Custom-clock graph lines use the cached accepted 15-minute rate, recorded in a separate
`accepted_samples` series, not the firmware's modeled MH/s fields. Normal mode
keeps the model series. Stale/missing samples and context changes break the
accepted line; pre-existing model history is never relabeled as accepted work.
This adds no extra miner API polling.

### Application Backups

Backup captures application settings, the web password, pools/assignments,
miner options, clock profiles, fan/temperature and failover settings, the public
clock baseline, RAM/SD chart and event history, private clock baseline campaigns,
and personal measured-rate data. Measured SQLite, its control pointer and pending
intervals are copied under the service/store locks. Restore preserves the target,
private salt and installation IDs but clears live counters and derived caches,
so pending intervals are not counted twice. This is not a full SD/OS image.
Firmware binaries, firmware attestations, machine identity, raw diagnostic logs
and pending network transactions are not restored.

Archives have a checked manifest, fixed path allowlist and bounded contents:
96 data files, 16 MiB per general file, 32 MiB expanded total and 16 MiB ZIP upload
or download. The measured SQLite database retains its separate 2 MiB limit.
Exceeding a limit fails the operation rather than reporting a partial backup.
Validated legacy configuration ZIPs remain importable. Archives contain private
credentials; keep exported copies private. Local archives live under the root-only
`/var/lib/blakestream-backups` directory and are served through authenticated requests.

Saved Ethernet profile settings are captured as portable data, even when the
cable is disconnected. The backup-only network read excludes pending changes,
unsaved profiles and DHCP leases; it does not activate or modify connections.
During a pending change it captures the saved pre-change configuration. With no
saved active connection, the last confirmed profile is preferred; otherwise a
single saved available profile is required. Static DNS priority is preserved.
Restore stages
`/opt/scripta/etc/network-restore.json`; it never activates an interface.
Settings > Network offers **Review Restored Network** for a matching interface.
Review fills a draft only; Apply and the existing timed confirmation remain
separate. DHCP restores do not pin the old lease or DNS addresses.
Restore does not edit or reload active NetworkManager connections: both Ethernet
and Wi-Fi stay connected. Wireless profiles and credentials remain separate from
application backups. Restoring the web-password hash can change the password
needed for the next login, but does not change either network connection.

The restore confirmation restarts the miner only after a successful restore.
Failed writes attempt rollback using a private journal; a later helper operation
recovers an interrupted restore before proceeding. A reported recovery failure
does not trigger a miner restart. Release-image staging removes private archives,
restored network drafts and personal baseline campaigns, retaining the public
`clock-baseline.json` examples.

The observed mapping must come from controlled accepted-work tests. An index,
a bridge echo, or an ACK is not a measurement of physical MHz. Power remains
unmeasured without a separate meter.

### Bounded hardware trial

`scripts/bkb-clock-trial.py` is an explicit, controller-local Python 2.7/3
tool, separate from default QA. It reads `devs` and `stats`, saves a private
options backup, applies a schedule, restarts the host between legs, and restores
normal 400 on completion or failure. It never flashes the bridge, edits pools,
or tunes the fan. `validate` and a `run` without `--execute` do not mutate the
miner. `observe` checks the current non-experimental baseline.

Each leg requires 120 seconds or more of warm-up and 1,800 to 3,600 seconds
of measured operation. Snapshots are checked every 15 seconds. Missing startup
telemetry is tolerated only within a bounded settling/recovery period, never
inside a measurement window. The default `strict` policy aborts for a
hardware/USB error, chip-count loss, excessive temperature, lost acceptance,
excessive rejected shares, changed identity/route, or an unrelated settings
change. Both wall-monotonic and device elapsed spans must cover the full window.
Counter resets or interrupted windows cannot become successful results.

An explicit schedule field `"hw_policy": "baseline-budget-v1"` permits bounded
measurement-only hardware errors because rare counts were also observed at the
reference setting. Startup and warm-up must still have zero hardware errors.
For a 30-minute window, reaching 2 errors on any board or 3 total aborts the leg;
for a full 60-minute window, the corresponding limits are 3 and 5. Windows shorter
than 60 minutes use the stricter limits. Any rejected share, USB error or lost
chip still aborts under this policy. Reports include both startup counts and
measurement error deltas, plus errors per 1,000 accepted shares. These fixed
limits are operational stop rules, not a statistical proof of equal error rates
or electrical safety. Failed strict-policy runs remain failed, not re-scored.

The opt-in `"hw_policy": "logged-budget-10-v1"` logs counts 1 through 9
and aborts at **10 total HW errors across all three boards per leg**, including
settling and warm-up. Each leg starts a new miner process; entering measurement
does not reset the budget. Precheck and restore use the same cumulative limit,
so one existing error no longer blocks launch, but ten still do. The limit does
not increase for longer windows. USB errors, any rejected share, chip loss,
temperature limits, counter resets and the other identity/acceptance guards
remain unchanged. Old policies and old failed results are not reclassified.

Every sample records its HW policy and per-board counts. Completed results
separate startup/warm-up errors, measured-window deltas and whole-leg totals;
error-bearing results carry `hardware-errors-observed` in `quality_flags`.
A completed test with errors is not a clean calibration or a qualified preset.
Standalone `observe` and `restore` require explicit
`--hw-policy logged-budget-10-v1`; a `run` takes policy only from its schedule.

The runner needs an expected host SHA, matching V1 bridge artifact, a healthy
64/64 baseline on all three boards, and root for `--execute`. Results include
per-board difficulty deltas, share counts, elapsed time, and GH/s. Fewer than
2,000 shares on any board marks that board's result exploratory. The reported
`196/sqrt(share_count)` percentage is a rough counting-noise indicator, not a
general confidence interval for variable share difficulty.

The runner records the reported algorithm and rejects missing/mixed baseline
algorithms or an algorithm change during a trial. Each completed board result
also records `physical_mhz: null` and `frequency_status: "unverified"`.
`nominal_mhz` is 400 only for index 30, otherwise null. None of these fields
turns accepted-work measurements into a physical frequency measurement.

For unattended operation, provide an independent process/reboot recovery
service using `restore --execute --backup ... --host-sha ...`, plus a stale-progress
watchdog. Keep the miner launcher in a separate service/control group from the
trial: killing the trial must not also kill the miner restored by its exit hook.
An approved `--restart` wrapper can launch that separate service. Recovery
restores clock keys and preserves unrelated user changes; a restore failure is
reported, not silently declared successful. Keep the private backup available.

On detect, global mode sends one thermal `SET_OPTION` with clock code 0, then
queues a clock generation for every active board. The generation runs only
when three boards are present and each one is ready. It retries a limited
number of times. Individual mode sends that board's clock during detect
instead of the code-0 placeholder.

Two transports exist inside global mode:

- Bridge-global (`dest=1`) when `/opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY`
  is present. The host sends one `SET_OPTION` and requires one ACK that covers
  all three boards. The guarded flash wrapper publishes the flag after verified
  readback of the release V1 image. First-boot verification can also publish it
  for an already-installed, recognized custom image, recording the hash read
  from the bridge. The runtime builder checks the flag's presence, not the
  digest inside it; it does not perform a fresh firmware read.
- Compatible per-board (`dest=0`) when the flag is absent. Global mode still
  uses one requested clock, but the host talks to each board separately. This
  is the transport for stock bridge firmware, which does not implement
  `dest=1`.

## Accepted-work validation

The shared baseline covers **Index 8..32**, including completed Index 31 and Index 32
observations. Every index has a full one-hour observation under `blake256r8`,
after ten minutes of excluded warm-up, with all three boards at that index.
The [Index baseline table](CLOCK-BASELINE.md) lists the published
accepted GH/s, estimated MHz, and average whole-miner watts. This is limited exploratory screening, not
every-board qualification or proof of long-term stability.

The measurements used a patched host and verified bridge with all boards
reporting 64/64 chips. Exact measured host and bridge identities remain in
`clock-baseline.json`; release compatibility never substitutes a different
hash for that provenance.

Previously reviewed compatible hosts remain listed separately in
`clock-baseline.json` under `compatible_host_sha256`. Shared display compatibility
does not replace the actual measured host identity with a bundled binary's hash.
Unlisted binaries remain incompatible. Personal history still uses exact
running-host identity and is not merged across builds by this list.

The current sweep replaces the earlier mixed-index screening examples. Completed windows
exclude warm-up and retain actual measured duration, accepted difficulty,
share counts, errors, temperatures, identities, and the full operating tuple.
Fan control remained automatic with one unchanged curve. Shared cooling, variable
fan speed, and work delivery can affect rates. Eyedro measured the whole miner's
power feed, including the power supply, controller, and fan; physical ASIC
frequency and individual-board power were not measured.

Index 30 is the normal nominal-400 reference on V1, not a stock-firmware comparison.
Published pooled per-board examples are about **61.64 GH/s** at Index 30,
**61.49 GH/s** at Index 31, and **63.72 GH/s** at Index 32, with average whole-miner
power of **251.5 W**, **256.0 W**, and **259.3 W**, respectively. Index 30 pools the
opening reference, its sweep step, and the closing reference. A Global preview
still multiplies the shared per-board example by three; it is not a live reading.

Interrupted and failed attempts remain in the local trial records and do not
supply baseline rates. Completed error-bearing windows retain their errors and
declared stop policy; they are not relabeled as error-free. This sweep recorded
hardware errors without a count-based stop, while retaining its temperature,
fan, telemetry, and valid-measurement safeguards. Higher-index windows and the
closing reference include recorded hardware errors; completed windows had no
rejected shares. Restoration to
normal clocks, unchanged saved configuration, and renewed accepted work were
checked. These outcomes do not qualify another miner or establish electrical
safety.

Detailed chronology, raw captures, failed attempts, and deployment records are
kept in Git-ignored local history. The published guide describes current
behavior and validation limits rather than individual lab slot assignments.

### Algorithm-specific baseline data

Schedules declare a canonical algorithm, such as `"algorithm": "blake256r8"`.
The API's `Blakecoin` name maps to `blake256r8`; raw diagnostics retain the
reported name. Missing, mixed, or changed algorithms cannot supply this baseline.

`scripts/export-clock-baseline.py` exports completed observations into separate
algorithm namespaces under the chosen root:

`<root>/blake256r8/baseline.json`

It retains accepted GH/s, duration, board/index tuples, identities, HW counts,
exploratory flags, raw-source references, and available measured-power evidence. Writes are atomic and skipped
when data has not changed. Empty datasets contain no invented rates; failed
windows retain separate failure records, not usable measurements. Personal
measured-rate history is not overwritten.

Other algorithm trials must declare and export their own canonical algorithm.
For example, `--algorithm x11` accepts X11 observations, not Blakecoin rows.
The separate public-example publisher currently accepts blake256r8/V1 only;
it does not mix another algorithm into the bundled clock display.

Per-board/Index averages keep algorithm, host/bridge identity, and operating tuple
separate. Unknown identities remain isolated by source run. Rates use accepted
difficulty over measured device time:

`accepted_ghs = total_accepted_difficulty * 2^32 / total_device_seconds / 1e9`

This is not an average of instantaneous dashboard samples or an equally
weighted average of unequal windows. Duplicate reports are not counted twice.
Error counts remain attached, and no average automatically becomes a preset.

The **Est. MHz** readout is unverified: a **performance-equivalent estimate**,
not measured ASIC frequency:

`estimated_mhz = 400 * candidate_accepted_ghs / same_board_reference_accepted_ghs`

The estimate requires completed candidate and all-Index 30 reference windows from
the same physical board, algorithm, and matching recorded context. Index 30 keeps
the nominal 400 MHz anchor. Missing references leave estimates unavailable;
there is no inferred linear clock index formula. The export retains
`estimated_mhz`, `relative_performance_pct`, `reference_accepted_ghs`, and
`estimate_kind`, while `physical_mhz` stays null and `frequency_status`
stays `unverified`.

The public publisher pools compatible completed work per Index, removes private
source paths, and retains contributing measurement identities and errors.
For complete same-index three-board windows it also publishes one
`average_watts` value per index, with `power_scope: whole-miner-wall-power`.
The average is total observed energy divided by total observed time; a meter
window is counted once, not once per board. Missing meter intervals are not
filled, and publication requires at least 99% coverage with no gap over 30 seconds.
Mixed-index or unmetered observations cannot supply a whole-miner power value
for one index. Detailed energy and coverage records remain in the private export,
not extra columns in the public table.
Its shared MHz estimate uses time-weighted references from the contributing
boards. Counting noise, temperature, errors, and work delivery affect these
estimates; they must not be inverted into a calibrated MHz control or presented
as a safety limit.

### Retesting and duration

Record algorithm, pool route, full operating tuple, chip counts, firmware
identity, and fan behavior. Compare accepted difficulty per observed second,
not the model Estimate. Retest useful points with the other boards held fixed
and preserve the original measurement identities in the underlying data.

Use the bounded runner's independent recovery and declared error policy.
Changing policy requires a separate trial, not retroactive acceptance of a
failed run. Chip loss, thermal cutoff, USB/command failure, stalled acceptance,
or an exceeded error budget stops the trial and triggers restoration.
Longer matched windows and repeated references are needed before proposing a
preset; a clean half-hour is not proof of safety or efficiency.

The 25 settings at thirty minutes each require **12.5 board-hours**. Testing
three different settings concurrently needs at least nine rounds: **4.5 measured
wall-clock hours**, or **4 hours 57 minutes** with three-minute warm-ups, before
reference repeats, restarts, and recovery. Testing every index on every board
requires **37.5 board-hours**, at least **12.5 measured wall-clock hours** with
all three working concurrently. Concurrent screening is not every-board
qualification.

### API transport and resource limits

The trial client uses one four-second monotonic deadline for connect, send, and
read. It accepts a complete NUL-terminated frame or clean EOF only after JSON
object validation, retains a 256 KiB reply cap, and does not retry during
measurement. Failure details record command, stage, byte count, elapsed time,
and limit without response contents. A real timeout still aborts and restores
clocks. The Python 2.7 monotonic fallback loads `librt.so.1` directly, without
spawning a library-discovery process for each clock read.

Permission repair and runtime cleanup retain fifteen-minute intervals at
staggered offsets. RAM sgminer logs cap at 4 MiB and retain the newest 2 MiB
when trimmed. These controls reduce resource contention; they do not prove
that every interrupted trial had the same root cause.

**Index 33/34 are unsupported.** Both the host parser and V1 bridge enforce 8..32.
Index 32 is the last recognizable stock Index record; subsequent bytes are not
validated Index entries. Raising an input maximum alone would not add supported
settings. This is a software/table boundary, not a measured physical ASIC limit.
See the [firmware table explanation](firmware-custom/README.md#why-the-range-ends-at-32).

## Fan-only behavior

The Miner subsection is labeled **Fan** when Adjustable Clocks is disabled or
the known bridge identity is stock. Clock inputs and clock profiles are hidden
in that state; fan controls remain available. With Adjustable Clocks enabled
on V1 it is labeled **Clocks & Fan**.

Manual fan settings are limited to 40-100% in the slider, typed entry, saved
options and runtime configuration. Saving applies on the next miner restart.

Automatic fan control allows a 20% minimum; its factory default remains 40%.
This does not verify fan rotation at 20%. The pending dashboard chip replaces
the normal fan/minimum values with `Updating Fan` and a 60-second countdown.
The timer starts when a changed curve is saved, including the wait for cron.
After zero the chip stays pending until the controller processes the request
and sgminer reports the requested fan percentage with no pending command.
The timer is a waiting allowance, not a guaranteed hardware deadline. It follows
the user's saved request across menu navigation, but a full page refresh clears
it. Routine automatic fan adjustments do not start a countdown. Saved request
revisions identify the controller acknowledgement; they do not re-open the timer.

Clock code 0 is fan and cutoff only. v1 does not walk the Index sequence and
does not change the bridge echo for that command. Live fan updates use this
path so a fan change does not pretend to be a clock change. The host fan
controller needs sgminer's API to read board temperatures and queue a target.
It cannot issue live updates while that API is unavailable during a restart;
normal polling resumes when the miner is available. Observed fan movement is
not evidence of a clock-index change or physical PLL lock.

## Thermal protection

Auto Fan defaults to **40% at 36 C**, **75% at 43 C**, and **100% at 50 C**.
Saved curves take precedence over defaults. Automatic minimum fan is 20%; manual
fan remains at least 40%. Target and midpoint temperatures are at least 20 C,
with target below midpoint below full-fan temperature. Typed curve values are
normalized on blur/save, not after each keystroke; graph previews do not change
the input being edited.

The clock form's shared Recover Temp / Cutoff Temp defaults are **40 / 55 C**,
encoded in `baikal-options` as `400:40:55`. These are the miner/bridge temperature
limits, separate from the host watchdog, and accept at most two digits (1-99 C).
The runtime validates recover below
cutoff; changing the form does not update the watchdog configuration.

`temp-watchdog.py` reads `/opt/scripta/etc/temp-config.json`. Its shipped defaults
disable a board at **80 C or above** and recover it at **70 C or below**, polling
every 30 seconds via cron. A board-mask change restarts the shared miner process,
briefly interrupting other boards as well. These are sampled host safeguards,
not independent hardware protection; they depend on working telemetry and host
services. `fan-controller.py` also uses the configured miner cutoff when bounding
its full-fan temperature. Neither layer proves safe operation or compensates
for a failed fan, missing telemetry, or damaged hardware.

## Firmware Operation Timing

Dev Tools displays elapsed time and an estimated total throughout a firmware
operation. Stock and V1 flashes start with the lab's measured **164-second**
baseline (about 170 seconds); a read-only verify defaults to 45 seconds. The
baseline remains visible before any local history exists or if history cannot
be read. When Dev Tools processes a successful result, it replaces only that
operation's estimate with the miner's own duration. A normal flash includes
pausing mining, entering DFU, two matching safety-backup reads, writing the 1 MiB
image, full readback verification, returning to normal USB mode, and the guarded
miner restart. Recovery mode instead reuses a retained validated safety backup;
if none is usable, the validated target can still be written but must verify
before mining resumes. Do not remove power when the estimate is exceeded: it is
a duration hint, not a timeout.

`/opt/scripta/etc/bkb-firmware-runtime.json` retains the last successful duration
separately for `stock`, `custom-v1`, and `verify`. Legacy single-operation records
are migrated on the next successful result; failed operations do not train the
estimate. Image pruning removes this per-miner record. Progress uses server
elapsed time, so browser/controller clock differences do not inflate the timer.

A flash worker reports success only after verified readback and successful
guarded miner restart. The flasher logs its USB-return check, but one early USB
enumeration snapshot does not skip the guarded restart attempt. A verified
write followed by a restart failure is reported as a failed operation. When Dev
Tools processes a failed flash result, it disables adjustable clocks and clears
raw Index overrides without learning the failed duration. For a successful V1
flash, it publishes identity after the capability marker is installed. The SSH flashing client
reads the worker result directly and does not perform this dashboard settings,
identity, or timing reconciliation. Continued accepted work and board health
still require observation after the job completes.

### First boot after an SD reflash

Writing an SD image does not replace the STM32 bridge firmware. A fresh image
stages `bkb-firmware-firstboot.pending`; the first-boot service runs after
hardware initialization and permission repair, before cron and the web server.
It holds the miner-launcher lock and uses the guarded, double-read verification
operation. This temporarily enters DFU and returns the bridge to normal mode,
but never writes firmware or automatically reverts to stock.

An exact recognized Custom V1 hash enables only Adjustable Clocks, with default
Index 30 and `400:40:55` miner options. Verified stock leaves clocks disabled.
Auto Fan and Dev Tools retain their factory defaults; detection does not enable
them. Saved clock overrides are removed. Recognized custom firmware also gets
the `BRIDGE_GLOBAL_V1_READY` capability marker, containing its verified digest.
Unknown firmware, a failed read or a
missing clock encoder leaves clocks disabled and records the failure.

The result is stored in `/var/lib/blakestream-firstboot/result.json`, with
details in `/var/log/blakestream-firstboot/firmware-firstboot.log`. Completed,
ordinary failed, and deliberately skipped checks consume the pending marker;
later boots then preserve saved preferences instead of repeating verification.
A first-boot GPIO/application recovery failure retains or recreates the pending
marker and keeps boot-local cleanup state; this alone does not gate dependent
boot services. A separate persistent firmware-write recovery marker blocks miner
startup and requires successful DFU enforcement before cron starts. A busy
launcher or active firmware job is left alone and recorded as a skipped check.
First-boot cleanup only restores GPIO/application mode, not flash contents. This
identity check is separate from sgminer's subsequent clock-command acknowledgement
and readback confirmation.

## Custom V1 Clock Commands

Stock startup programs each host board as four internal SPI targets, and each
target at selectors 0 through 15. V1 uses that same order when it applies a
non-zero clock code:

- `dest=0` visits the addressed host's four targets (64 slots).
- `dest=1` checks all three hosts first, then visits all 12 targets
  (192 slots) in one handler invocation.
- Each slot's state is read from the stock chip map. States 2 and 3 receive
  the clock command because stock work scheduling includes both. Inactive
  slots are skipped; state 2 is not skipped merely because its startup test
  failed. No slot state or startup-success count is changed by this filter.
- Selector 0 carries the requested clock index. Selectors 1 through 15 use the
  stock fixed register path.
- The stock per-slot delay is retained even for a skipped slot. Healthy
  boards receive all 64 sender calls; a global healthy request receives 192.
- A host's bridge echo advances only when every active slot's sender call
  succeeds. Any active-chip failure rejects the command and withholds that
  host's new echo. A global ACK reports success only if every selected host
  succeeds; a successful board echo alone cannot confirm a rejected global
  command.
- Host state must already be 4 or 5, and every selected target must already
  be ready (state 3 at selector zero), before any SPI write. An inactive
  readiness slot still rejects the request. Prevalidation failure leaves
  all echoes unchanged.

Indices 8 through 32 select the unchanged stock clock table; Index 30 is
the nominal 400 MHz reference. Clock code 0 only updates fan/temperature
settings. Stock thermal protection and fan handling remain intact.

`CommandVerified` and `AppliedClockValid` require a successful command ACK
and matching GET_INFO echo for the requested generation. This confirms the
command path, not physical PLL lock or measured ASIC frequency. A degraded
board can confirm successfully when its active chips respond; its bad-chip
count and lower accepted performance remain visible.

The emulators in `firmware-custom/smoke_assert_v1.py` and
`firmware-custom/smoke_assert_active_slots.py` check ordering, parameters,
inactive-slot selection, active failure rejection, readiness gates, echoes,
fan-only behavior and unchanged chip state. They do not see the SPI wires
or measure the ASIC. Hardware trials confirmed Index 30 on a 56/64 board
and healthy 64/64 boards while accepted shares advanced.

The release image is `firmware-custom/build/bkb-stm32f407-flash-clockmod-v1.bin`,
also staged as `overlay/opt/scripta/firmware/custom.bin`:

- MD5: `18babe0c4c67d9146e230f9dc423f2f2`
- SHA256: `d2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a`

Firmware recognition checks an explicit identity/hash allowlist in
[firmware-identity.php](overlay/var/www/inc/firmware-identity.php) and
[build-miner-runtime.py](overlay/opt/scripta/bin/build-miner-runtime.py), not
the V1 label alone. Recognition is separate from the UI's current-release
check, which requires the release hash above. Readback verification is also
separate from per-board command confirmation.
Stock rollback remains an explicit firmware operation.

Building a new image requires a verified stock dump, available at
`overlay/opt/scripta/firmware/stock.bin`, 1048576 bytes, md5
`d5f844be2952fd713717a71a67e2be61`, sha256
`bc19e0ff3b416a8aa21f47ae3b6f4e19c3c975ea7c1315718d28b10650532e7d`.
`firmware-custom/build.sh` checks that identity and then stops if the
bare-metal compiler is missing. Default QA does not run it. The tracked files
under `firmware-custom/build/` are the reference images.

## Startup count and reported chip count

`GET_INFO` carries two different counts. The reported chip count is the width
of the chip map (64 on a full board). `StartupSuccessCount` is how many chips
the bridge reported as started. The status page shows **Chips** as
startup/total, for example **56/64** or **64/64**, without a tooltip or toggle.
A missing or invalid component is a dash; zero stays zero. The total is never
silently substituted for a missing startup count.

A board can report 64 chips and a startup count of 56. That is a startup
result, not a request to relabel the board as a 56-chip device.

## Accepted-work boundaries

Live Accepted 15m/1h rates come from the Measured Rates service, not raw
clock-history logs or synthetic MH/s. `clock-history-recorder.py` brackets
`devs`/`pools` with `stats` reads and calls `measured_rate_service.Service.record`
before the Adjustable Clocks gate. Accepted-work collection therefore does not
require clock overrides to be enabled. It does not backfill raw logs.
Live windows do not require a bridge-verification record or a successful clock
command. A stable failed clock confirmation does not invalidate accepted-work
counters. A sent index of zero remains unknown, never an inferred index 30.
Pending commands, changing confirmation state, invalid counter snapshots and
changed operating context still prevent intervals from crossing that boundary.
Persistent measurement history and learned estimates require verified bridge
identity, successful clock confirmation and a known all-board index tuple.

`measured_rates.py` accumulates valid co-snapshotted device-counter intervals
after two observed settling minutes. A same-pool reconnect may advance
`DiagRouteEpoch` without resetting live windows when boot/process identity,
clock, pool/worker context and monotonic counters remain consistent. A brief
unavailable sample hides rates without immediately discarding the window;
recovery must pass those checks within 125 seconds (two minute ticks plus
scheduling tolerance). Longer gaps, changed operating context or counter resets
restart live collection unless a compatible firmware/process transition can
retain recent closed intervals as described under Personal Measured Rates.
That transition never contributes a crossing interval; the new counter domain
settles separately while retained intervals age by controller monotonic time.
Verified history still uses its strict route epoch,
120-second gap limit and fresh settling period, with no outage backfill.
Rates require at least 300 observed seconds and 20 accepted
shares, except that a valid zero-work window reports zero after 300 seconds.
For the contributing intervals, GH/s is

```text
sum(accepted_difficulty_delta) * 2^32 / sum(device_elapsed_delta) / 1e9
```

Short windows stay "measuring" and are not a failed clock test. Actual observed
coverage is reported; the 15-minute or one-hour label does not imply a full
window. `Service.publish` writes the version-2 controller cache at
`/run/blakestream/accepted-work-rates.json`. `f_status.php` reads it through
`bs_accepted_rates_by_board` and checks live context compatibility. Version-2
caches older than 120 seconds or timestamped in the future are rejected.

The older PHP `bs_accepted_work_rate` and Python clock-history reducer remain
legacy helpers; they are not the current live rate producer. Tests on a
development machine use fixtures, not a live miner, to verify these contracts.

The diagnostics report can compare windows only when the captured driver
snapshots and the operator's deployment, read-back, and cold-start notes say
the run is controlled. A firmware file hash is not a chip read-back. The
report is supposed to stay inconclusive when those conditions are missing.

## Unified board diagnostics

Dev Tools has one Boards tab for the activity grids and board health. The
board filter selects both sections. Refresh reads both endpoints through the
activity polling cycle; leaving the tab or destroying the controller ignores
late responses. An old saved Activity-tab selection migrates to Boards.
Totals, deltas, trace export, enable/disable, and confirmed miner restart stay
available. Opening the tab only reads data; it does not change mining settings.

### Network probes

Network groups saved pool profiles by canonical host and TCP port, across all
categories. For example, three AT1 worker profiles using the same host and port
produce one row containing all three names, and one five-sample TCP latency
probe plus an ICMP check. Different ports stay separate. Hostnames are compared
case-insensitively without a trailing DNS dot; IP literals use PHP's native
address parser. DNS aliases are not merged by their current resolved address.
Ambiguous numeric IP forms and malformed ports are rejected rather than silently
probing another destination. IPv6 TCP checks use bracketed socket addresses;
ICMP remains best-effort and requires the platform's ping support.

The UI sends stable endpoint keys, not saved-profile positions. Reordering or
removing profiles during a run cannot redirect a request to a different host.
The all-endpoint API and the legacy numeric selector use the same grouped list.
Repeated clicks cannot start overlapping runs in the same view.
Probes perform bounded connection attempts and ping; they do not write network
profiles, change DHCP/static mode, or restart NetworkManager. An offline pool
should produce a timeout. The available logs do not establish that probing
caused the separate NetworkManager termination.

### Selectable diagnostic logs

Logging runs independently of the Dev Tools toggle. The toggle only exposes
the tools and their endpoints; hiding them does not stop capture or reset saved
RAM/SD destinations. Fresh images default to RAM except for the existing SD
security audit log.

Sgminer always starts with text log capture. Its default RAM log at
`/run/blakestream-miner/sgminer.log` caps at 4 MiB and keeps the newest 2 MiB when
trimming. An explicit SD selection writes `/var/log/blakestream-miner/sgminer.log` with the same
4 MiB cap and 2 MiB retained tail. Both paths use the same bounded writer;
there are no additional daily sgminer archives. Changing
sgminer's destination takes effect on its next restart, not on a Dev Tools
visibility change. Logs in the other sealed destination are retained; legacy
logs from shared writable paths are never imported.

Dev Tools > Logs provides independent RAM/SD destinations for Network / Wi-Fi
(`network-events.log`), System events (`system-events.log`), Fan / temperature
(`cooling-events.log`), and Web server errors (`lighttpd-error.log`). Each defaults
to RAM. Files live in `/run/blakestream` or `/opt/scripta/var`, cap at 1 MiB,
and retain the newest half when trimming. Destination changes copy existing
events before retiring the old file; the collector, moves, and clears share a
lock. View, Download Logs, and Download diagnostics use these retained files.

`diagnostic-log-recorder.py` runs as `www-data` at seconds 10 and 40. It reads
the journal using a cursor, captures cooling-state transitions rather than
temperature samples, and omits sensitive messages and URL query strings.
Lighttpd sends errors to the journal; changing their retained destination does
not restart any service. Kernel watchdog tick spam and routine login sessions
are excluded. Each run handles at most 2,000 journal records; input truncation
is noted in System events. Events not yet collected can be lost on power loss.

Logs use readable source names and event descriptions. Helper JSON is parsed
into settings-change and result messages; idle rollback checks and unchanged
transaction countdowns/results are not events. The helpers' empty adapter and
interface placeholders are not treated as live network status. Routine rollback
service starts are omitted, while actual service failures remain visible.
Existing RAM/SD diagnostic entries are converted once without resetting the
journal cursor. Network driver errors retain their technical details.

Collector heartbeat/cursor state stays in RAM and feeds the existing compact
failure/stall warning. Pool History shows `waiting for update` while its first
sample is pending during startup, allowing its five-minute cadence plus one
scheduler tick. Real errors and overdue collectors keep their warnings.
Security audit storage remains on SD; live temperature
state and web access logging are unchanged. Image preparation removes these
diagnostic logs and their cursor state.

### Controller memory

Lighttpd runs two PHP workers, recycling each after 100 requests so decoded
history pages do not leave four large worker heaps resident indefinitely.
The hashrate-chart endpoint validates the selected history file, retains board
metadata and sample offsets, then streams samples from small decoded batches. Both passes use
the same open file so an atomic recorder update cannot mix generations.
Hour, day and week responses retain their model samples, accepted-work samples,
null gaps and pool labels without decoding the entire week into PHP arrays.
Clock History similarly keeps compact sample references for sorting and run
grouping, decoding bounded batches when aggregating or emitting each board.
Selective clears share the scheduled collector's lock, stage all destinations,
and retain rollback copies until replacement succeeds. Invalid source data or
a detected concurrent replacement makes a selective clear fail without
overwriting the source. Read-only history requests release the session lock
before processing so other tabs in the same login can continue polling.
Clock and board-diagnostics collection share sample field names and repeated
strings in memory, then encode samples in batches of 128 through `history_json.py`.
Board diagnostics also packs its retained per-sample counter maps. This avoids
retaining large dictionaries and a second complete encoded history during each
save. The JSON schemas, individual samples, seven-day clock history, 18-hour
board-diagnostics history and storage selections are unchanged. Failed writes
leave the previous file intact. The diagnostics collector caches its Python 2
monotonic-clock binding to `librt.so.1`, without spawning library-discovery tools;
`/proc/uptime` remains the fallback. Automatic package updates and independent
fan, temperature and network rollback checks are not disabled or deferred.
The sgminer log writer requests the same low-memory-killer protection as
sgminer itself; diagnostic collectors remain unprotected. System Events
includes explicit miner-restart messages and legacy-kernel low-memory victim
messages, subject to the normal bounded log retention.

Log capture briefly retries transient output failures. If storage remains
unwritable, it continues draining miner output and counts discarded log bytes
instead of waiting indefinitely for storage. Warnings are rate-limited; when
writing recovers, the log receives a gap marker and the journal records the
discarded-byte count. No unbounded backlog is kept in RAM. Trimming streams the
newest 2 MiB through a scratch file and replaces the log only after a complete
write; the log remains capped at 4 MiB on RAM or SD. The temporary tail needs
up to another 2 MiB plus its trim marker while replacement is in progress.
The miner watchdog verifies pipe-reader ownership and can reattach capture
without restarting sgminer. It uses the running pipeline's destination, not a
newly selected destination that has not yet taken effect. Unknown readers or
incomplete process inspection prevent automatic reattachment. Recovery cannot
reconstruct bytes already lost when the previous reader exited. Existing
firmware-operation guards still defer watchdog recovery while flashing.

The footer uses `MemAvailable` where the kernel provides it. On older kernels,
it estimates available memory from free pages, buffers and file cache,
excluding `Shmem` from cache because RAM-backed files cannot simply be
discarded. This is still an estimate, not a guarantee that an allocation will
succeed. Controller uptime comes from `/proc/uptime`; days do not wrap and
hours are displayed within a day.

### Clock-history logs

Dev Tools > Logs defaults clock-history files to a readable UTC timeline. Each
record identifies the board, mode, requested override, sent clock index, command
confirmation, temperature, fan percentage, and accepted/rejected/error counters.
Normal mode's stored zero override is displayed as `none(normal)`, not Index 0.
The bridge echo is a status label, not a physical MHz measurement. Missing
fields remain `n/a`; no hashrate is inferred from these counters in this view.

The readable view shows at most 300 recent records, with a 2 MiB input limit
and 256 KiB output limit. Invalid or oversized documents show an error instead
of treating a chopped JSON tail as complete data. Raw JSON keeps the existing
bounded tail view, and Download JSON retrieves the original full document.
Changing the view format does not modify or clear recorded history.

## Hardware fault isolation

Startup chip count and `SET_OPTION` status help distinguish board health from
clock-command confirmation. A fault that follows a physical board to another
logical position points toward that board rather than the original position.
Local replacement testing restored full chip counts and confirmed commands
before the accepted-work trials.

Successful command confirmation does not establish stock/V1 frequency
equivalence or measure the oscillator. Keep physical-board identity in private
diagnostics when investigating chip loss or command failures.

## Tests you can run without hardware

```bash
scripts/qa
```

That command is enough for PHP, Node, Python, and the v1 emulator. It is
described in [QA.md](QA.md). After you fetch and patch sgminer:

```bash
scripts/qa native native-tsan driver
```

Host and ARM build commands are in
[sgminer-build/BUILD-SOURCE.md](sgminer-build/BUILD-SOURCE.md). Default QA
does not build those binaries and does not talk to hardware.

The optional `scripts/qa firmware-container` phase exercises the firmware web
endpoints, flash/recovery scripts, sudo rules, and screen/user permissions with
simulated USB/DFU and miner hardware. Prepare and run it on a build machine as
described in [Container integration](QA.md#container-integration). It does not
flash a physical miner or validate a complete SD-card image.

## Optional hardware steps and rollback

Physical flashing is not part of QA or of the source recipe. The STM32 tools live in
`firmware-custom/flash/`. They require an explicit miner address and an
explicit `--confirm` before a write. A write is intended to be reversible in
two ways: a pre-write read-back of whatever is on the chip, and a later
restore of the verified stock dump. Read `firmware-custom/flash/README.md`
before using them. If the USB bridge does not enumerate, a power cycle may
help; persistent failure can require manual BOOT0/NRST ROM DFU recovery and a
verified stock rewrite. Neither recovery path is guaranteed by a successful
pre-flash backup.

Do not treat a bench flash as proof of PLL lock. Further hardware checks are:

1. Logic-analyzer capture of the SPI frame, compared with the emulator's
   expected selector order.
2. Repeated controlled accepted-work windows with generation, pool, and process
   epoch held still. The bounded trial runner can automate these; its full
   30-60-minute requirement is stronger than the UI's 300-second display rule.
3. The same window on stock bridge firmware, then on v1, without changing
   pools or the fan policy between them.
4. Rollback to the stock dump if the bridge stops answering.

The SD-card image build in [BUILD.md](BUILD.md) is also optional. It
downloads the factory SD image, needs root to loop-mount it, and copies the
current overlay tree. It does not rebuild sgminer or the STM32 image unless you
run those recipes yourself.
