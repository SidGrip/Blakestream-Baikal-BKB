#!/usr/bin/python
"""Loopback-only miner API substitute, launched by the real screen workflow."""
from __future__ import print_function
import hashlib
import json
import os
import socket
import sys

ROOT = '/run/qa-hardware'
if not os.path.isfile('/etc/bkb-firmware-qa-container') or not os.path.isfile('/.dockerenv'):
    sys.exit(99)
fault = json.load(open(ROOT + '/control.json')).get('fault', '')
digest = hashlib.md5(open(ROOT + '/flash.bin', 'rb').read()).hexdigest()
if fault == 'restart-failure' or digest not in (
        'd5f844be2952fd713717a71a67e2be61', '0e8467eddabf589ad94549a8d572c759',
        '18babe0c4c67d9146e230f9dc423f2f2'):
    sys.exit(1)
with open(ROOT + '/miner.json', 'w') as handle:
    json.dump({'pid': os.getpid(), 'uid': os.geteuid()}, handle)
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.0.0.1', 4028))
server.listen(16)
print('Fixture miner ready')
sys.stdout.flush()
while True:
    client, _ = server.accept()
    try:
        client.settimeout(2)
        raw = client.recv(8192)
        if not raw:
            continue
        request = json.loads(raw.decode('ascii'))
        cmd = request.get('command', '')
        parameter = str(request.get('parameter', ''))
        enabled = ('0', '1', '2')
        ok, message = True, 'fixture OK'
        if cmd in ('ascenable', 'ascdisable'):
            ok = parameter in enabled
        elif cmd in ('ascpool', 'ascfailover'):
            parts = parameter.split(',')
            ok = len(parts) == 2 and parts[0] in enabled and parts[1] in ('0', '-1')
            cleared = ok and parts[1] == '-1'
            if cmd == 'ascpool':
                message = 'cleared pool pin' if cleared else 'pinned to pool fixture'
            else:
                message = 'cleared failover pin' if cleared else 'failover pinned'
        elif cmd != 'version':
            ok = False
        response = {'STATUS': [{'STATUS': 'S' if ok else 'E', 'Msg': message if ok else 'invalid fixture command'}],
                    'VERSION': [{'API': '3.7'}]}
        client.sendall(json.dumps(response, separators=(',', ':')).encode('ascii') + b'\x00')
    except (ValueError, socket.error):
        pass
    finally:
        client.close()
