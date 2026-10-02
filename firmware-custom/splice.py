#!/usr/bin/env python3
"""Splice the assembled stub into a copy of the firmware dump and repoint the
SET_OPTION dispatch handler. Pure data edit; no flashing.

Usage: splice.py <dump.bin> <stub.bin> <out.bin> <patch_vma> <dispatch_off> <new_handler>
"""
import sys, struct

dump_path, stub_path, out_path, patch_vma_s, disp_off_s, new_handler_s = sys.argv[1:7]
patch_vma   = int(patch_vma_s, 0)
disp_off    = int(disp_off_s, 0)
new_handler = int(new_handler_s, 0)
FLASH_BASE  = 0x08000000

dump = bytearray(open(dump_path, "rb").read())
stub = open(stub_path, "rb").read()
patch_off = patch_vma - FLASH_BASE

# 1) sanity: the region we overwrite must currently be erased (0xFF)
region = dump[patch_off:patch_off + len(stub)]
assert all(b == 0xFF for b in region), (
    "refusing to overwrite non-erased flash at 0x%08X" % patch_vma)

# 2) sanity: dispatch entry currently points at the stock SET_OPTION handler
old = struct.unpack_from("<I", dump, disp_off)[0]
assert old == 0x08000CED, "unexpected dispatch word 0x%08X at 0x%X" % (old, disp_off)

# 3) write stub + repoint dispatch entry
dump[patch_off:patch_off + len(stub)] = stub
struct.pack_into("<I", dump, disp_off, new_handler)

open(out_path, "wb").write(dump)
print("  stub @0x%08X (%d bytes); dispatch[0x%X] 0x%08X -> 0x%08X"
      % (patch_vma, len(stub), disp_off, old, new_handler))
print("  output size %d bytes" % len(dump))
