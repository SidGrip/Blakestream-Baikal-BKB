#!/usr/bin/env python3
"""Regression checks for the public BK-B validated clock configuration."""

import os
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(ROOT, 'overlay/opt/scripta/bin/build-miner-runtime.py')
try:
    import importlib.util
    SPEC = importlib.util.spec_from_file_location('build_miner_runtime', MODULE_PATH)
    RUNTIME = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(RUNTIME)
except ImportError:
    import imp
    RUNTIME = imp.load_source('build_miner_runtime', MODULE_PATH)


def check(actual, expected, label):
    if actual != expected:
        raise AssertionError("%s: expected %r, got %r" % (label, expected, actual))


check(RUNTIME.normalize_baikal_options(None), "400:40:55", "missing option")
check(RUNTIME.normalize_baikal_options("40:55"), "400:40:55", "legacy temps")
check(RUNTIME.normalize_baikal_options("400:45:60"), "400:45:60", "validated clock")
check(RUNTIME.normalize_baikal_options("260:40:55"), "400:40:55", "unvalidated clock")
check(RUNTIME.normalize_baikal_options("500:40:55"), "400:40:55", "old maximum")
check(RUNTIME.normalize_baikal_clock_mode("", False, False), "global", "mode default")
check(RUNTIME.normalize_baikal_clock_mode("", False, True), "individual", "legacy mode")
check(
    RUNTIME.normalize_baikal_clock_mode("global", True, True),
    "global",
    "saved global mode",
)
check(
    RUNTIME.normalize_baikal_board_clocks("400:400:400"),
    "400:400:400",
    "validated board clocks",
)

try:
    RUNTIME.normalize_baikal_board_clocks("400:260:400")
except ValueError:
    pass
else:
    raise AssertionError("unvalidated individual clock was accepted")

print("PASS validated clock table and legacy migration")


def fails(fn, label):
    try:
        fn()
    except RUNTIME.ExperimentalClockError:
        return
    raise AssertionError(label)


def option_dict(**values):
    return dict(values)


fails(lambda: RUNTIME.parse_board_clock_index(True), "bool index")
fails(lambda: RUNTIME.parse_board_clock_index(29), "int index")
fails(lambda: RUNTIME.parse_board_clock_index(29.0), "float index")
fails(lambda: RUNTIME.parse_board_clock_index(""), "empty index")
fails(lambda: RUNTIME.parse_board_clock_index(None), "null index")
for bad in ("08:30:30", "29:30:30 ", " 29:30:30", "+29:30:30", "29.0:30:30",
            "29:30", "29:30:30:31", "7:30:30", "33:30:30", "029:30:30",
            "8:09:32", "30:30:30:", ":8:9:32", "29:30:30\n", "29:30:30\r\n"):
    fails(lambda bad=bad: RUNTIME.parse_board_clock_index(bad), "bad clock_index " + bad)
check(RUNTIME.parse_board_clock_index("29:30:30"), "29:30:30", "canonical clock_index")
check(RUNTIME.parse_board_clock_index("8:9:32"), "8:9:32", "edge clock_index")

fails(lambda: RUNTIME.parse_experimental_flag(1), "int flag")
fails(lambda: RUNTIME.parse_experimental_flag(1.0), "float flag")
fails(lambda: RUNTIME.parse_experimental_flag(""), "empty flag")
fails(lambda: RUNTIME.parse_experimental_flag("TRUE"), "upper flag")
fails(lambda: RUNTIME.parse_experimental_flag(None), "null flag")
check(RUNTIME.parse_experimental_flag(True), True, "bool true flag")
check(RUNTIME.parse_experimental_flag(False), False, "bool false flag")
check(RUNTIME.parse_experimental_flag("true"), True, "string true flag")
check(RUNTIME.parse_experimental_flag("false"), False, "string false flag")

