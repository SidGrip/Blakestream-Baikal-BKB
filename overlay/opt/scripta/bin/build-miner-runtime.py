#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build-miner-runtime.py - Blakestream-GaintB

Compatible with Python 2.7+ and Python 3 (the Baikal device runs Python 2.7).

Reads:
  /opt/scripta/etc/saved-pools.json       - user pool catalog (categories + pools)
  /opt/scripta/etc/board-assignments.json - board id -> pool id (or null = idle)
  /opt/scripta/etc/miner.options.json     - shared sgminer options

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
import sys
import collections

ETC_DIR = "/opt/scripta/etc"
SAVED_POOLS = os.path.join(ETC_DIR, "saved-pools.json")
ASSIGNMENTS = os.path.join(ETC_DIR, "board-assignments.json")
OPTIONS = os.path.join(ETC_DIR, "miner.options.json")
TEMP_STATE = os.path.join(ETC_DIR, "temp-state.json")
FAILOVER_CONFIG = os.path.join(ETC_DIR, "failover-config.json")
RUNTIME_CONF = "/tmp/scripta-runtime-miner.conf"
RUNTIME_POOLMAP = "/tmp/scripta-runtime-poolmap.json"


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


def index_pools_by_id(saved_pools_doc):
    """Return dict: pool_id -> pool_object (with category context if needed)."""
    out = {}
    for cat in saved_pools_doc.get("categories", []):
        for pool in cat.get("pools", []):
            pid = pool.get("id")
            if pid:
                out[pid] = pool
    return out


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
            if failover_id == primary_id:
                sys.stderr.write("WARN: board %s failover == primary, ignoring\n" % board_id_str)
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

    # Convert miner.options.json (list of {key, value}) into a flat dict
    options = {}
    if isinstance(options_list, list):
        for item in options_list:
            if isinstance(item, dict) and "key" in item and "value" in item:
                options[item["key"]] = item["value"]
    elif isinstance(options_list, dict):
        options = dict(options_list)

    # Determine if we need failover backups: all same-algo saved pools that
    # AREN'T already in by_pool are added with quota:1 so sgminer keeps a
    # connection warm. Primary pools get quota = boards * primary_quota_multiplier
    # (default 10) so ~99% of work goes to user choice while backups stay alive
    # ready to absorb quota redistribution if a primary dies.
    failover_enabled = failover.get("enabled", True)
    multiplier = int(failover.get("primary_quota_multiplier", 10))
    if multiplier < 1:
        multiplier = 1

    primary_pool_ids = set(by_pool.keys())

    # Backup pool collection has TWO sources, in this priority order:
    #
    #   1. Explicit per-board failover (boards where the user picked a specific
    #      failover pool in the dashboard's FAILOVER lane). These are added as
    #      quota:1 backups regardless of the global failover toggle. Their
    #      presence ALSO suppresses the auto-search for those boards' algos
    #      (so sgminer can only fail over to the user-chosen pool, never to
    #      some random other same-algo pool).
    #
    #   2. Global automatic same-algo failover (only when failover-config.json
    #      enabled=true). Adds every saved pool of an algo used by a primary,
    #      EXCEPT for algos that are already covered by an explicit per-board
    #      failover. This preserves today's auto-failover behaviour for boards
    #      without an explicit user choice, while honouring the "stop hashing
    #      when both die" guarantee for boards that have one.
    backup_pools = []
    backup_pool_ids_seen = set()

    # 1. Explicit per-board failovers
    explicit_failover_pool_ids = set()
    for bid, fid in explicit_failover_by_board.items():
        explicit_failover_pool_ids.add(fid)
    # Algos that have an explicit failover for at least one board → skip auto
    explicit_failover_algos = set()
    for fid in explicit_failover_pool_ids:
        algo = pool_index[fid].get("algo")
        if algo:
            explicit_failover_algos.add(algo)
    for fid in explicit_failover_pool_ids:
        if fid in primary_pool_ids:
            # Already in pools_out as a primary; sgminer will share it.
            continue
        if fid in backup_pool_ids_seen:
            continue
        backup_pool_ids_seen.add(fid)
        backup_pools.append(pool_index[fid])

    # 2. Global same-algo auto backups (only for algos NOT covered by explicit)
    if failover_enabled:
        primary_algos = set()
        for pid in primary_pool_ids:
            primary_algos.add(pool_index[pid].get("algo", "blake256r8"))
        auto_algos = primary_algos - explicit_failover_algos
        for cat in saved.get("categories", []):
            for pool in cat.get("pools", []):
                pid = pool.get("id")
                if (pid and pid not in primary_pool_ids
                        and pid not in backup_pool_ids_seen
                        and pool.get("algo") in auto_algos):
                    backup_pool_ids_seen.add(pid)
                    backup_pools.append(pool)

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
    # has_backups is true whenever ANY backup pool is in the runtime conf —
    # explicit per-board failovers OR auto-search same-algo pools. The quota
    # multiplier kicks in either way so primaries get most of the work.
    has_backups = len(backup_pools) > 0
    use_quota = len(by_pool) > 1 or has_backups
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
    runtime.setdefault("api-allow", "W:127.0.0.1,W:192.168.0.0/16")
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
    summary = {
        "config": RUNTIME_CONF,
        "poolmap": RUNTIME_POOLMAP,
        "boards_assigned": sum(len(b) for b in by_pool.values()),
        "boards_idle": sum(1 for v in assignments.values() if not v),
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
