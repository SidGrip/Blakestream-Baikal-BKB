from __future__ import print_function

import fcntl
import json
import os
import sys
import time

from measured_rate_store import Store, StoreError
from measured_rates import samples_from_api, reduce_samples, public_summary


def read_json(path, fallback):
    try:
        with open(path) as handle:
            value = json.load(handle)
            return value if isinstance(value, type(fallback)) else fallback
    except (IOError, ValueError):
        return fallback


def write_json(path, data):
    tmp = path + '.%d.tmp' % os.getpid()
    try:
        with open(tmp, 'w') as handle:
            handle.write(json.dumps(data, separators=(',', ':'), allow_nan=False))
        os.chmod(tmp, 0o664)
        os.rename(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Service(object):
    def __init__(self, root=''):
        self.ram = root + '/run/blakestream'
        self.sd = root + '/opt/scripta/var'
        self.etc = root + '/opt/scripta/etc'
        self.boot_path = root + '/proc/sys/kernel/random/boot_id'
        self.summary = self.ram + '/measured-rates-status.json'
        self.rates = self.ram + '/accepted-work-rates.json'
        self.store = Store(self.ram, self.sd)

    def locked(self, wait=False):
        if not os.path.isdir(self.ram):
            try:
                os.makedirs(self.ram, 0o775)
            except OSError:
                if not os.path.isdir(self.ram):
                    raise
        lock = open(self.ram + '/measured-rates-command.lock', 'a')
        # A clean-stop flush can briefly wait for the in-flight minute writer.
        for attempt in range(51 if wait else 1):
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock
            except IOError:
                if wait and attempt < 50:
                    time.sleep(0.2)
        lock.close()
        raise StoreError('Measured rates are busy; retry shortly')

    def publish(self, status, now):
        summary = public_summary(status, now)
        write_json(self.summary, summary)
        write_json(self.rates, {'version': 2, 'updated': now,
                              'generation': summary.get('generation'), 'boards': summary['boards']})
        return summary

    def record(self, before, after, devs, pools, cfg, options, now, force=False):
        with self.locked():
            try:
                with open(self.boot_path) as handle:
                    boot_id = handle.read(128).strip()
                if not boot_id:
                    raise StoreError('Boot identity unavailable')
                firmware = read_json(self.etc + '/bkb-firmware-identity.json', {})
                miner = read_json(self.etc + '/miner.conf', {})

                def collect(meta, runtime):
                    samples = samples_from_api(before, after, devs, pools, cfg, options,
                                               firmware, miner, meta, now, boot_id)
                    return reduce_samples(meta, runtime, samples)

                return self.publish(self.store.tick(collect, now, force=force), now)
            except (StoreError, IOError, OSError, ValueError) as error:
                self.failure(error, now)
                raise

    def failure(self, error, now):
        # Do not leave a successful-looking stale status after a failed writer.
        old = read_json(self.summary, {})
        old.update({'ok': False, 'error': str(error), 'updated': now, 'boards': {}, 'unsaved': True})
        self.publish(old, now)

    def command(self, operation, argument=None, now=None):
        now = int(time.time()) if now is None else now
        if operation not in ('status', 'target', 'clear', 'clear-side', 'reset-board', 'flush'):
            raise StoreError('Unsupported measured-rate command')
        if operation in ('target', 'clear-side') and argument not in ('SD', 'RAM'):
            raise StoreError('Invalid storage target')
        if operation == 'reset-board' and str(argument) not in ('0', '1', '2'):
            raise StoreError('Invalid board')
        with self.locked(wait=operation == 'flush'):
            try:
                if operation == 'status':
                    return public_summary(self.store.status(now=now), now)
                if operation in ('clear', 'clear-side', 'reset-board'):
                    # Invalidate caches before the durable mutation; a crash must
                    # not leave pre-clear estimates looking current.
                    write_json(self.rates, {'version': 2, 'updated': now, 'boards': {}})
                    write_json(self.summary, {'ok': False, 'updated': now, 'error': 'History reset in progress', 'boards': {}})
                if operation == 'target':
                    status = self.store.set_target(argument, now=now)
                elif operation == 'clear':
                    status = self.store.clear(now=now)
                elif operation == 'reset-board':
                    if str(argument) not in ('0', '1', '2'):
                        raise StoreError('Invalid board')
                    status = self.store.reset_board(int(argument), now=now)
                elif operation == 'clear-side':
                    if argument not in ('RAM', 'SD'):
                        raise StoreError('Invalid storage side')
                    current = self.store.status(now=now)
                    if current['target'] == argument:
                        status = self.store.clear(now=now)
                    elif argument == 'RAM':
                        def reset_live(meta, runtime):
                            runtime.clear()
                            return []
                        status = self.store.tick(reset_live, now, force=True)
                    else:
                        status = current
                elif operation == 'flush':
                    status = self.store.tick(lambda meta, runtime: [], now, force=True)
                else:
                    raise StoreError('Unsupported measured-rate command')
                return self.publish(status, now)
            except (StoreError, IOError, OSError, ValueError) as error:
                self.failure(error, now)
                raise


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(json.dumps({'ok': False, 'error': 'Invalid measured-rate command'}))
        return 1
    try:
        result = Service().command(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
        print(json.dumps(result, separators=(',', ':'), allow_nan=False))
        return 0 if result.get('ok') else 1
    except (StoreError, IOError, OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
