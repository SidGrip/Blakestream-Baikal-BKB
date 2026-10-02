#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build-miner-runtime.py - Blakestream-GaintB

Compatible with Python 2.7+ and Python 3 (the Baikal device runs Python 2.7).

Reads:
  /opt/scripta/etc/saved-pools.json       - user pool catalog (categories + pools)
  /opt/scripta/etc/board-assignments.json - board id -> pool id (or null = idle)
  /opt/scripta/etc/miner.options.json     - shared sgminer options
  /run/blakestream/temp-state.json        - runtime temp-watchdog board state

Writes:
  /tmp/scripta-runtime-miner.conf       - sgminer JSON config for the launcher
  /tmp/scripta-runtime-poolmap.json     - v2: saved pool id <-> runtime pool_no
                                          sidecar for the apply-board-assignments
                                          ascpool control plane

v2 (per-ASC routing): we emit a sidecar JSON next to the runtime conf that
maps each saved pool id to the sgminer runtime pool_no it ended up at, plus
the inverse and the list of backup pool_nos. apply-board-assignments.py reads
this after the API is up so it can resolve user-saved pool ids to the
runtime pool_no the new ascpool|<asc>,<poolno> command needs.

Sidecar schema:
  {
    "runtime_pool_count": N,
    "saved_to_runtime": {"pool-eu3": 0, "pool-at1": 1, ...},
    "runtime_to_saved": {"0": "pool-eu3", "1": "pool-at1", ...},
    "backup_pool_nos":  [2, 3, ...]
  }