plain = option_dict(**{"baikal-options": "400:40:55"})
check(RUNTIME.apply_experimental_clock_index(plain, None), "off", "absent experimental")
check("baikal-experimental-clock-index" not in plain and "baikal-board-clock-index" not in plain,
      True, "absent keys stay omitted")

off = option_dict(**{"baikal-experimental-clock-index": False})
check(RUNTIME.apply_experimental_clock_index(off, "individual"), "off", "explicit false")
check("baikal-experimental-clock-index" not in off, True, "false flag omitted")

off_text = option_dict(**{"baikal-experimental-clock-index": "false"})
check(RUNTIME.apply_experimental_clock_index(off_text, None), "off", "string false")
check("baikal-experimental-clock-index" not in off_text, True, "string false omitted")

fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{"baikal-board-clock-index": "29:30:30"}), "individual"),
    "clock_index without flag")
fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{"baikal-experimental-clock-index": True}), "individual"),
    "flag without clock_index")
fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{"baikal-experimental-clock-index": False, "baikal-board-clock-index": "29:30:30"}),
    "individual"), "false flag keeps clock_index")
fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{"baikal-experimental-clock-index": True, "baikal-board-clock-index": "29:30:30"}),
    None), "missing mode")
fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{"baikal-experimental-clock-index": True, "baikal-board-clock-index": "29:30:30"}),
    "global"), "global mode")
fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{"baikal-experimental-clock-index": True, "baikal-board-clock-index": "7:30:30"}),
    "individual"), "malformed while clocks would be off")

omitted = option_dict(**{
    "baikal-experimental-clock-index": "true",
    "baikal-board-clock-index": "29:30:30",
    "baikal-options": "400:40:55",
})
check(RUNTIME.apply_experimental_clock_index(omitted, "individual"), "omitted",
      "well formed but not verified")
check("baikal-experimental-clock-index" not in omitted and "baikal-board-clock-index" not in omitted,
      True, "unverified enable omitted")
check(omitted["baikal-options"], "400:40:55", "other options kept")

temp_dir = tempfile.TemporaryDirectory(prefix="bkb-exp-clock-index-")
temp = temp_dir.name
identity_path = temp + "/identity.json"
clocks_path = temp + "/scripta.conf"
with open(identity_path, "w") as handle:
    handle.write('{"identity":"CUSTOM V1","chip_md5":"0E8467EDDABF589AD94549A8D572C759"}')
with open(clocks_path, "w") as handle:
    handle.write('{"userAdjustableClocks": true}')
enabled = option_dict(**{
    "baikal-experimental-clock-index": "true",
    "baikal-board-clock-index": "8:30:32",
})
check(RUNTIME.apply_experimental_clock_index(
    enabled, "individual", identity_paths=(identity_path,), scripta_conf=clocks_path),
    "enabled", "verified enable")
check(enabled["baikal-experimental-clock-index"] is True, True, "JSON boolean true")
check(enabled["baikal-board-clock-index"], "8:30:32", "enabled clock_index string")
newer_identity = temp + "/newer-identity.json"
with open(newer_identity, "w") as handle:
    handle.write('{"identity":"STOCK","chip_md5":"d5f844be2952fd713717a71a67e2be61"}')
check(RUNTIME.firmware_supports_clock_index((newer_identity, identity_path)), False,
      "current stock record overrides stale custom fallback")
check(RUNTIME.firmware_supports_clock_index((temp + "/missing", identity_path)), True,
      "missing current record permits SD fallback")

for label, digest, expected in (
        ('CUSTOM V1', RUNTIME.BKB_V1_CHIP_MD5, True),
        ('CUSTOM V1', RUNTIME.BKB_DEV_CHIP_MD5, True),
        ('CUSTOM V2', RUNTIME.BKB_V1_CHIP_MD5, False),
        ('CUSTOM V1', 'f' * 32, False)):
    with open(newer_identity, 'w') as handle:
        json.dump({'identity': label, 'chip_md5': digest}, handle)
    check(RUNTIME.firmware_supports_clock_index((newer_identity, identity_path)), expected,
          'strict identity and authoritative current record')

