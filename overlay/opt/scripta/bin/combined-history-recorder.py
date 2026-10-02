#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
combined-history-recorder.py - Blakestream-GaintB consolidated recorder.

Runs the five per-recorder modules in ONE interpreter per minute instead of a
separate python fork per recorder, to cut CPU/load on the controller. Each
recorder keeps its own bs-* journal tag (the Dev Tools Recorders health column
reads journalctl -t bs-<tag>), its own RAM/SD target, and runs inside its own
timeout + try/except so one recorder cannot stall or crash the others. The pool
recorder keeps its 5-minute cadence via a file-age gate. Fan control and the
overheat temp-watchdog stay on their own crons/processes by design (safety
actuators must not share fate with diagnostic recorders).

The recorder scripts are loaded by path (their filenames are hyphenated, so they
cannot be imported by name) and their main() is called; the `if __name__` guard
keeps a standalone `python <recorder>.py` working for manual debugging.

Compatible with Python 2.7 and Python 3.
"""

from __future__ import print_function
import imp   # py2.7 + py3.5 on this controller; load-by-path of hyphenated names
import os
import signal
import subprocess
import sys
import time
import traceback

try:
    from cStringIO import StringIO   # py2: accepts native str
except ImportError:
    from io import StringIO          # py3: accepts str

BIN = '/opt/scripta/bin'
RUNTIME = '/run/blakestream'
POOL_TICK = os.path.join(RUNTIME, '.pool-tick')   # stamps the last pool attempt
POOL_MIN_INTERVAL = 290        # pool recorder is */5; gate to ~every 5 minutes
PER_RECORDER_TIMEOUT = 20      # backstop; recorders already settimeout(4) per API call

# (journal tag, recorder filename). Order matters: the failover recorder reads
# back hashrate-history.json, so hashrate must run before it.
RECORDERS = [
    ('bs-hashrate', 'hashrate-history-recorder.py'),
    ('bs-failover', 'failover-history-recorder.py'),
    ('bs-clock',    'clock-history-recorder.py'),
    ('bs-history',  'pool-history-recorder.py'),
    ('bs-diagnostics', 'bkb-diagnostics-recorder.py'),
]

_UNICODE = type(u'')


class RecorderTimeout(Exception):
    pass


def emit(tag, text):
    """Send captured output to the recorder's journal tag, mirroring the old
    `python <recorder>.py 2>&1 | logger -t bs-<tag>` cron pipe. Stays silent on
    empty output so a clean run leaves no journal line (health falls back to
    file mtime), exactly as before."""
    if not text:
        return
    if not text.endswith('\n'):
        text += '\n'
    data = text.encode('utf-8', 'replace') if isinstance(text, _UNICODE) else text
    try:
        p = subprocess.Popen(['/usr/bin/logger', '-t', tag], stdin=subprocess.PIPE)
        p.communicate(data)
    except Exception:
        pass   # logging must never break the orchestrator


def _touch(path):
    try:
        with open(path, 'a'):
            os.utime(path, None)
    except Exception:
        pass


def run_one(tag, fname):
    path = os.path.join(BIN, fname)
    modname = 'rec_' + fname.replace('-', '_').replace('.py', '')
    buf = StringIO()
    old_out, old_err, old_argv = sys.stdout, sys.stderr, sys.argv
    sys.stdout = buf
    sys.stderr = buf
    sys.argv = [path]   # recorders read sys.argv (clock's --force); give a clean argv

    def _on_alarm(signum, frame):
        raise RecorderTimeout('timed out after %ds' % PER_RECORDER_TIMEOUT)

    prev = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(PER_RECORDER_TIMEOUT)
    try:
        mod = imp.load_source(modname, path)
        if hasattr(mod, 'main'):
            mod.main()
    except RecorderTimeout as e:
        buf.write('RECORDER TIMEOUT: %s (sgminer API hang?)\n' % e)
    except SystemExit:
        pass   # main() may sys.exit(0)
    except Exception:
        buf.write('RECORDER FAILED:\n')
        traceback.print_exc(file=buf)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev)
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

    emit(tag, buf.getvalue())


def pool_due():
    """Pool recorder cadence is */5. Gate on a dedicated RAM tick file (stamped on
    each attempt) rather than pool-history.json, so the cadence holds regardless of
    the pool recorder's RAM/SD target or a no-write run during a sgminer outage."""
    try:
        return (time.time() - os.path.getmtime(POOL_TICK)) >= POOL_MIN_INTERVAL
    except OSError:
        return True


def main():
    for tag, fname in RECORDERS:
        if fname == 'pool-history-recorder.py':
            if not pool_due():
                continue
            _touch(POOL_TICK)   # stamp the attempt; gates the next ~5 minutes
        try:
            run_one(tag, fname)
        except Exception:
            # Last-resort guard: a fault in the harness for one recorder must not
            # stop the others.
            try:
                emit(tag, 'ORCHESTRATOR ERROR:\n' + traceback.format_exc())
            except Exception:
                pass


if __name__ == '__main__':
    main()
