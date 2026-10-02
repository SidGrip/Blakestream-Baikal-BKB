"""Container-only GPIO redirection and shorter hardware settling delays."""
import os
import sys

if os.path.isfile('/etc/bkb-firmware-qa-container'):
    import __builtin__
    import json
    import time

    original_open = __builtin__.open
    original_sleep = time.sleep

    class GPIOFile(object):
        def __init__(self, handle, pin, node):
            self.handle, self.pin, self.node = handle, pin, node

        def write(self, value):
            result = self.handle.write(value)
            with original_open('/run/qa-hardware/gpio.jsonl', 'a') as trace:
                trace.write(json.dumps([self.pin, self.node, str(value)]) + '\n')
            return result

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def fixture_open(path, *args, **kwargs):
        if isinstance(path, basestring) and path.startswith('/sys/class/gpio_sw/'):
            pin, node = path.rsplit('/', 2)[-2:]
            path = path.replace('/sys/class/gpio_sw/', '/run/qa-hardware/gpio/', 1)
            return GPIOFile(original_open(path, *args, **kwargs), pin, node)
        return original_open(path, *args, **kwargs)

    def fixture_sleep(seconds):
        args = getattr(sys, 'argv', [])
        if args and args[0] == '/opt/scripta/firmware/dfu_flash.py':
            seconds = min(seconds, 0.02)
        return original_sleep(seconds)

    __builtin__.open = fixture_open
    time.sleep = fixture_sleep