with open(clocks_path, "w") as handle:
    handle.write('{"userAdjustableClocks": "true"}')
string_clocks = option_dict(**{
    "baikal-experimental-clock-index": True,
    "baikal-board-clock-index": "29:30:30",
})
check(RUNTIME.apply_experimental_clock_index(
    string_clocks, "individual", identity_paths=(identity_path,),
    scripta_conf=clocks_path), "omitted", "string clocks flag is not enabled")

fails(lambda: RUNTIME.load_saved_options([
    {"key": "baikal-board-clock-index", "value": "29:30:30"},
    {"key": "baikal-board-clock-index", "value": "29:30:30"},
]), "duplicate clock_index")
loaded = RUNTIME.load_saved_options([
    {"key": "baikal-clock-mode", "value": "individual"},
    {"key": "baikal-experimental-clock-index", "value": "true"},
    {"key": "no-submit-stale", "value": "false"},
])
check(loaded["baikal-clock-mode"], "individual", "saved mode preserved")
check(loaded["no-submit-stale"], "false", "unrelated string preserved")
check(RUNTIME.explicit_saved_clock_mode([
    {"key": "baikal-clock-mode", "value": "individual"},
]), "individual", "one saved mode is explicit")

duplicate_mode = [
    {"key": "baikal-clock-mode", "value": "individual"},
    {"key": "baikal-clock-mode", "value": "individual"},
    {"key": "baikal-experimental-clock-index", "value": True},
    {"key": "baikal-board-clock-index", "value": "29:30:30"},
]
fails(lambda: RUNTIME.load_saved_options(duplicate_mode),
      "duplicate clock mode cannot authorize")
check(RUNTIME.explicit_saved_clock_mode(duplicate_mode) is None, True,
      "duplicate mode is not explicit")
fails(lambda: RUNTIME.apply_experimental_clock_index(
    option_dict(**{
        "baikal-experimental-clock-index": True,
        "baikal-board-clock-index": "29:30:30",
    }), RUNTIME.explicit_saved_clock_mode(duplicate_mode)),
    "collapsed individual mode is not permission")

plain_duplicate = [
    {"key": "baikal-clock-mode", "value": "global"},
    {"key": "baikal-clock-mode", "value": "individual"},
    {"key": "baikal-options", "value": "400:40:55"},
]
fails(lambda: RUNTIME.load_saved_options(plain_duplicate),
      "every duplicate miner option fails closed")
check(RUNTIME.explicit_saved_clock_mode(plain_duplicate) is None, True,
      "non-experimental duplicate is still not explicit")


def fails_with(fn, text, label):
    try:
        fn()
    except RUNTIME.ExperimentalClockError as exc:
        if text not in str(exc):
            raise AssertionError("%s: expected %r in %r" % (label, text, exc))
        return
    raise AssertionError(label)


for dangerous in ("monitor", "event-runcmd", "events", "include", "includes",
                  "Include", "INCLUDES", "api-listen", "API-ALLOW", "user-added",
                  "monitor\x00safe"):
    fails_with(lambda dangerous=dangerous: RUNTIME.load_saved_options([
        {"key": dangerous, "value": "/bin/false"},
    ]), "unsupported miner option", "dangerous/unknown option %s" % dangerous)
legacy_api = RUNTIME.load_saved_options([
    {"key": "api-allow", "value": "W:0/0"},
    {"key": "api-groups", "value": "W:*"},
    {"key": "api-port", "value": "9999"},
])
check(legacy_api["api-allow"], "W:0/0", "legacy API allowlist accepted as input")
check(legacy_api["api-groups"], "W:*", "legacy API group accepted only as input")
check(legacy_api["api-port"], "9999", "legacy API port accepted as input")
RUNTIME.enforce_fixed_options(legacy_api)
check(legacy_api["api-allow"], "B:127.0.0.1", "API allowlist forced to restricted loopback group")
check(legacy_api["api-groups"],
      "B:*:restart:ascidentify:ascenable:ascdisable:bkbtrace:bkbfan:ascpool:ascfailover",
      "API group forced to required commands")
