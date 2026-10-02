#!/usr/bin/env python3
"""Check container launch boundaries using a fake Docker executable only."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'scripts/test-firmware-container.sh'
DOCKER = '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ['QA_COMMAND_LOG'], 'a') as handle:
    handle.write(json.dumps(args) + '\\n')
if args[:2] == ['image', 'inspect']:
    if os.environ.get('QA_IMAGE_MISSING') == '1':
        sys.exit(1)
    print(os.environ.get('QA_IMAGE_LABEL', '1') if '--format' in args else '{}')
if args[:1] == ['run']:
    sys.exit(int(os.environ.get('QA_RUN_EXIT', '0')))
'''


class ContainerRunnerTests(unittest.TestCase):
    def invoke(self, args=(), env=None):
        with tempfile.TemporaryDirectory(prefix='bkb container runner ') as temp:
            folder = Path(temp)
            fake = folder / 'docker'
            fixture = folder / 'docker.py'
            fixture.write_text(DOCKER)
            fake.write_text('#!/bin/sh\nexec {0} {1} "$@"\n'.format(
                shlex.quote(sys.executable), shlex.quote(str(fixture))))
            fake.chmod(0o755)
            log = folder / 'commands.jsonl'
            settings = dict(os.environ, PATH=temp + ':' + os.environ['PATH'],
                            QA_COMMAND_LOG=str(log), BKB_FIRMWARE_QA_IMAGE='fixture:local')
            settings.update(env or {})
            process = subprocess.run(['bash', str(RUNNER)] + list(args), cwd=temp,
                env=settings, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=10)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return process, commands

    def test_run_is_offline_and_confined(self):
        process, commands = self.invoke()
        self.assertEqual(process.returncode, 0, process.stdout)
        run = next(row for row in commands if row[0] == 'run')
        for option, value in (('--network', 'none'), ('--pull', 'never'),
                              ('--cap-drop', 'ALL'), ('--memory', '512m'),
                              ('--cpus', '2'), ('--pids-limit', '192')):
            self.assertEqual(run[run.index(option) + 1], value)
        self.assertIn('--init', run)
        self.assertIn('--rm', run)
        for forbidden in ('--privileged', '--device', '--pid', '--publish', '-p'):
            self.assertNotIn(forbidden, run)
        mounts = [run[i + 1] for i, arg in enumerate(run) if arg == '--mount']
        self.assertEqual(mounts, [
            'type=bind,src={0}/scripts,dst=/repo/scripts,readonly'.format(ROOT),
            'type=bind,src={0}/overlay,dst=/repo/overlay,readonly'.format(ROOT)])
        self.assertFalse(any(row[0] in ('build', 'pull') for row in commands))
        self.assertEqual(commands[-1][:2], ['rm', '-f'])
        self.assertEqual(commands[-1][2], run[run.index('--name') + 1])

    def test_failed_suite_keeps_exit_code_and_cleans_up(self):
        process, commands = self.invoke(env={'QA_RUN_EXIT': '7'})
        self.assertEqual(process.returncode, 7)
        self.assertEqual(commands[-1][:2], ['rm', '-f'])

    def test_missing_or_wrong_image_fails_without_running(self):
        for settings in ({'QA_IMAGE_MISSING': '1'}, {'QA_IMAGE_LABEL': 'wrong'}):
            with self.subTest(settings=settings):
                process, commands = self.invoke(env=settings)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(any(row[0] in ('run', 'build', 'pull') for row in commands))

    def test_build_is_explicit(self):
        process, commands = self.invoke(['--build'])
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(commands, [['build', '--tag', 'fixture:local', str(ROOT / 'scripts/firmware-qa')]])

    def test_help_and_bad_arguments_do_not_invoke_docker(self):
        for args, expected in ((['--help'], 0), (['--device'], 2), (['--build', 'extra'], 2)):
            process, commands = self.invoke(args)
            self.assertEqual(process.returncode, expected, process.stdout)
            self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
