"""Shared RE helpers for the BK-B STM32F407 firmware dump.
Run from any directory: python3 /path/to/checkout/firmware-dump/reftools.py 08001a64
Uses the bundled stock bridge image; BKB_STOCK_DUMP selects another dump.
"""
import os
from pathlib import Path
import struct
from capstone import *

BASE = 0x08000000
PATH = Path(os.environ.get('BKB_STOCK_DUMP', str(
    Path(__file__).resolve().parent.parent /
    'overlay/opt/scripta/firmware/stock.bin'))).expanduser()
D = PATH.read_bytes()

_md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
_md.detail = True

def off(addr): return addr - BASE
def at(addr):  return D[off(addr):]

def u32(addr): return struct.unpack('<I', D[off(addr):off(addr)+4])[0]
def u16(addr): return struct.unpack('<H', D[off(addr):off(addr)+2])[0]

def find_u32(val):
    """All addresses whose LE u32 == val (literal-pool / data xrefs)."""
    needle = struct.pack('<I', val); out = []; i = 0
    while True:
        j = D.find(needle, i)
        if j < 0: break
        out.append(BASE + j); i = j + 1
    return out

def disasm(addr, count=40):
    """Disassemble `count` instructions starting at addr. Returns list of (addr,mnem,op,bytes)."""
    out = []
    for ins in _md.disasm(at(addr), addr):
        out.append((ins.address, ins.mnemonic, ins.op_str, ins.bytes.hex()))
        if len(out) >= count: break
    return out

def show(addr, count=40):
    for a, m, o, b in disasm(addr, count):
        print("0x%08X  %-8s %-28s ; %s" % (a, m, o, b))

def sweep(start, end, predicate):
    """Resyncing linear sweep. predicate(ins)->bool; returns list of (addr,'mnem op')."""
    hits = []; pos = off(start); endo = off(end)
    while pos < endo:
        got = False
        for ins in _md.disasm(D[pos:endo], BASE + pos):
            got = True
            if predicate(ins): hits.append((ins.address, ins.mnemonic + ' ' + ins.op_str))
            pos = off(ins.address) + ins.size
        if not got: pos += 2
    return hits

def bl_targets_to(target, start=0x3000, end=0x100000):
    """Find BL/BLX sites whose resolved target == `target` (call xrefs)."""
    res = []
    def pred(ins):
        if ins.mnemonic in ('bl', 'blx') and ins.op_str.startswith('#'):
            try: t = int(ins.op_str[1:], 16)
            except: return False
            return t == target
        return False
    return sweep(start, end, pred)

def func_start(addr, back=0x400):
    """Scan backward for a likely prologue: push {...,lr}  (encodings 0xB5xx / 0xE92D....)."""
    a = off(addr)
    lo = max(0, a - back)
    cand = None
    for p in range(a, lo, -2):
        hw = struct.unpack('<H', D[p:p+2])[0]
        if (hw & 0xFF00) == 0xB500:  # push {..,lr}
            cand = BASE + p; break
        if (hw & 0xFFFF) == 0xE92D:  # push.w {...} (T2) first halfword
            cand = BASE + p; break
    return cand

if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 2:
        show(int(sys.argv[1], 16), int(sys.argv[2]) if len(sys.argv) > 2 else 40)
