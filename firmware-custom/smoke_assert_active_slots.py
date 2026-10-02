#!/usr/bin/env python3
"""Release V1 must skip only slots that stock scheduling already excludes."""
from pathlib import Path
import smoke_assert_v1 as base


def slot_key(ordinal):
    return ordinal // 64, (ordinal % 64) // 16, ordinal % 16


def run(host=0, dest=1, code=8, slots=None, failures=(), gates=None):
    slots = slots or {}
    ordinals = range(192) if dest == 1 else range(host * 64, host * 64 + 64)
    replies = [i not in failures for i in ordinals if slots.get(slot_key(i), 3) in (2, 3)]
    result = base.run_case(host, code, dest=dest, gates=gates, cutoff=55, fan=70,
                           sender_results=replies, slot_states=slots)
    assert result['sp_end'] == 0x20020000 and result['min_sp'] % 8 == 0
    assert all(w[1] in (0x302, 0x305, 0x306)
               for w in result['instrumented']['state_writes'])
    return result


count = 0
for dest in (0, 1):
    for host in range(3):
        boards = [host] if dest == 0 else range(3)
        healthy = run(host=host, dest=dest)
        for board in boards:
            for state in (0, 1, 2, 3, 4, 255):
                ordinal = board * 64 + 31
                result = run(host=host, dest=dest, slots={slot_key(ordinal): state}, failures=(ordinal,))
                active = state in (2, 3)
                assert base.ack_status(result) == (0 if active else 1)
                expected = [(e[1], e[2], e[3]) for e in base.events(healthy, 'sender')
                            if active or e[1] * 16 + e[2] != ordinal]
                assert [(e[1], e[2], e[3]) for e in base.events(result, 'sender')] == expected
                assert base.events(result, 'fan') == base.events(healthy, 'fan')
                assert len([e for e in base.events(result, 'delay') if e[1] == 1]) == (64 if dest == 0 else 192)
                for h in boards:
                    assert result['report_idx'][h] == (30 if active and h == board else 8)
                count += 1
            # Selector zero remains a readiness gate even when its slot is inactive.
            for state in (0, 1, 2, 4, 255):
                result = run(host=host, dest=dest, slots={(board, 0, 0): state})
                assert base.ack_status(result) == 0 and not base.events(result, 'sender')
                assert result['report_idx'] == base.unchanged_echo()
                count += 1
            gates = [5, 5, 5]
            gates[board] = 3
            result = run(host=host, dest=dest, gates=gates)
            assert base.ack_status(result) == 0 and not base.events(result, 'sender')
            count += 1

for code in (8, 30, 32):
    slots = {(0, 0, i): 0 for i in range(1, 9)}
    result = run(slots=slots, code=code)
    assert base.ack_status(result) == 1 and result['report_idx'] == {h: code for h in range(3)}
    assert len(base.events(result, 'sender')) == 184
    assert all(e[3] == bytes((code, 1, 1, 6, 7, 7, 0, 0)) for e in base.events(result, 'sender'))
    count += 1

result = run(code=0, slots={(0, 0, 0): 0})
assert base.ack_status(result) == 1 and not base.events(result, 'sender')
assert result['report_idx'] == base.unchanged_echo()
count += 1

image = Path(base.PATCHED).read_bytes()
assert len(image) == 1048576
assert image[0x1094:0x109c] == bytes.fromhex('237a023b012b23d8')
print('PASS %d active-slot cases: strict active failures, unavailable slots skipped, readiness retained' % count)
