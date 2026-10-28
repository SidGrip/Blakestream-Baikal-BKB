#!/usr/bin/env python3
"""Portable entry points must resolve from any cwd and fail closed.

This file is safe to run from scripts/qa contract-python: it never invokes
the contract-python phase, so it does not call itself.
"""
from __future__ import print_function

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA = os.path.join(ROOT, 'scripts', 'qa')
BUILD = os.path.join(ROOT, 'firmware-custom', 'build.sh')
APPLY = os.path.join(ROOT, 'sgminer-build', 'apply-patches.sh')
FETCH = os.path.join(ROOT, 'sgminer-build', 'fetch-baseline.sh')
PIN = '3cde8deca53f83ac65bc685b988166bd17930753'
FAILURES = []


def check(cond, message):
    if not cond:
        FAILURES.append(message)
        sys.stderr.write('FAIL: %s\n' % message)


def run(args, cwd, env=None):
    merged = os.environ.copy()
    merged.pop('BKB_STOCK_DUMP', None)
    merged.pop('BKB_SGMINER_SRC', None)
    merged.pop('BKB_V1_IMAGE', None)
    merged.pop('BKB_FIRMWARE_OUT', None)
    if env:
        merged.update(env)
    return subprocess.run(
        args, cwd=cwd, env=merged, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, universal_newlines=True)


def write_exec(path, text):
    with open(path, 'w') as handle:
        handle.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


