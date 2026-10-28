#!/usr/bin/env python3
"""Exercise the bundled source and patch path without network or compilation."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
PIN = '3cde8deca53f83ac65bc685b988166bd17930753'


class BundledSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bkb offline source ')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.env = os.environ.copy()
        for key in ('BKB_SGMINER_URL', 'BKB_SGMINER_SRC', 'GIT_DIR',
                    'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY',
                    'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            self.env.pop(key, None)
        self.env.update(GIT_ALLOW_PROTOCOL='file', GIT_TERMINAL_PROMPT='0',
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

    def run_command(self, args, success=True):
        result = subprocess.run(
            [str(arg) for arg in args], cwd=str(self.work), env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def fetch(self, source, root=ROOT, success=True, extra=()):
        return self.run_command(
            ['bash', root / 'sgminer-build/fetch-baseline.sh', '--src', source]
            + list(extra), success=success)

    def fixture(self, bundle=True):
        root = self.work / 'relocated repo'
        folder = root / 'sgminer-build'
        (folder / 'vendor').mkdir(parents=True)
        for name in ('fetch-baseline.sh', 'UPSTREAM-PIN'):
            shutil.copy2(ROOT / 'sgminer-build' / name, folder / name)
        if bundle:
            shutil.copy2(ROOT / 'sgminer-build/vendor/sgminer-baikal.bundle',
                         folder / 'vendor/sgminer-baikal.bundle')
        return root

    def test_offline_restore_and_all_patches(self):
        source = self.work / 'source with spaces'
        restored = self.fetch(source)
        self.assertIn('offline', restored)
        self.assertEqual(self.run_command(
            ['git', '-C', source, 'rev-parse', 'HEAD']).strip(), PIN)
        self.run_command(['git', '-C', source, 'fsck', '--full'])
        self.assertFalse((source / '.git/objects/info/alternates').exists())
        self.assertEqual(self.run_command(
            ['git', '-C', source, 'status', '--porcelain']), '')
        self.assertTrue((source / 'LICENSE').is_file())
        self.assertTrue((source / 'driver-baikalu.c').is_file())
        self.assertTrue((source / 'submodules/jansson').is_dir())
        self.assertTrue((source / 'submodules/libusb-bk').is_dir())
        self.assertNotIn('160000 ', self.run_command(
            ['git', '-C', source, 'ls-tree', '-r', 'HEAD']))

        apply = ['bash', ROOT / 'sgminer-build/apply-patches.sh', '--src', source]
        self.assertIn('applied 36 patches; checksums match', self.run_command(apply))
        api = (source / 'api.c').read_text()
        self.assertNotIn('{"save",   dosave', api)
        policy = json.loads((ROOT / 'overlay/opt/scripta/policy/miner-options.json').read_text())
        self.assertEqual(policy['fixed_values']['api-allow'], 'B:127.0.0.1')
        group = policy['fixed_values']['api-groups'].split(':')
        self.assertEqual(group[:2], ['B', '*'])
        granted_writes = set(group[2:])
        required = {'restart', 'ascidentify', 'ascenable', 'ascdisable',
                    'bkbtrace', 'bkbfan', 'ascpool', 'ascfailover'}
        forbidden = {'save', 'addpool', 'removepool', 'quit', 'setconfig',
                     'debug', 'lockstats', 'ascset'}
        self.assertEqual(granted_writes, required)
        self.assertTrue(granted_writes.isdisjoint(forbidden))
        rows = dict(re.findall(
            r'\{"([a-z0-9-]+)",\s*[^,\n]+,\s*(true|false),\s*(?:true|false)\}', api))
        for command in required:
            self.assertEqual(rows.get(command), 'true', command)
        self.assertIn('if (cmds[i].iswritemode == false)', api)

        trace_start = api.index('static void bkbtrace(')
        trace_end = api.index('\nstatic void bkbfan(', trace_start)
        trace = api[trace_start:trace_end]
        self.assertIn('param == NULL', trace)
        for operation in ('on', 'off', 'clear', 'export'):
            self.assertIn('strcmp(param, "%s")' % operation, trace)
        self.assertIn('snprintf(request, sizeof(request), "0,diag-trace,%s", param)', trace)
        self.assertIn('ascset(io_data, c, request, isjson, group)', trace)
        trace_grammar = re.compile(r'^(?:on|off|clear|export)\Z')
        for parameter in ('on', 'off', 'clear', 'export'):
            self.assertRegex(parameter, trace_grammar)
        for parameter in ('', 'enable', 'on ', ' on', 'on,extra', 'on\n',
                          '0,diag-trace,on'):
            self.assertNotRegex(parameter, trace_grammar)

        fan_start = trace_end + 1
        fan_end = api.index('\nstatic void ascpool(', fan_start)
        fan = api[fan_start:fan_end]
        for guard in ("strchr(comma + 1, ',') != NULL", 'comma != param + 1',
                      "param[0] < '0'", "param[0] > '2'", "*fan_text == '\\0'",
                      'isdigit((unsigned char)*scan)', 'strlen(fan_text) == 2',
                      'fan >= 20 && fan <= 99', 'strcmp(fan_text, "100") == 0'):
            self.assertIn(guard, fan)
        self.assertIn('snprintf(request, sizeof(request), "%d,fan,%ld", id, fan)', fan)
        self.assertIn('ascset(io_data, c, request, isjson, group)', fan)
        self.assertNotIn('strtok', fan)
        fan_grammar = re.compile(r'^[0-2],(?:[2-9][0-9]|100)\Z')
        for parameter in ('0,20', '1,99', '2,100'):
            self.assertRegex(parameter, fan_grammar)
        for parameter in ('0,0', '0,19', '0,-20', '0,+20', '0, 20',
                          '0,20 ', '0,20\n', '0,020', '0,20,extra', '3,20', '00,20'):
            self.assertNotRegex(parameter, fan_grammar)
        self.assertIn('patches already applied', self.run_command(apply))
        # Re-preparing an existing checkout must preserve its patched files.
        self.assertIn('already at', self.fetch(source))
        self.assertIn('patches already applied', self.run_command(apply))

    def test_relocated_repo_and_relative_destination(self):
        root = self.fixture()
        self.fetch('relative source', root=root)
        self.assertTrue((self.work / 'relative source/driver-baikalu.c').is_file())

    def test_missing_bundle_fails_before_creating_destination(self):
        root = self.fixture(bundle=False)
        source = self.work / 'missing source'
        output = self.fetch(source, root=root, success=False)
        self.assertIn('missing bundled sgminer source', output)
        self.assertFalse(source.exists())

    def test_corrupt_bundle_fails_before_creating_destination(self):
        root = self.fixture()
        with (root / 'sgminer-build/vendor/sgminer-baikal.bundle').open('ab') as handle:
            handle.write(b'corrupt')
        source = self.work / 'corrupt source'
        output = self.fetch(source, root=root, success=False)
        self.assertIn('failed SHA-256 verification', output)
        self.assertFalse(source.exists())

    def test_existing_files_and_wrong_commit_are_not_overwritten(self):
        source = self.work / 'source'
        source.mkdir()
        marker = source / 'keep.txt'
        marker.write_text('keep this file')
        self.assertIn('refusing to replace', self.fetch(source, success=False))
        self.assertEqual(marker.read_text(), 'keep this file')

        checkout = self.work / 'wrong commit'
        self.fetch(checkout)
        self.run_command(['git', '-C', checkout, 'checkout', '--detach', 'HEAD^'])
        before = self.run_command(['git', '-C', checkout, 'rev-parse', 'HEAD'])
        self.assertIn('refusing to modify', self.fetch(checkout, success=False))
        self.assertEqual(before, self.run_command(
            ['git', '-C', checkout, 'rev-parse', 'HEAD']))

    def test_explicit_source_override_still_requires_pin(self):
        source = self.work / 'explicit source'
        bundle = ROOT / 'sgminer-build/vendor/sgminer-baikal.bundle'
        self.fetch(source, extra=('--url', bundle))
        self.assertEqual(self.run_command(
            ['git', '-C', source, 'rev-parse', 'HEAD']).strip(), PIN)


if __name__ == '__main__':
    unittest.main(verbosity=2)