check(legacy_api["api-port"], "4028", "API port forced to fixed value")
for bad_value in (0, 1, 0.0, 1.0, None, [], {}):
    fails_with(lambda bad_value=bad_value: RUNTIME.load_saved_options([
        {"key": "no-submit-stale", "value": bad_value},
    ]), "true or false", "non-boolean stale option %r" % (bad_value,))
for bad_value in ("400:40:55 --monitor /tmp/pwn", "400:40:55\nevent-runcmd=x",
                  "400:40:55:1", [], {}, True, None):
    fails_with(lambda bad_value=bad_value: RUNTIME.load_saved_options([
        {"key": "baikal-options", "value": bad_value},
    ]), "colon-separated integers", "nested baikal option payload %r" % (bad_value,))


def assert_no_scope(options, label):
    check(RUNTIME.CLOCK_SCOPE_KEY not in options, True, label)


plain_scope = option_dict(**{
    "baikal-options": "400:40:55",
    "blakestream-clock-scope": "global",
})
check(RUNTIME.apply_experimental_clock_index(plain_scope, None), "off", "scope without experimental keys")
assert_no_scope(plain_scope, "absent experiment still strips scope")
check(plain_scope["baikal-options"], "400:40:55", "normal 400 is unchanged beside scope")

off_scope = option_dict(**{
    "baikal-experimental-clock-index": False,
    "blakestream-clock-scope": "individual",
    "baikal-options": "400:45:60",
})
check(RUNTIME.apply_experimental_clock_index(off_scope, "global"), "off", "explicit false keeps scope out")
assert_no_scope(off_scope, "false flag strips scope")
check("baikal-experimental-clock-index" not in off_scope, True, "false flag still omitted")

with open(clocks_path, "w") as handle:
    handle.write('{"userAdjustableClocks": true}')
global_equal = option_dict(**{
    "baikal-experimental-clock-index": "true",
    "baikal-board-clock-index": "29:29:29",
    "blakestream-clock-scope": "global",
    "baikal-options": "400:40:55",
})
check(RUNTIME.apply_experimental_clock_index(
    global_equal, "individual", identity_paths=(identity_path,), scripta_conf=clocks_path),
    "enabled", "global scope with equal indices")
assert_no_scope(global_equal, "enabled global scope stripped")
check(global_equal["baikal-board-clock-index"], "29:29:29", "equal indices kept")
check(global_equal["baikal-experimental-clock-index"] is True, True, "global scope flag is JSON true")
check(global_equal["baikal-options"], "400:40:55", "400 label kept for global scope")

individual_unequal = RUNTIME.load_saved_options([
    {"key": "baikal-clock-mode", "value": "individual"},
    {"key": "baikal-experimental-clock-index", "value": True},
    {"key": "baikal-board-clock-index", "value": "8:9:32"},
    {"key": "blakestream-clock-scope", "value": "individual"},
    {"key": "no-submit-stale", "value": "false"},
])
check(individual_unequal["blakestream-clock-scope"], "individual", "loaded individual scope")
check(RUNTIME.apply_experimental_clock_index(
    individual_unequal, "individual", identity_paths=(identity_path,),
    scripta_conf=clocks_path), "enabled", "individual scope allows unequal indices")
assert_no_scope(individual_unequal, "enabled individual scope stripped")
check(individual_unequal["baikal-board-clock-index"], "8:9:32", "unequal indices kept")
check(individual_unequal["no-submit-stale"], "false", "scope does not coerce unrelated strings")

individual_equal = option_dict(**{
    "baikal-experimental-clock-index": True,
    "baikal-board-clock-index": "8:8:8",
    "blakestream-clock-scope": "individual",
})
check(RUNTIME.apply_experimental_clock_index(
    individual_equal, "individual", identity_paths=(identity_path,),
    scripta_conf=clocks_path), "enabled", "individual scope allows equal indices")
