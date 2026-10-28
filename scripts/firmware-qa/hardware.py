#!/usr/bin/python
"""File-backed USB/DFU transport; never opens a device node."""
from __future__ import print_function
import json
import os
import struct
import sys
import time

ROOT = '/run/qa-hardware'


def load(name):
    with open(ROOT + '/' + name) as handle:
        return json.load(handle)


def save(name, value):
    with open(ROOT + '/' + name, 'w') as handle:
        json.dump(value, handle)


def event(kind, **data):
    data.update(kind=kind, uid=os.geteuid(), at=time.time())
    with open(ROOT + '/events.jsonl', 'a') as handle:
        handle.write(json.dumps(data) + '\n')


def usb_mode():
    levels = {}
    mode = 'app'
    for line in open(ROOT + '/gpio.jsonl'):
        pin, node, value = json.loads(line)
        previous = levels.get((pin, node))
        levels[(pin, node)] = value
        if (pin == 'PA10' and node == 'data' and previous == '0' and value == '1' and
                levels.get(('PA10', 'cfg')) == levels.get(('PA18', 'cfg')) == '1'):
            mode = 'dfu' if levels.get(('PA18', 'data')) == '0' else 'app'
    return mode


def main():
    if not os.path.isfile('/etc/bkb-firmware-qa-container') or not os.path.isfile('/.dockerenv'):
        return 99
    fault = load('control.json').get('fault', '')
    state = load('state.json')
    mode = usb_mode()
    if os.path.basename(sys.argv[0]) == 'lsusb':
        if mode == 'dfu':
            if fault != 'dfu-missing':
                print('Bus 001 Device 001: ID 0483:df11 Fixture DFU')
        else:
            state['app_polls'] += 1
            save('state.json', state)
            if fault != 'slow-usb' or state['app_polls'] > 2:
                print('Bus 001 Device 001: ID 0483:5740 Fixture Application')
        return 0

    args = sys.argv[1:]
    if mode != 'dfu' or args[:4] != ['-a', '0', '-d', '0483:df11']:
        event('invalid-transport', args=args)
        return 98
    if '-U' in args:
        if args[-2:] != ['-s', '0x08000000:0x100000']:
            return 98
        state['reads'] += 1
        data = open(ROOT + '/flash.bin', 'rb').read()
        if (fault == 'backup-mismatch' and state['reads'] == 2) or (
                fault == 'readback-mismatch' and state['writes'] == 1):
            data = data[:-1] + struct.pack('B', ord(data[-1:]) ^ 1)
        target = args[args.index('-U') + 1]
        event('read', target=target, count=state['reads'])
        save('state.json', state)
        if fault == 'read-failure':
            return 1
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            event('upload-output-exists', target=target)
            return 74
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        return 0
    if '-D' in args:
        if args[-2:] != ['-s', '0x08000000']:
            return 98
        source = args[args.index('-D') + 1]
        data = open(source, 'rb').read()
        if len(data) != 0x100000:
            return 98
        state['writes'] += 1
        event('write', source=source, count=state['writes'])
        save('state.json', state)
        fail = fault == 'rollback-failure' or (fault == 'write-failure' and state['writes'] == 1)
        with open(ROOT + '/flash.bin', 'wb') as handle:
            handle.write(data[:512] + b'\xff' * (len(data) - 512) if fail else data)
        return 1 if fail else 0
    return 98


if __name__ == '__main__':
    sys.exit(main())
