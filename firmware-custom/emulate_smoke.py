#!/usr/bin/env python3
"""
Local smoke test for the BK-B clock patch — emulate the STM32F407 firmware with
Unicorn, drive the patched SET_OPTION handler with the exact clock code the
GUI->sgminer chain produces, and capture the SPI bytes emitted to each board.
NOTHING is sent to the miner.

Usage:
  emulate_smoke.py <image.bin> <clock_code> [--from-handler 0x08005001] [--trace]
  emulate_smoke.py <image.bin> --mhz 520        # converts MHz -> host code

Exit 0 if the run completed cleanly (reached the tail-jump to the stock handler).
Prints the per-board SPI DR write log and the decoded clock-table entry.
"""
import sys, struct
from unicorn import *
from unicorn.arm_const import *

FLASH_BASE = 0x08000000
RAM_BASE   = 0x20000000
RAM_SIZE   = 0x20000
PERIPH_BASE= 0x40000000
PERIPH_SIZE= 0x00100000
USB_BASE   = 0x50000000
USB_SIZE   = 0x00040000

DATA_LMA   = 0x080041A0      # .data load addr in flash
DATA_VMA   = 0x20000000      # .data run addr in RAM
DATA_LEN   = 0x29C
CLOCK_INDEX_TABLE   = 0x080041AC      # LMA of the clock table (VMA 0x2000000C)

STOCK_SET_OPTION = 0x08000CEC   # patched handler tail-jumps here -> our stop point
BOARD_SENDER     = 0x08001A64   # the board SET_FREQ-over-SPI sender
FRAME_BUILDER    = 0x080018BC
SPI_TX           = 0x08001830
CS_RESTORE       = 0x08001790
CS_SETUP         = 0x080017B4
BOARD_TABLE      = 0x08003EAC
COUNTED_DELAY    = 0x080006F0

SPI1_BASE = 0x40013000
SPI2_BASE = 0x40003800
SPI3_BASE = 0x40003C00