assert_no_scope(individual_equal, "equal individual scope stripped")

legacy_unequal = option_dict(**{
    "baikal-experimental-clock-index": True,
    "baikal-board-clock-index": "29:30:30",
})
check(RUNTIME.apply_experimental_clock_index(
    legacy_unequal, "individual", identity_paths=(identity_path,),
    scripta_conf=clocks_path), "enabled", "legacy no-scope unequal indices unchanged")
check(legacy_unequal["baikal-board-clock-index"], "29:30:30", "legacy clock_index unchanged")

fails_with(lambda: RUNTIME.apply_experimental_clock_index(option_dict(**{
    "baikal-experimental-clock-index": True,
    "baikal-board-clock-index": "8:30:32",
    "blakestream-clock-scope": "global",
}), "individual"), "three equal clock indices", "global scope rejects unequal indices")
fails_with(lambda: RUNTIME.apply_experimental_clock_index(option_dict(**{
    "baikal-experimental-clock-index": True,
    "baikal-board-clock-index": "30:30:30",
    "blakestream-clock-scope": "global",
}), "global"), "explicit baikal-clock-mode individual",
    "scope does not authorize global actual mode")
fails_with(lambda: RUNTIME.load_saved_options([
    {"key": "blakestream-clock-scope", "value": "global"},
    {"key": "blakestream-clock-scope", "value": "individual"},
]), "duplicate miner option", "duplicate scope")
fails_with(lambda: RUNTIME.load_saved_options([
    {"key": "blakestream-clock-scope"},
]), "key and value", "scope without a value")
for bad in (True, False, 0, 1, 30, 400, 480, None, "", "Global", "GLOBAL",
            "global ", " global", "individual\n", "both", "400", "480", "30"):
    fails_with(lambda bad=bad: RUNTIME.parse_clock_scope(bad),
               "global or individual", "bad scope %r" % (bad,))
    fails_with(lambda bad=bad: RUNTIME.load_saved_options([
        {"key": "blakestream-clock-scope", "value": bad},
        {"key": "baikal-options", "value": "400:40:55"},
    ]), "global or individual", "saved bad scope %r" % (bad,))

# The privileged writer must not use its old predictable ``path + '.tmp'``
# name and must reject a writable output parent. A pre-created symlink at the
# legacy name must remain untouched and its victim must not be modified.
atomic_dir = os.path.join(temp, "atomic")
os.mkdir(atomic_dir, 0o700)
atomic_path = os.path.join(atomic_dir, "runtime.json")
victim_path = os.path.join(temp, "victim")
with open(victim_path, "w") as handle:
    handle.write("unchanged")
os.symlink(victim_path, atomic_path + ".tmp")
RUNTIME.write_json_atomic(atomic_path, {"api-port": "4028"})
with open(victim_path, "r") as handle:
    check(handle.read(), "unchanged", "predictable temp symlink victim unchanged")
check(os.path.islink(atomic_path + ".tmp"), True, "legacy temp symlink not followed")
check(os.path.isfile(atomic_path) and not os.path.islink(atomic_path), True,
      "runtime output is a regular file")
check(os.stat(atomic_path).st_mode & 0o777, 0o640, "runtime output mode")
with open(atomic_path, "r") as handle:
    check(json.load(handle), {"api-port": "4028"}, "atomic runtime output content")

writable_dir = os.path.join(temp, "writable")
os.mkdir(writable_dir, 0o777)
os.chmod(writable_dir, 0o777)
try:
    RUNTIME.write_json_atomic(os.path.join(writable_dir, "runtime.json"), {"safe": True})
except OSError:
    pass
else:
    raise AssertionError("group/world-writable runtime parent was accepted")

temp_dir.cleanup()
print("PASS experimental Index runtime and secure atomic-writer contracts")