def main():
    unrelated = os.path.abspath(os.sep)
    spaced_cwd = tempfile.mkdtemp(prefix='bkb qa cwd ')
    try:
        described = run([QA, '--describe'], unrelated)
        check(described.returncode == 0, 'qa --describe from / failed: %s' % described.stdout)
        check('not a pass' in described.stdout, 'describe does not say optional phases are not a pass')
        check(ROOT in described.stdout, 'describe did not print the repository root')

        helped = run([QA, '--help'], spaced_cwd)
        check(helped.returncode == 0, 'qa --help from a spaced cwd failed')
        check('emulation-v1' in helped.stdout, 'help missing emulation-v1')

        minimal_path = os.path.join(spaced_cwd, 'minimal bin')
        os.mkdir(minimal_path)
        os.symlink(shutil.which('bash'), os.path.join(minimal_path, 'bash'))
        no_dirname = run([QA, '--describe'], unrelated, {'PATH': minimal_path})
        check(no_dirname.returncode != 0, 'qa continued without dirname')
        check('missing required dirname' in no_dirname.stdout,
              'missing dirname error was unclear: %s' % no_dirname.stdout)
        check('Repository:' not in no_dirname.stdout,
              'missing dirname produced a misleading repository root')

        listed = run([APPLY, '--list'], unrelated)
        check(listed.returncode == 0, 'apply --list failed: %s' % listed.stdout)
        names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        check(len(names) == 36, 'expected 36 patches, got %d' % len(names))
        check(not any(name.startswith('0003-') for name in names), 'rejected 0003 was listed')
        check(names[-1].startswith('0037-'), 'last patch is not 0037: %s' % names[-1:])

        pinned = run([FETCH, '--print-pin'], spaced_cwd)
        check(pinned.returncode == 0 and pinned.stdout.strip() == PIN,
              'pin print mismatch: %s' % pinned.stdout)

        missing_src = run([APPLY, '--src', os.path.join(spaced_cwd, 'no-such-src')], unrelated)
        check(missing_src.returncode != 0, 'apply on a missing tree exited 0')
        check('not found' in missing_src.stdout.lower() or 'checkout' in missing_src.stdout.lower(),
              'missing source did not explain itself: %s' % missing_src.stdout)

        repo = tempfile.mkdtemp(prefix='bkb src ', dir=spaced_cwd)
        subprocess.check_call(['git', 'init'], cwd=repo, stdout=subprocess.PIPE)
        subprocess.check_call(['git', 'config', 'user.email', 'qa@example.com'], cwd=repo)
        subprocess.check_call(['git', 'config', 'user.name', 'QA'], cwd=repo)
        subprocess.check_call(['git', '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'not-upstream'],
                              cwd=repo, stdout=subprocess.PIPE)
        wrong = run([APPLY, '--src', repo], unrelated)
        check(wrong.returncode != 0, 'apply accepted a checkout that is not the pin')
        check(PIN in wrong.stdout, 'wrong-commit error did not name the pin: %s' % wrong.stdout)
        after = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, universal_newlines=True)
        check(after.strip() != PIN, 'apply moved HEAD onto the upstream pin')

        native = run([QA, 'native'], unrelated, {'BKB_SGMINER_SRC': os.path.join(spaced_cwd, 'missing')})
        check(native.returncode != 0, 'native phase passed without source')
        check('RESULT: PASS' not in native.stdout, 'native failure summary said PASS')
        check('native FAIL' in native.stdout, 'native failure was not reported: %s' % native.stdout)

        driver = run([QA, 'driver'], unrelated, {'BKB_SGMINER_SRC': os.path.join(spaced_cwd, 'missing')})
        check(driver.returncode != 0, 'driver phase passed without source')
        check('RESULT: PASS' not in driver.stdout, 'driver failure summary said PASS')

        image = run([QA, 'emulation-v1'], unrelated, {'BKB_V1_IMAGE': os.path.join(spaced_cwd, 'no-v1.bin')})
        check(image.returncode != 0, 'emulation passed with a missing image')
        check('RESULT: PASS' not in image.stdout, 'missing image was reported as PASS')
        check('skip' not in image.stdout.lower() or 'does not skip' in image.stdout.lower(),
              'missing image looked like a skip: %s' % image.stdout)
        active = run([QA, 'emulation-active-slots'], unrelated, {'BKB_V1_IMAGE': os.path.join(spaced_cwd, 'no-v1.bin')})
        check(active.returncode != 0, 'active-slot emulation passed with a missing image')
        check('RESULT: PASS' not in active.stdout, 'missing image reported as PASS')

        php = run([QA, 'contract-php'], unrelated, {'BKB_PHP': os.path.join(spaced_cwd, 'no-php')})
        check(php.returncode != 0, 'contract-php passed without php')
        check('RESULT: PASS' not in php.stdout, 'missing php was reported as PASS')

        failing_php = os.path.join(spaced_cwd, 'failing php')
        write_exec(failing_php, '#!/bin/sh\ncase "$1" in */test-accepted-work.php) exit 17;; esac\nexit 0\n')
        early = run([QA, 'contract-php'], unrelated, {'BKB_PHP': failing_php})
        check(early.returncode != 0, 'early test failure was hidden by later passing tests')
        check('contract-php FAIL (exit 17)' in early.stdout, 'test exit code was not preserved')

        forbid_log = os.path.join(spaced_cwd, 'forbid.log')
        bindir = os.path.join(spaced_cwd, 'bin')
        os.mkdir(bindir)
        for name in ('ssh', 'sudo', 'apt-get', 'apt', 'docker', 'dfu-util',
                     'losetup', 'arm-none-eabi-gcc', 'git'):
            write_exec(os.path.join(bindir, name),
                       '#!/bin/sh\necho FORBIDDEN %s >> "%s"\nexit 99\n' % (name, forbid_log))
        forbidden_env = {'PATH': bindir + os.pathsep + os.environ.get('PATH', '')}
        quiet = run([QA, '--describe'], unrelated, forbidden_env)
        check(quiet.returncode == 0, 'describe failed with shadowed tools')
        check(not os.path.exists(forbid_log), 'describe invoked a forbidden tool')

        node = run([QA, 'contract-node'], unrelated, forbidden_env)
        check(node.returncode == 0, 'contract-node failed: %s' % node.stdout[-500:])
        check(not os.path.exists(forbid_log), 'contract-node invoked a forbidden tool')

        relocated = os.path.join(spaced_cwd, 'relocated checkout')
        for directory in ('scripts', 'overlay', 'firmware-custom'):
            shutil.copytree(os.path.join(ROOT, directory), os.path.join(relocated, directory),
                            symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(os.path.join(ROOT, 'TECHNICAL-GUIDE.md'), relocated)
        relocated_qa = os.path.join(relocated, 'scripts', 'qa')
        described = run([relocated_qa, '--describe'], unrelated, forbidden_env)
        check(described.returncode == 0 and relocated in described.stdout,
              'relocated qa did not resolve its own root: %s' % described.stdout)
        node = run([relocated_qa, 'contract-node'], unrelated, forbidden_env)
        check(node.returncode == 0, 'relocated contract-node failed: %s' % node.stdout[-500:])
        check(not os.path.exists(forbid_log), 'relocated QA invoked a forbidden tool')

        python_log = os.path.join(spaced_cwd, 'python override.log')
        python_override = os.path.join(spaced_cwd, 'custom python')
        write_exec(python_override, '#!/bin/sh\nprintf used >> %s\nexec %s "$@"\n' %
                   (shlex.quote(python_log), shlex.quote(sys.executable)))
        write_exec(os.path.join(bindir, 'python3'), '#!/bin/sh\nexit 97\n')
        python_env = dict(forbidden_env, BKB_PYTHON=python_override, TMPDIR=spaced_cwd)
        for name in ('test-firmware-container-runner.py', 'test-image-packaging.py',
                     'test-measured-image-prune.py', 'test-clock-config.py'):
            result = run([python_override, os.path.join(relocated, 'scripts', name)],
                         unrelated, python_env)
            check(result.returncode == 0,
                  '%s ignored Python override or failed: %s' % (name, result.stdout[-1000:]))
        check(os.path.isfile(python_log), 'selected Python interpreter was not invoked')
        check(not any(name.startswith('bkb-exp-clock-index-') for name in os.listdir(spaced_cwd)),
              'clock config test leaked its temporary directory')
        check(not os.path.exists(forbid_log), 'Python fixtures invoked a forbidden tool')

        php_bin = shutil.which(os.environ.get('BKB_PHP') or 'php')
        check(php_bin is not None, 'PHP is required for the interpreter override check')
        if php_bin:
            php_log = os.path.join(spaced_cwd, 'php override.log')
            php_override = os.path.join(spaced_cwd, 'custom php')
            write_exec(php_override, '#!/bin/sh\nprintf used >> %s\nexec %s "$@"\n' %
                       (shlex.quote(php_log), shlex.quote(os.path.abspath(php_bin))))
            write_exec(os.path.join(bindir, 'php'), '#!/bin/sh\nexit 98\n')
            wire = run([sys.executable, os.path.join(relocated, 'scripts', 'test-bkb-wire-contract.py'), relocated,
                        'Contract.test_ui_reads_real_grid_and_shared_trace_snapshot'], unrelated,
                       dict(forbidden_env, BKB_PHP=php_override))
            check(wire.returncode == 0, 'wire test ignored PHP override: %s' % wire.stdout)
            check(os.path.isfile(php_log), 'wire test did not call the selected PHP interpreter')

        firmware = run(
            [QA, 'firmware-build'], unrelated,
            dict(forbidden_env, BKB_STOCK_DUMP=os.path.join(spaced_cwd, 'no-stock.bin'),
                 BKB_FIRMWARE_OUT=os.path.join(spaced_cwd, 'should-not-build')))
        check(firmware.returncode != 0, 'firmware-build passed without a stock dump')
        check('RESULT: PASS' not in firmware.stdout, 'missing dump was reported as PASS')
        forbid_text = open(forbid_log).read() if os.path.exists(forbid_log) else ''
        check('arm-none-eabi-gcc' not in forbid_text,
              'firmware-build called the compiler without a verified dump: %s' % forbid_text)
        check(not os.path.exists(os.path.join(spaced_cwd, 'should-not-build')),
              'firmware-build created an output directory without a dump')

        fake_tree = tempfile.mkdtemp(prefix='bkb fw ', dir=spaced_cwd)
        fw = os.path.join(fake_tree, 'firmware-custom')
        os.mkdir(fw)
        shutil.copy(BUILD, os.path.join(fw, 'build.sh'))
        shutil.copy(os.path.join(ROOT, 'firmware-custom', 'STOCK-BRIDGE.txt'),
                    os.path.join(fw, 'STOCK-BRIDGE.txt'))
        bad_dump = os.path.join(fake_tree, 'bad dump.bin')
        with open(bad_dump, 'wb') as handle:
            handle.write(b'\xff' * 64)
        bad = run([os.path.join(fw, 'build.sh'), '--dump', bad_dump,
                   '--out-dir', os.path.join(fake_tree, 'out dir'), 'v1'],
                  unrelated, forbidden_env)
        check(bad.returncode != 0, 'bad stock dump was accepted')
        check('failed verification' in bad.stdout, 'bad dump error was unclear: %s' % bad.stdout)
        forbid_text = open(forbid_log).read() if os.path.exists(forbid_log) else ''
        check('arm-none-eabi-gcc' not in forbid_text, 'bad dump still invoked gcc')
    finally:
        shutil.rmtree(spaced_cwd)

    if FAILURES:
        sys.stderr.write('%d entry-point failure(s)\n' % len(FAILURES))
        return 1
    print('PASS portable QA entry points')
    return 0


if __name__ == '__main__':
    sys.exit(main())