def host_code(mhz):
    return ((mhz // 10) % 20) + 2

def index_code(mhz):
    """Historical v3 encoder: the shared clock/report index, clamped to the
    mode-0x10 ladder. clk/10-18 (400->22, 480->30, 500->32), clamp [8,32]."""
    return max(8, min(32, mhz // 10 - 18))

def board_path(image, board, seq):
    """Decode the stock board-selection table used by FRAME_BUILDER."""
    if board < 0 or board > 11:
        return None
    group = 0 if seq <= 7 else 1
    base = (BOARD_TABLE - FLASH_BASE) + board * 0x24
    spi = struct.unpack('<I', image[base:base + 4])[0]
    restore = base + group * 8 + 4
    setup = base + (group + 2) * 8 + 4
    return {
        'board': board,
        'seq': seq,
        'spi': spi,
        'selector': (seq + ((board & 1) << 4)) & 0xff,
        'setup_gpio': struct.unpack('<I', image[setup:setup + 4])[0],
        'setup_mask': struct.unpack('<H', image[setup + 4:setup + 6])[0],
        'restore_gpio': struct.unpack('<I', image[restore:restore + 4])[0],
        'restore_mask': struct.unpack('<H', image[restore + 4:restore + 6])[0],
    }

def spi_board(addr):
    if SPI1_BASE <= addr < SPI1_BASE+0x400: return ('SPI1', SPI1_BASE)
    if SPI2_BASE <= addr < SPI2_BASE+0x400: return ('SPI2', SPI2_BASE)
    if SPI3_BASE <= addr < SPI3_BASE+0x400: return ('SPI3', SPI3_BASE)
    return (None, None)

def run_emulation(img_path, code, start, trace=False, full_path=False,
                  gate=5, cutoff=0x37, fan=0x46, algo=0x00, miner_id=0,
                  dest=0,
                  prefill=None, repeat=1, force_txe=True, skip_v4_settle=False,
                  force_sender_result=None, force_sender_results=None,
                  skip_counted_delays=False, instruction_limit=5_000_000):
    image = open(img_path, 'rb').read()

    # Decode the selected clock-table entry from the image's flash table.
    eoff = (CLOCK_INDEX_TABLE - FLASH_BASE) + code*4
    entry = image[eoff:eoff+4]
    tag, mode = entry[0], entry[1]
    freq = struct.unpack('<H', entry[2:4])[0]

    TRAP = 0xDEADBEEF
    REPLY_SENDER = 0x080005FC   # original handler tail-jumps here to emit the ACK
    STATE_BASE = 0x20000E18; STRIDE = 0x374
    TIM2_BASE = 0x40000000
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    try: uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
    except Exception: pass

    uc.mem_map(FLASH_BASE, 0x100000)
    uc.mem_write(FLASH_BASE, image)
    uc.mem_map(RAM_BASE, RAM_SIZE)
    uc.mem_write(DATA_VMA, image[DATA_LMA-FLASH_BASE: DATA_LMA-FLASH_BASE+DATA_LEN])
    if prefill is not None:                 # stress: poison the stack/RAM working area
        uc.mem_write(0x20010000, bytes([prefill]) * 0x8000)

    for b in range(3):                      # per-board state: gate + report idx
        st = STATE_BASE + b*STRIDE
        uc.mem_write(st+0x320, bytes([gate]))
        uc.mem_write(st+0x302, b'\x1e')

    spi_writes = []; spi_by_board = {}; periph_writes = []
    sender_events = []; frame_events = []; setup_events = []; restore_events = []
    active_board = [None]
    opts = {'force_txe': force_txe}

    def mmio_read(uc, offset, size, ud):
        addr = PERIPH_BASE + offset
        name, base = spi_board(addr)
        if name and (addr - base) == 0x08:  # SPI status reg: TXE/RXNE ready (unless simulating timeout)
            return 0x03 if opts['force_txe'] else 0x00
        return 0
    def mmio_write(uc, offset, size, value, ud):
        addr = PERIPH_BASE + offset
        name, base = spi_board(addr)
        if name and (addr - base) == 0x0C:
            spi_writes.append((name, base, value & 0xFFFF))
            spi_by_board.setdefault(active_board[0], []).append(value & 0xFFFF)
        else:
            periph_writes.append((addr, value & 0xFFFFFFFF))

    uc.mmio_map(PERIPH_BASE, PERIPH_SIZE, mmio_read, None, mmio_write, None)
    uc.mmio_map(USB_BASE, USB_SIZE, lambda *a: 0, None, lambda *a: None, None)
    uc.mem_map(0x00000000, 0x10000)         # scratch for CS-GPIO writes (base from RAM state)

    frame = 0x20010000 if prefill is None else 0x2001A000
    fb = bytearray(16)
    fb[0] = miner_id
    fb[1] = 0x03
    fb[3] = dest & 0xFF
    fb[4] = code & 0xFF
    fb[5] = algo & 0xFF
    fb[6] = cutoff & 0xFF
    fb[7] = fan & 0xFF
    uc.mem_write(frame, bytes(fb))

    # track stack low-water + cutoff write
    min_sp = [0x20020000]; cutoff_writes = []
    def ram_hook(uc, access, addr, size, value, ud):
        st0 = STATE_BASE + miner_id*STRIDE
        if addr == st0+0x305:
            cutoff_writes.append(value & 0xFF)
    uc.hook_add(UC_HOOK_MEM_WRITE, ram_hook, begin=RAM_BASE, end=RAM_BASE+RAM_SIZE)

    calls = {'sender':0, 'frame_builder':0, 'spi_tx':0,
             'cs_setup':0, 'cs_restore':0, 'counted_delay':0,
             'settle_delay':0}
    last_pc = [0]
    def hook_code(uc, addr, size, ud):
        if skip_v4_settle and addr == 0x08005080:
            uc.reg_write(UC_ARM_REG_PC, 0x0800508e | 1)
            return
        if addr == COUNTED_DELAY:
            calls['counted_delay'] += 1
            if uc.reg_read(UC_ARM_REG_R0) == 5:
                calls['settle_delay'] += 1
            if skip_counted_delays:
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
                return
        # The stock sender's final status depends on an ASIC response word.
        # Success tests inject that external response at the common epilogue;
        # timeout tests leave the stock return path untouched.
        if addr == 0x08001AF4:
            forced = force_sender_result
            if force_sender_results is not None:
                call_index = calls['sender'] - 1
                if 0 <= call_index < len(force_sender_results):
                    forced = force_sender_results[call_index]
            if forced is not None:
                uc.reg_write(UC_ARM_REG_R0, int(bool(forced)))
        if addr == BOARD_SENDER:
            calls['sender'] += 1
            sender_events.append({
                'board': uc.reg_read(UC_ARM_REG_R0) & 0xff,
                'seq': uc.reg_read(UC_ARM_REG_R1) & 0xff,
            })
        elif addr == FRAME_BUILDER:
            calls['frame_builder'] += 1
            board = uc.reg_read(UC_ARM_REG_R0) & 0xff
            seq = uc.reg_read(UC_ARM_REG_R1) & 0xff
            active_board[0] = board
            frame_events.append(board_path(image, board, seq))
        elif addr == CS_SETUP:
            calls['cs_setup'] += 1
            board = uc.reg_read(UC_ARM_REG_R0) & 0xff
            seq = uc.reg_read(UC_ARM_REG_R1) & 0xff
            active_board[0] = board
            setup_events.append(board_path(image, board, seq))
        elif addr == CS_RESTORE:
            calls['cs_restore'] += 1
            board = uc.reg_read(UC_ARM_REG_R0) & 0xff
            seq = uc.reg_read(UC_ARM_REG_R1) & 0xff
            restore_events.append(board_path(image, board, seq))
        elif addr == SPI_TX: calls['spi_tx'] += 1
        sp = uc.reg_read(UC_ARM_REG_SP)
        if sp < min_sp[0]: min_sp[0] = sp
        last_pc[0] = addr
        if trace: print("  pc=0x%08X sp=0x%08X" % (addr, sp))
    uc.hook_add(UC_HOOK_CODE, hook_code)

    reached_end = False; fault = None
    # full path: run handler -> original cutoff/fan -> up to the (stock) reply sender.
    # We stop at the reply sender entry: emulating the USART/USB TX itself needs boot
    # state we don't seed, and that code is unchanged stock anyway.
    stop_at = REPLY_SENDER if full_path else STOCK_SET_OPTION
    runs = 0
    try:
        for runs in range(1, repeat+1):
            uc.reg_write(UC_ARM_REG_SP, 0x20020000)
            uc.reg_write(UC_ARM_REG_R0, frame)
            uc.reg_write(UC_ARM_REG_LR, TRAP)
            uc.reg_write(UC_ARM_REG_CPSR, uc.reg_read(UC_ARM_REG_CPSR) | (1 << 5))
            try:
                uc.emu_start(start | 1, stop_at, timeout=0,
                             count=instruction_limit)
                pc = uc.reg_read(UC_ARM_REG_PC)
                reached_end = pc in (stop_at, stop_at | 1)
                if not reached_end:
                    fault = "instruction limit reached @pc=0x%08X" % pc
                    break
            except UcError as e:
                pc = uc.reg_read(UC_ARM_REG_PC)
                if full_path and (pc == TRAP or pc == TRAP-1):
                    reached_end = True   # clean return into the LR trap
                else:
                    fault = "%s @pc=0x%08X (last 0x%08X)" % (e, pc, last_pc[0]); break
    except Exception as e:
        fault = "harness:%s" % e

    sp_end = uc.reg_read(UC_ARM_REG_SP)
    perboard = {}
    for name, base, val in spi_writes:
        perboard.setdefault(name, []).append(val)
    tim2 = [(a,v) for a,v in periph_writes if TIM2_BASE <= a < TIM2_BASE+0x400 and 0x34 <= (a-TIM2_BASE) <= 0x40]
    # v3 truthful-echo check: per-board GET_INFO report index at state+0x302.
    # Seeded to 0x1e (boot 480); the v3 stub overwrites it with the sent index.
    report_idx = {}
    for b in range(3):
        st = STATE_BASE + b*STRIDE
        try:
            report_idx[b] = uc.mem_read(st+0x302, 1)[0]
        except Exception:
            report_idx[b] = None
    return dict(code=code, entry=entry, tag=tag, mode=mode, freq=freq,
                calls=calls, reached_tail=reached_end, fault=fault, perboard=perboard,
                spi_by_board=spi_by_board, sender_events=sender_events,
                frame_events=frame_events,
                setup_events=setup_events, restore_events=restore_events,
                min_sp=min_sp[0], sp_end=sp_end, cutoff_writes=cutoff_writes, tim2=tim2,
                report_idx=report_idx, runs=runs,
                forced_sender_result=force_sender_result)

def main():
    img_path = sys.argv[1]
    trace = '--trace' in sys.argv
    start = STOCK_SET_OPTION
    if '--from-handler' in sys.argv:
        start = int(sys.argv[sys.argv.index('--from-handler')+1], 0)
    if '--mhz' in sys.argv:
        code = index_code(int(sys.argv[sys.argv.index('--mhz')+1]))
    else:
        code = int(sys.argv[2], 0)
    r = run_emulation(img_path, code, start, trace)
    print("=== smoke test: %s  code=%d (mode=0x%02x freq=0x%04X) ===" % (img_path.split('/')[-1], r['code'], r['mode'], r['freq']))
    print("expected clock-table entry at index %d = %s" % (r['code'], r['entry'].hex(' ')))
    print("calls: board_sender=%d frame_builder=%d spi_tx=%d" % (r['calls']['sender'], r['calls']['frame_builder'], r['calls']['spi_tx']))
    print("reached stock-handler tail-jump:", r['reached_tail'], ("(FAULT: %s)" % r['fault']) if r['fault'] else "")
    print("SPI DR writes per board:")
    if not r['perboard']: print("   (none)")
    for name, vals in r['perboard'].items():
        print("   %s: %d words: %s" % (name, len(vals), ' '.join('0x%04X'%v for v in vals)))
    return 0 if (r['reached_tail'] and not r['fault']) else 1

if __name__ == '__main__':
    sys.exit(main())