Both files are written atomically (temp + rename). If the sidecar fails to
write after the runtime conf is in place, we delete any pre-existing sidecar
so the applier fails-closed on missing file (no chance of reading a stale
sidecar that doesn't match the current runtime conf) and we exit non-zero so
the launcher refuses to start sgminer.

Plan B (sgminer quota mode): the BK-B exposes all 3 hash boards via ONE USB
device (the STM32F407 bridge), so sgminer cannot pin per-board. Instead we
run a SINGLE sgminer instance with all unique pools the user has assigned,
each weighted by 'quota:N' (where N is the number of boards assigned to that
pool). sgminer's --load-balance strategy distributes work units across pools
proportionally. v2 layers true per-ASC pool routing on top via ascpool.

Exits:
  0 = success (config + sidecar written)
  1 = error
  2 = no boards have pools (idle, caller should not start sgminer)
"""

from __future__ import print_function
import json
import os
import re
import sys
import collections

ETC_DIR = "/opt/scripta/etc"
RUNTIME_DIR = "/run/blakestream"
SAVED_POOLS = os.path.join(ETC_DIR, "saved-pools.json")
ASSIGNMENTS = os.path.join(ETC_DIR, "board-assignments.json")
OPTIONS = os.path.join(ETC_DIR, "miner.options.json")
BRIDGE_GLOBAL_V1_READY = "/opt/scripta/firmware/BRIDGE_GLOBAL_V1_READY"
TEMP_STATE = os.path.join(RUNTIME_DIR, "temp-state.json")
FAILOVER_CONFIG = os.path.join(ETC_DIR, "failover-config.json")
RUNTIME_CONF = "/tmp/scripta-runtime-miner.conf"
RUNTIME_POOLMAP = "/tmp/scripta-runtime-poolmap.json"
BAIKAL_CLOCK_DEFAULT = 400
BAIKAL_VALIDATED_CLOCKS = (400,)
BAIKAL_RECOVER_DEFAULT = 40
BAIKAL_CUTOFF_DEFAULT = 55
BAIKAL_TEMP_MIN = 1
BAIKAL_TEMP_MAX = 99
BKB_DEV_CHIP_MD5 = "0e8467eddabf589ad94549a8d572c759"
BKB_V1_CHIP_MD5 = "18babe0c4c67d9146e230f9dc423f2f2"
# \Z, not $, so a final newline is not an exact index tuple.
EXPERIMENTAL_CLOCK_INDEX_RE = re.compile(
    r"^(?:[8-9]|[1-2][0-9]|3[0-2])(?::(?:[8-9]|[1-2][0-9]|3[0-2])){2}\Z")
CLOCK_SCOPE_KEY = "blakestream-clock-scope"

# Public labels are accepted-work-validated lookup entries. Raw clock indices are
# a separate experimental option and are not a MHz scale.


def _unlink_if_exists(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def write_json_atomic(path, data):
    """Atomic temp + rename. Raises on failure."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.rename(tmp, path)


def load_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except IOError:
        if default is not None:
            return default
        raise
    except ValueError as e:
        print("ERROR parsing %s: %s" % (path, e), file=sys.stderr)
        sys.exit(1)


def int_or_default(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_baikal_options(value):
    clock = BAIKAL_CLOCK_DEFAULT
    recover = BAIKAL_RECOVER_DEFAULT
    cutoff = BAIKAL_CUTOFF_DEFAULT
    text = "" if value is None else str(value).strip()

    if text:
        parts = text.split(":")
        if len(parts) >= 3:
            clock = int_or_default(parts[0], clock)
            recover = int_or_default(parts[1], recover)
            cutoff = int_or_default(parts[2], cutoff)
        elif len(parts) >= 2:
            # Backward-compatible old format: recover:cutoff.
            recover = int_or_default(parts[0], recover)
            cutoff = int_or_default(parts[1], cutoff)
        elif len(parts) == 1:
            clock = int_or_default(parts[0], clock)

    if clock not in BAIKAL_VALIDATED_CLOCKS:
        clock = BAIKAL_CLOCK_DEFAULT
    clock = ((clock + 5) // 10) * 10
    if recover < BAIKAL_TEMP_MIN:
        recover = BAIKAL_TEMP_MIN
    if recover > BAIKAL_TEMP_MAX:
        recover = BAIKAL_TEMP_MAX
    if cutoff < BAIKAL_TEMP_MIN:
        cutoff = BAIKAL_TEMP_MIN
    if cutoff > BAIKAL_TEMP_MAX:
        cutoff = BAIKAL_TEMP_MAX
    if recover >= cutoff:
        recover = BAIKAL_RECOVER_DEFAULT
        cutoff = BAIKAL_CUTOFF_DEFAULT

    return "%d:%d:%d" % (clock, recover, cutoff)


def normalize_baikal_clock_mode(value, has_saved_mode, has_board_clocks):
    if value in ("global", "individual"):
        return value
    if not has_saved_mode and has_board_clocks:
        return "individual"
    return "global"


def normalize_baikal_board_clocks(value):
    text = "" if value is None else str(value).strip()
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError("baikal-board-clocks must be board0:board1:board2")

    clocks = []
    for part in parts:
        if not re.match(r"^[0-9]+$", part):
            raise ValueError("baikal-board-clocks values must be integers")
        clock = int(part)
        if clock not in BAIKAL_VALIDATED_CLOCKS:
            raise ValueError("baikal-board-clocks values must use a validated clock")
        clock = ((clock + 5) // 10) * 10
        clocks.append(clock)
    return "%d:%d:%d" % tuple(clocks)


class ExperimentalClockError(Exception):
    pass


def _exact_text(value):
    """Real text only. Booleans, numbers, and null are not indices or flags."""
    if isinstance(value, bool):
        return None
    try:
        text_types = (str, unicode)  # Python 2 str is bytes; json uses unicode.
    except NameError:
        text_types = (str,)
    if isinstance(value, text_types):
        return value
    return None


def parse_experimental_flag(value):
    if value is True:
        return True
    if value is False:
        return False
    text = _exact_text(value)
    if text == "true":
        return True
    if text == "false":
        return False
    raise ExperimentalClockError(
        "baikal-experimental-clock-index must be a boolean or the string true or false")


def parse_board_clock_index(value):
    text = _exact_text(value)
    if text is None or EXPERIMENTAL_CLOCK_INDEX_RE.match(text) is None:
        raise ExperimentalClockError(
            "baikal-board-clock-index must be three integers 8..32 such as 29:30:30")
    return text


def parse_clock_scope(value):
    """Presentation only: global or individual. Not a frequency and not a mode."""
    text = _exact_text(value)
    if text != "global" and text != "individual":
        raise ExperimentalClockError(
            "blakestream-clock-scope must be global or individual")
    return text


def board_clock_indices_equal(clock_index):
    parts = clock_index.split(":")
    return len(parts) == 3 and parts[0] == parts[1] == parts[2]


def firmware_supports_clock_index(paths=None):
    if paths is None:
        paths = (
            os.path.join(RUNTIME_DIR, "bkb-firmware-identity.json"),
            os.path.join(ETC_DIR, "bkb-firmware-identity.json"),
        )
    for path in paths:
        try:
            with open(path, "r") as handle:
                record = json.load(handle)
        except (IOError, OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        chip = _exact_text(record.get("chip_md5"))
        # The current RAM record takes precedence over a stale SD fallback.
        images = {BKB_DEV_CHIP_MD5: "CUSTOM V1", BKB_V1_CHIP_MD5: "CUSTOM V1"}
        return (chip is not None and chip.lower() in images and
                record.get("identity") == images[chip.lower()])
    return False


def adjustable_clocks_enabled(path=None):
    if path is None:
        path = os.path.join(ETC_DIR, "scripta.conf")
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("userAdjustableClocks") is True


def _saved_option_items(options_list):
    items = options_list
    if isinstance(options_list, dict):
        items = [{"key": key, "value": value} for key, value in options_list.items()]
    if not isinstance(items, list):
        raise ExperimentalClockError("miner options must be a list or object")
    return items


def explicit_saved_clock_mode(options_list):
    """Return baikal-clock-mode only when the saved list contains it once.

    A repeated entry is ambiguous. It must not authorize experimental Index,
    even when every copy is the string individual. Absence stays None so a
    legacy individual inference cannot enable the experiment either.
    """
    found = []
    for item in _saved_option_items(options_list):
        if not isinstance(item, dict) or "key" not in item or "value" not in item:
            continue
        if item["key"] == "baikal-clock-mode":
            found.append(item["value"])
    if len(found) != 1:
        return None
    return found[0]


def load_saved_options(options_list):
    """Flatten miner.options.json. Duplicate experimental keys fail closed."""
    options = {}
    mode_count = 0
    for item in _saved_option_items(options_list):
        if not isinstance(item, dict) or "key" not in item:
            continue
        key = item["key"]
        if key == CLOCK_SCOPE_KEY:
            if CLOCK_SCOPE_KEY in options:
                raise ExperimentalClockError(
                    "blakestream-clock-scope must appear once")
            if "value" not in item:
                raise ExperimentalClockError(
                    "blakestream-clock-scope must be global or individual")
            parse_clock_scope(item["value"])
        if "value" not in item:
            continue
        if key in ("baikal-experimental-clock-index", "baikal-board-clock-index") and key in options:
            raise ExperimentalClockError("%s must appear once" % key)
        if key == "baikal-clock-mode":
            mode_count += 1
        options[key] = item["value"]
    if 'baikal-fan' in options:
        options['baikal-fan'] = str(max(40, min(100, int_or_default(options['baikal-fan'], 70))))
    flag = options.get("baikal-experimental-clock-index")
    if mode_count > 1 and (flag is True or flag == "true"):
        raise ExperimentalClockError(
            "duplicate baikal-clock-mode cannot authorize experimental Index")
    return options


def apply_experimental_clock_index(options, explicit_clock_mode, identity_paths=None,
                           scripta_conf=None):
    """Rewrite the experimental keys in the runtime option dict.

    explicit_clock_mode is the single saved mode, or None when that key was
    absent or repeated. A repeated mode cannot authorize this experiment.
    A legacy inference of individual mode is not accepted. Both keys are
    removed when experimental mode is off, and also when a well-formed enable
    is not allowed to launch. Malformed values raise instead of being clamped.

    blakestream-clock-scope is presentation metadata, not an sgminer option.
    It is validated and then removed. Global scope with the experiment on
    requires three equal indices. Absent scope keeps the legacy behavior.
    """
    scope = None
    if CLOCK_SCOPE_KEY in options:
        scope = parse_clock_scope(options.get(CLOCK_SCOPE_KEY))

    flag_present = "baikal-experimental-clock-index" in options
    clock_index_present = "baikal-board-clock-index" in options
    if not flag_present and not clock_index_present:
        options.pop(CLOCK_SCOPE_KEY, None)
        return "off"

    flag = False
    clock_index = None
    if flag_present:
        flag = parse_experimental_flag(options.get("baikal-experimental-clock-index"))
    if clock_index_present:
        clock_index = parse_board_clock_index(options.get("baikal-board-clock-index"))
    if flag and not clock_index_present:
        raise ExperimentalClockError(
            "baikal-experimental-clock-index requires baikal-board-clock-index")
    if clock_index_present and not flag:
        raise ExperimentalClockError(
            "baikal-board-clock-index requires baikal-experimental-clock-index")
    if flag and explicit_clock_mode != "individual":
        raise ExperimentalClockError(
            "baikal-experimental-clock-index requires explicit baikal-clock-mode individual")
    if flag and scope == "global" and not board_clock_indices_equal(clock_index):
        raise ExperimentalClockError(
            "blakestream-clock-scope global requires three equal clock indices")

    options.pop(CLOCK_SCOPE_KEY, None)
    options.pop("baikal-experimental-clock-index", None)
    options.pop("baikal-board-clock-index", None)
    if not flag:
        return "off"
    if not (firmware_supports_clock_index(identity_paths) and
            adjustable_clocks_enabled(scripta_conf)):
        sys.stderr.write(
            "WARN: experimental Index omitted; verified custom firmware and "
            "Adjustable Clocks are required\n")
        return "omitted"

    options["baikal-experimental-clock-index"] = True
    options["baikal-board-clock-index"] = clock_index
    return "enabled"


def index_pools_by_id(saved_pools_doc):
    """Return dict: pool_id -> pool_object (with category context if needed)."""
    out = {}
    for cat in saved_pools_doc.get("categories", []):
        for pool in cat.get("pools", []):
            pid = pool.get("id")
            if pid:
                out[pid] = pool
    return out


def pool_endpoint_key(url):
    """Normalize a stratum URL down to the endpoint identity host:port."""
    if not url:
        return ""
    s = "%s" % url
    s = re.sub(r"^https?://", "", s, flags=re.I)
    s = re.sub(r"^quota:\d+;", "", s, flags=re.I)
    s = re.sub(r"^stratum\+(tcp|ssl|tls)://", "", s, flags=re.I)
    s = s.split("/", 1)[0]
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    return s.rstrip("/ \t\r\n").lower()


def main():
    saved = load_json(SAVED_POOLS, default={"categories": []})
    assignments = load_json(ASSIGNMENTS, default={"0": None, "1": None, "2": None})
    options_list = load_json(OPTIONS, default=[])
    temp_state = load_json(TEMP_STATE, default={})
    failover = load_json(FAILOVER_CONFIG, default={"enabled": True, "primary_quota_multiplier": 10})

    pool_index = index_pools_by_id(saved)

    # Coerce assignment values to the canonical {primary, failover} shape.
    # Accepts legacy string-shape {"0": "pool-id", ...} for backward compat.
    # Use truthiness rather than isinstance(..., str) — Python 2 returns
    # unicode (not str) from json.load, which would fail the type check.
    def _slot(v):
        if isinstance(v, dict):
            return (v.get("primary") or None, v.get("failover") or None)
        return (v or None, None)

    # Group boards by primary pool id (skip null/orphaned/temp-disabled), and
    # collect each board's explicit failover (if any).
    by_pool = collections.OrderedDict()
    explicit_failover_by_board = {}      # board_id -> failover pool id (only if known)
    boards_with_explicit_failover = set()  # boards whose backup is user-pinned
    for board_id_str in sorted(assignments.keys()):
        ts = temp_state.get(board_id_str, {}) or {}
        if ts.get("disabled"):
            sys.stderr.write("INFO: board %s skipped by temp watchdog\n" % board_id_str)
            continue
        primary_id, failover_id = _slot(assignments.get(board_id_str))
        if not primary_id:
            continue
        if primary_id not in pool_index:
            print("WARN: board %s assigned to unknown primary pool id %s, skipping"
                  % (board_id_str, primary_id), file=sys.stderr)
            continue
        by_pool.setdefault(primary_id, []).append(board_id_str)
        if failover_id and failover_id in pool_index:
            primary_algo = pool_index[primary_id].get("algo")
            failover_algo = pool_index[failover_id].get("algo")
            primary_endpoint = pool_endpoint_key(pool_index[primary_id].get("url"))
            failover_endpoint = pool_endpoint_key(pool_index[failover_id].get("url"))
            if failover_id == primary_id:
                sys.stderr.write("WARN: board %s failover == primary, ignoring\n" % board_id_str)
            elif primary_endpoint and failover_endpoint and primary_endpoint == failover_endpoint:
                sys.stderr.write("WARN: board %s failover endpoint == primary endpoint, ignoring\n" % board_id_str)
            elif primary_algo and failover_algo and primary_algo != failover_algo:
                sys.stderr.write("WARN: board %s failover algo (%s) != primary algo (%s), ignoring\n"
                                 % (board_id_str, failover_algo, primary_algo))
            else:
                explicit_failover_by_board[board_id_str] = failover_id
                boards_with_explicit_failover.add(board_id_str)
        elif failover_id:
            sys.stderr.write("WARN: board %s failover pool %s unknown, ignoring\n"
                             % (board_id_str, failover_id))

    if not by_pool:
        print("INFO: no boards assigned to any pool — nothing to launch", file=sys.stderr)
        # Drop both the runtime conf and the poolmap sidecar so neither a
        # stale conf nor a stale sidecar from a previous run can leak into
        # the next launch.
        _unlink_if_exists(RUNTIME_CONF)
        _unlink_if_exists(RUNTIME_POOLMAP)
        sys.exit(2)

    # Convert miner.options.json (list of {key, value}) into a flat dict.
    # Capture the saved clock mode before legacy individual inference.
    try:
        options = load_saved_options(options_list)
    except ExperimentalClockError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        _unlink_if_exists(RUNTIME_CONF)
        _unlink_if_exists(RUNTIME_POOLMAP)
        sys.exit(1)
    # Count the saved list. The flattened dict would keep only the last mode.
    explicit_clock_mode = explicit_saved_clock_mode(options_list)

    has_saved_clock_mode = "baikal-clock-mode" in options
    has_board_clocks = "baikal-board-clocks" in options
    clock_mode = normalize_baikal_clock_mode(
        options.get("baikal-clock-mode"),
        has_saved_clock_mode,
        has_board_clocks)
    options["baikal-options"] = normalize_baikal_options(options.get("baikal-options"))
    options["baikal-clock-mode"] = clock_mode
    if os.path.isfile(BRIDGE_GLOBAL_V1_READY):
        options["baikal-bridge-global"] = True
    else:
        options.pop("baikal-bridge-global", None)
    if clock_mode == "individual":
        if not has_board_clocks:
            options["baikal-board-clocks"] = ":".join(
                [str(BAIKAL_CLOCK_DEFAULT)] * 3)
        try:
            options["baikal-board-clocks"] = normalize_baikal_board_clocks(
                options.get("baikal-board-clocks"))
        except ValueError as e:
            print("ERROR: %s" % e, file=sys.stderr)
            _unlink_if_exists(RUNTIME_CONF)
            _unlink_if_exists(RUNTIME_POOLMAP)
            sys.exit(1)
    else:
        # Retain board values in miner.options.json, but do not expose them to
        # sgminer while the global clock is active.
        options.pop("baikal-board-clocks", None)

    try:
        apply_experimental_clock_index(options, explicit_clock_mode)
    except ExperimentalClockError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        _unlink_if_exists(RUNTIME_CONF)
        _unlink_if_exists(RUNTIME_POOLMAP)
        sys.exit(1)
    # Validated above. Never copy the presentation key into sgminer config.
    options.pop(CLOCK_SCOPE_KEY, None)

    # Determine explicit failover backups. Only pools selected in the dashboard's
    # FAILOVER lane are added as backups. An empty failover slot means the board
    # should stop if its primary is unavailable, not auto-search another pool.
    multiplier = int(failover.get("primary_quota_multiplier", 10))
    if multiplier < 1:
        multiplier = 1

    primary_pool_ids = set(by_pool.keys())

    backup_pools = []
    backup_pool_ids_seen = set()

    explicit_failover_pool_ids = set()
    for bid, fid in explicit_failover_by_board.items():
        explicit_failover_pool_ids.add(fid)
    for fid in explicit_failover_pool_ids:
        if fid in primary_pool_ids:
            # Already in pools_out as a primary; sgminer will share it.
            continue
        if fid in backup_pool_ids_seen:
            continue
        backup_pool_ids_seen.add(fid)
        backup_pools.append(pool_index[fid])

    # Build the pools array. Each unique assigned pool becomes one entry.
    # Quota = number of boards assigned to it × multiplier (when failover on
    # and there are backups), otherwise just the board count.
    #
    # Also track the saved-pool-id -> runtime pool_no mapping as we go. The
    # runtime pool_no is the index of the pool in pools_out, which is also
    # what sgminer reports as POOL=N in the API. apply-board-assignments.py
    # uses this mapping to translate user-saved pool ids into the integer
    # pool_no the new ascpool|<asc>,<poolno> command needs.
    pools_out = []
    saved_to_runtime = collections.OrderedDict()
    backup_pool_nos = []
    has_backups = len(backup_pools) > 0
    # Load-balance + quota prefixes are only correct when boards are split
    # across multiple PRIMARY pools and we want sgminer to weight work by
    # quota. With per-asc routing engaged, a single-primary + failover
    # layout must NOT set load-balance: doing so makes sgminer round-robin
    # work across primary AND failover pools by their quotas even while
    # the primary's stratum is healthy, defeating per-asc pinning.
    use_quota = len(by_pool) > 1
    for idx, (pid, boards) in enumerate(by_pool.items()):
        pool = pool_index[pid]
        url = pool.get("url", "")
        if use_quota:
            quota = len(boards) * (multiplier if has_backups else 1)
            url = "quota:%d;%s" % (quota, url)
        entry = {
            "url": url,
            "user": pool.get("user", ""),
            "pass": pool.get("pass", "x"),
            "algo": pool.get("algo", "blake256r8"),
            "extranonce": pool.get("extranonce", False),
            "priority": str(idx),
        }
        pools_out.append(entry)
        saved_to_runtime[pid] = idx

    # Append backup pools (always quota:1 — the floor). When primaries die,
    # sgminer redistributes their quota among the surviving alive pools.
    if has_backups:
        next_idx = len(pools_out)
        for pool in backup_pools:
            url = "quota:1;%s" % pool.get("url", "")
            entry = {
                "url": url,
                "user": pool.get("user", ""),
                "pass": pool.get("pass", "x"),
                "algo": pool.get("algo", "blake256r8"),
                "extranonce": pool.get("extranonce", False),
                "priority": str(next_idx),
            }
            pools_out.append(entry)
            bpid = pool.get("id")
            if bpid:
                saved_to_runtime[bpid] = next_idx
            backup_pool_nos.append(next_idx)
            next_idx += 1

    # Build runtime config. Inherit shared options + load-balance flag if multi-pool.
    runtime = {}
    runtime.update(options)
    runtime["pools"] = pools_out
    if use_quota:
        runtime["load-balance"] = True
    if has_backups:
        runtime["_failover_backup_count"] = len(backup_pools)
    # Make sure essential defaults exist
    runtime.setdefault("api-port", "4028")
    runtime.setdefault("api-allow", "W:127.0.0.1")
    runtime.setdefault("scan-time", "0")
    runtime.setdefault("no-submit-stale", True)

    # Write the runtime conf atomically
    try:
        write_json_atomic(RUNTIME_CONF, runtime)
    except (IOError, OSError) as e:
        print("ERROR: failed to write runtime conf %s: %s" % (RUNTIME_CONF, e),
              file=sys.stderr)
        # Best-effort cleanup of any stale sidecar so we never get into a
        # state where the conf is missing but the sidecar is stale.
        _unlink_if_exists(RUNTIME_POOLMAP)
        sys.exit(1)

    # v2: write the saved-pool-id <-> runtime pool_no sidecar that
    # apply-board-assignments.py reads to translate user-saved pool ids into
    # ascpool|<asc>,<poolno> commands. The runtime_to_saved keys are JSON
    # strings (object keys must be strings) — the applier converts back to
    # int when calling the API.
    poolmap = {
        "runtime_pool_count": len(pools_out),
        "saved_to_runtime": dict(saved_to_runtime),
        "runtime_to_saved": dict((str(idx), pid)
                                 for pid, idx in saved_to_runtime.items()),
        "backup_pool_nos": list(backup_pool_nos),
    }
    try:
        write_json_atomic(RUNTIME_POOLMAP, poolmap)
    except (IOError, OSError) as e:
        # Sidecar write failed AFTER runtime conf was written. The runtime
        # conf is correct but the applier won't be able to translate saved
        # pool ids to runtime pool_nos. Two corrective actions:
        #   1. Remove any pre-existing sidecar so the next applier read sees
        #      ENOENT and fails-closed instead of reading a stale file from
        #      a previous run that no longer matches the current runtime conf.
        #   2. Remove the runtime conf itself for the same reason — the
        #      launcher's miner-start.sh treats builder rc != 0 as a hard
        #      failure (no factory fallback in the v2 launcher), so sgminer
        #      will never start with an inconsistent conf+sidecar pair.
        print("ERROR: failed to write poolmap sidecar %s: %s"
              % (RUNTIME_POOLMAP, e), file=sys.stderr)
        _unlink_if_exists(RUNTIME_POOLMAP)
        _unlink_if_exists(RUNTIME_CONF)
        sys.exit(1)

    # Print a summary to stdout for the launcher to parse / log
    assigned_count = sum(len(b) for b in by_pool.values())
    board_ids = set(str(k) for k in assignments.keys())
    board_ids.update(["0", "1", "2"])
    summary = {
        "config": RUNTIME_CONF,
        "poolmap": RUNTIME_POOLMAP,
        "boards_assigned": assigned_count,
        "boards_idle": max(0, len(board_ids) - assigned_count),
        "unique_pools": len(by_pool),
        "load_balance": use_quota,
        "failover_backups": len(backup_pools) if has_backups else 0,
        "explicit_failovers": dict(explicit_failover_by_board),
        "runtime_pool_count": len(pools_out),
        "pools": [
            {"id": pid, "boards": boards, "quota": len(boards) * (multiplier if has_backups else 1),
             "runtime_pool_no": saved_to_runtime[pid]}
            for pid, boards in by_pool.items()
        ],
    }
    print(json.dumps(summary))
    sys.exit(0)


if __name__ == "__main__":
    main()
