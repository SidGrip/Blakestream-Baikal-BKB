#!/usr/bin/env python3
"""Contract smoke coverage for experimental BK-B bridge firmware v1.

The default image is the tracked reference under firmware-custom/build.
A stock dump is not required. Pass an image path as the first argument or
set BKB_V1_IMAGE. Bridge echo is command-path state, not physical PLL lock.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def resolve_image():
    if len(sys.argv) > 1:
        return os.path.abspath(sys.argv[1])
    env = os.environ.get('BKB_V1_IMAGE')
    if env:
        return os.path.abspath(env)
    return os.path.join(HERE, 'build', 'bkb-stm32f407-flash-clockmod-v1.bin')


PATCHED = resolve_image()
if not os.path.isfile(PATCHED):
    sys.stderr.write('missing v1 firmware image: %s\n' % PATCHED)
    sys.stderr.write(
        'Default reference: firmware-custom/build/'
        'bkb-stm32f407-flash-clockmod-v1.bin\n')
    sys.stderr.write(
        'This check does not rebuild from the stock dump and does not skip.\n')
    sys.exit(2)

import struct

import emulate_smoke as emu
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.unicorn_py3.arch.arm import UcAArch32
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_SP,
)
NEW_HANDLER = 0x08005001
REPLY_SENDER = 0x080005FC
FAN_HELPER = 0x08000CB0
STATE_BASE = 0x20000E18
STATE_STRIDE = 0x374
SEED_IDX = 0x1E
READY_OFFSETS = (0x008, 0x0C8, 0x188, 0x248)
PARAM_TAIL = bytes((1, 1, 6, 7, 7, 0, 0))
ACTIVE = {}


class InstrumentedUc(UcAArch32):
    """Seed all host states and capture calls made by the patched handler."""

    def __new__(cls, arch, mode, cpu=None):
        return object.__new__(cls)

    def emu_start(self, begin, until, timeout=0, count=0):
        gates = ACTIVE.get('gates', (5, 5, 5))
        readiness = ACTIVE.get('readiness', {})
        for host in range(3):
            state = STATE_BASE + host * STATE_STRIDE
            self.mem_write(state + 0x320, bytes((gates[host],)))
            for group in range(4):
                for selector in range(16):
                    self.mem_write(state + group * 0xC0 + selector * 12 + 8, b'\x03')
            values = readiness.get(host, (3, 3, 3, 3))
            for offset, value in zip(READY_OFFSETS, values):
                self.mem_write(state + offset, bytes((value,)))
        for (host, group, selector), value in ACTIVE.get('slot_states', {}).items():
            self.mem_write(STATE_BASE + host * STATE_STRIDE + group * 0xC0
                           + selector * 12 + 8, bytes((value,)))

        events = ACTIVE.setdefault('events', [])
        state_writes = ACTIVE.setdefault('state_writes', [])

        def capture_code(uc, address, size, user_data):
            if address == emu.BOARD_SENDER:
                events.append((
                    'sender',
                    uc.reg_read(UC_ARM_REG_R0) & 0xFF,
                    uc.reg_read(UC_ARM_REG_R1) & 0xFF,
                    bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R2), 8)),
                    uc.reg_read(UC_ARM_REG_SP),
                ))
            elif address == emu.FRAME_BUILDER:
                events.append((
                    'frame',
                    uc.reg_read(UC_ARM_REG_R0) & 0xFF,
                    uc.reg_read(UC_ARM_REG_R1) & 0xFF,
                    uc.reg_read(UC_ARM_REG_R2) & 0xFFFF,
                    uc.reg_read(UC_ARM_REG_R3) & 0xFFFF,
                ))
            elif address == emu.COUNTED_DELAY:
                events.append((
                    'delay', uc.reg_read(UC_ARM_REG_R0),
                    uc.reg_read(UC_ARM_REG_SP)))
            elif address == FAN_HELPER:
                events.append((
                    'fan',
                    uc.reg_read(UC_ARM_REG_R0) & 0xFF,
                    uc.reg_read(UC_ARM_REG_R1) & 0xFF,
                    uc.reg_read(UC_ARM_REG_SP),
                ))

        def capture_state_write(uc, access, address, size, value, user_data):
            for host in range(3):
                state = STATE_BASE + host * STATE_STRIDE
                if state <= address < state + STATE_STRIDE:
                    state_writes.append(
                        (host, address - state, size, value))
                    break

        self.hook_add(UC_HOOK_CODE, capture_code)
        self.hook_add(
            UC_HOOK_MEM_WRITE, capture_state_write,
            begin=STATE_BASE, end=STATE_BASE + 3 * STATE_STRIDE - 1)
        result = super().emu_start(begin, until, timeout, count)
        if until == REPLY_SENDER:
            frame = self.reg_read(UC_ARM_REG_R0)
            events.append((
                'ack',
                bytes(self.mem_read(frame, 4)),
                bytes(self.mem_read(frame + 0xD8, 4)),
                self.reg_read(UC_ARM_REG_SP),
            ))
        return result


emu.Uc = InstrumentedUc


def run_case(host, code, dest=1, gates=None, readiness=None,
             sender_results=None, cutoff=0x37, fan=0x46, force_txe=True,
             image_path=None, slot_states=None):
    ACTIVE.clear()
    if gates is not None:
        ACTIVE['gates'] = tuple(gates)
    if readiness is not None:
        ACTIVE['readiness'] = readiness
    if slot_states is not None:
        ACTIVE['slot_states'] = slot_states
    result = emu.run_emulation(
        image_path or PATCHED, code, NEW_HANDLER, full_path=True, miner_id=host, dest=dest,
        gate=5, cutoff=cutoff, fan=fan,
        force_txe=force_txe,
        force_sender_results=sender_results,
        skip_counted_delays=True, instruction_limit=20_000_000,
    )
    result['instrumented'] = {
        'events': ACTIVE.get('events', []),
        'state_writes': ACTIVE.get('state_writes', []),
    }
    if result['fault'] or not result['reached_tail']:
        raise AssertionError('handler did not finish: %s' % result['fault'])
    reply = events(result, 'ack')
    if (len(reply) != 1 or reply[0][1][:3] != bytes((host, 3, 1)) or
            reply[0][2] != bytes(4) or reply[0][3] != 0x20020000):
        raise AssertionError('stock reply shape or stack changed: %s' % reply)
    return result


def events(result, name):
    return [event for event in result['instrumented']['events']
            if event[0] == name]


def ack_status(result):
    ack = events(result, 'ack')
    return ack[-1][1][3] if ack else None


def state_writes(result, offset):
    return [write for write in result['instrumented']['state_writes']
            if write[1] == offset]


def unchanged_echo():
    return {0: SEED_IDX, 1: SEED_IDX, 2: SEED_IDX}


def check(case, reasons, failures):
    if reasons:
        failures.append('%s: %s' % (case, '; '.join(reasons)))
        print('%-42s FAIL' % case)
    else:
        print('%-42s PASS' % case)


def check_echo_writes(result, expected, reasons):
    actual = state_writes(result, 0x302)
    if actual != [(host, 0x302, 1, code) for host, code in expected]:
        reasons.append('echo-writes=%s' % actual)


def check_rejected_preflight(result, reasons):
    if events(result, 'sender') or events(result, 'fan'):
        reasons.append('side-effects')
    if result['report_idx'] != unchanged_echo():
        reasons.append('echo=%s' % result['report_idx'])
    check_echo_writes(result, [], reasons)
    if state_writes(result, 0x305):
        reasons.append('cutoff-write')
    if ack_status(result) != 0:
        reasons.append('ack')


def main():
    image = open(PATCHED, 'rb').read()
    failures = []

    print('v1 bridge-global stock-sequence success')
    for code in (8, 22, 30, 32):
        result = run_case(0, code, sender_results=[1] * 192)
        sends = events(result, 'sender')
        frames = events(result, 'frame')
        delays = [event for event in events(result, 'delay') if event[1] == 1]
        reasons = []
        expected_order = [
            (target, selector)
            for target in range(12)
            for selector in range(16)
        ]
        if [(event[1], event[2]) for event in sends] != expected_order:
            reasons.append('sender-order/count')
        expected_param = bytes((code,)) + PARAM_TAIL
        if any(event[3] != expected_param for event in sends):
            reasons.append('parameter-block')
        if len(delays) != 192:
            reasons.append('delay-count=%d' % len(delays))
        if result['report_idx'] != {0: code, 1: code, 2: code}:
            reasons.append('echo=%s' % result['report_idx'])
        check_echo_writes(result, [(host, code) for host in range(3)], reasons)
        if ack_status(result) != 1:
            reasons.append('ack=%s' % ack_status(result))
        if [(event[1], event[2]) for event in events(result, 'fan')] != [
                (0, 0x46), (1, 0x46), (2, 0x46)]:
            reasons.append('fan')

        table_offset = emu.CLOCK_INDEX_TABLE - emu.FLASH_BASE + code * 4
        table = image[table_offset:table_offset + 4]
        selector_zero_values = (
            0x1E77,
            struct.unpack('<H', table[:2])[0],
            struct.unpack('<H', table[2:])[0],
        )
        for target, selector in expected_order:
            target_frames = [
                event for event in frames
                if event[1] == target and event[2] == selector
            ]
            registers = [event[3] for event in target_frames[:3]]
            values = tuple(event[4] for event in target_frames[:3])
            expected_values = (
                selector_zero_values if selector == 0
                else (0x1E77, 0x1102, 0x0800)
            )
            if registers != [8, 7, 6] or values != expected_values:
                reasons.append(
                    'frame-t%d-s%d=%s/%s' %
                    (target, selector, registers, values))
                break

        call_sps = [event[-1] for event in
                    sends + delays + events(result, 'fan')]
        if (result['sp_end'] != 0x20020000 or result['min_sp'] % 8 or
                any(sp % 8 for sp in call_sps)):
            reasons.append('stack-alignment')
        check('global-code%d' % code, reasons, failures)

    print('\nv1 per-host failure and echo gating')
    for failed_call in (0, 63, 64, 127, 128, 191):
        forced = [1] * 192
        forced[failed_call] = 0
        result = run_case(0, 22, sender_results=forced)
        failed_host = failed_call // 64
        expected = {0: 22, 1: 22, 2: 22}
        expected[failed_host] = SEED_IDX
        reasons = []
        if len(events(result, 'sender')) != 192:
            reasons.append('incomplete-sequence')
        if result['report_idx'] != expected:
            reasons.append('echo=%s' % result['report_idx'])
        check_echo_writes(result, [
            (host, 22) for host in range(3) if host != failed_host], reasons)
        if ack_status(result) != 0:
            reasons.append('ack=%s' % ack_status(result))
        check('sender-fail-call%d' % failed_call, reasons, failures)

    print('\nv1 preflight, fan-only, and invalid input')
    for failed_host in range(3):
        gates = [5, 5, 5]
        gates[failed_host] = 3
        result = run_case(0, 22, gates=gates)
        reasons = []
        check_rejected_preflight(result, reasons)
        check('state-gate-host%d' % failed_host, reasons, failures)

    for failed_host in range(3):
        for target in range(4):
            for value in (0, 1, 2, 4):
                readiness = [3] * 4
                readiness[target] = value
                result = run_case(0, 22, readiness={failed_host: readiness})
                reasons = []
                check_rejected_preflight(result, reasons)
                check('ready-host%d-target%d-value%d' % (
                    failed_host, target, value), reasons, failures)

    result = run_case(0, 0, cutoff=0, fan=63,
                      readiness={host: (0, 0, 0, 0) for host in range(3)})
    reasons = []
    if events(result, 'sender'):
        reasons.append('clock-path')
    if [(event[1], event[2]) for event in events(result, 'fan')] != [
            (0, 63), (1, 63), (2, 63)]:
        reasons.append('fan')
    if ack_status(result) != 1:
        reasons.append('ack')
    if result['report_idx'] != unchanged_echo():
        reasons.append('echo=%s' % result['report_idx'])
    check_echo_writes(result, [], reasons)
    check('global-fan-only', reasons, failures)

    for host, code, dest in (
            (3, 30, 1), (0, 7, 1), (0, 33, 1), (0, 30, 2)):
        result = run_case(host, code, dest=dest)
        reasons = []
        check_rejected_preflight(result, reasons)
        check('invalid-h%d-c%d-d%d' % (host, code, dest), reasons, failures)

    print('\nv1 board-specific regression')
    for host in range(3):
        result = run_case(host, 22, dest=0, sender_results=[1] * 64,
                          gates=[5 if other == host else 3 for other in range(3)],
                          readiness={other: (0, 0, 0, 0)
                                     for other in range(3) if other != host})
        expected_order = [
            (target, selector)
            for target in range(host * 4, host * 4 + 4)
            for selector in range(16)
        ]
        expected_echo = unchanged_echo()
        expected_echo[host] = 22
        reasons = []
        if [(event[1], event[2]) for event in
                events(result, 'sender')] != expected_order:
            reasons.append('sender-order/count')
        if result['report_idx'] != expected_echo:
            reasons.append('echo=%s' % result['report_idx'])
        check_echo_writes(result, [(host, 22)], reasons)
        if ack_status(result) != 1:
            reasons.append('ack')
        check('individual-host%d' % host, reasons, failures)

        for failed_call in (0, 15, 16, 31, 32, 47, 48, 63):
            forced = [1] * 64
            forced[failed_call] = 0
            result = run_case(host, 22, dest=0, sender_results=forced)
            reasons = []
            if [(event[1], event[2]) for event in
                    events(result, 'sender')] != expected_order:
                reasons.append('sender-order/count')
            if result['report_idx'] != unchanged_echo():
                reasons.append('echo=%s' % result['report_idx'])
            check_echo_writes(result, [], reasons)
            if ack_status(result) != 0:
                reasons.append('ack')
            check('individual-host%d-fail%d' % (host, failed_call),
                  reasons, failures)

        for target in range(4):
            readiness = [3] * 4
            readiness[target] = 2
            result = run_case(host, 22, dest=0, readiness={host: readiness})
            reasons = []
            check_rejected_preflight(result, reasons)
            check('individual-host%d-ready%d' % (host, target), reasons, failures)

        result = run_case(host, 22, dest=0, force_txe=False)
        reasons = []
        if len(events(result, 'sender')) != 64:
            reasons.append('incomplete-sequence')
        if result['report_idx'] != unchanged_echo():
            reasons.append('echo=%s' % result['report_idx'])
        check_echo_writes(result, [], reasons)
        if ack_status(result) != 0:
            reasons.append('ack')
        check('individual-host%d-spi-timeout' % host, reasons, failures)

    print('\n==== %s ====' % (
        'ALL PASS' if not failures else 'FAILURES: %d' % len(failures)))
    for failure in failures:
        print('  FAIL:', failure)
    raise SystemExit(0 if not failures else 1)


if __name__ == '__main__':
    main()
