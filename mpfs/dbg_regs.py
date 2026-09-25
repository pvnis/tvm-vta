"""Decode VTA's user counters: acc writes plus the operand debug taps in EventCounters.

The registers sit after everything the driver uses, and are latched when a program finishes
and cleared when the next one launches - so they describe the LAST program run.

    0x24  acc_wr        accumulator writes
    0x28  vme_inp cnt   DRAM read beats delivered to LOAD for inp     (VME rd 2)
    0x2c  vme_inp or    OR of every 32-bit word of every beat
    0x30  vme_inp first lowest 32-bit word of the first beat (scratchpads: of the LAST read)
    0x34..0x3c          same for vme_wgt                               (VME rd 3)
    0x40..0x48          same for spad_inp: scratchpad reads handed to the GEMM
    0x4c..0x54          same for spad_wgt
    0x58..0x6c  inp LOAD: starts, first start's ysize<<16|xsize and dram_offset (elements),
                          VME commands, first command's byte address and AXI len (beats-1)
    0x70..0x84  same for wgt (the control: wgt loads correctly on the board)

An "or" of zero means that stream never carried a single nonzero bit.

usage:
    dbg_regs.py board                 read the board over ssh with devmem2
    ... 2>&1 | dbg_regs.py tsim       decode the VTA_DBG line(s) TSIM prints with VTA_DUMP_DBG=1
"""
import re
import subprocess
import sys

BASE = 0x60020000
NAMES = {0x24: "acc_wr"}
for base, stream in ((0x28, "vme_inp"), (0x34, "vme_wgt"), (0x40, "spad_inp"), (0x4c, "spad_wgt")):
    for k, field in enumerate(("cnt", "or", "last" if stream.startswith("spad") else "first")):
        NAMES[base + 4 * k] = f"{stream}.{field}"
# Second set: what each LOAD tensor load was told (first start) and asked VME for (first cmd).
for base, t in ((0x58, "inp"), (0x70, "wgt")):
    for k, field in enumerate(("start.cnt", "start.ysize|xsize", "start.dram_offset",
                               "cmd.cnt", "cmd.addr", "cmd.len")):
        NAMES[base + 4 * k] = f"{t}.{field}"

# Third set: raw instruction traces on both sides of LOAD's instruction queue.
for base, side in ((0x88, "ld_enq"), (0xbc, "ld_deq")):
    NAMES[base] = f"{side}.cnt"
    for k in range(3):
        for w in range(4):
            NAMES[base + 4 + 16 * k + 4 * w] = f"{side}[{k}].w{w}"

NAMES[0xec] = "fetch.co_enq.cnt"
NAMES[0xf0] = "fetch.st_enq.cnt"

MEM = {0: "uop", 1: "wgt", 2: "inp", 3: "acc", 4: "out"}
OPS = {0: "LOAD", 1: "STORE", 2: "GEMM", 3: "FINISH", 4: "ALU"}


def decode_inst(v):
    f = lambda lo, n: (v >> lo) & ((1 << n) - 1)
    op = f(0, 3)
    s = f"{OPS.get(op, op)}"
    if op in (0, 1):
        s += (f" {MEM.get(f(7, 3), f(7, 3))} sram={f(10, 16)} dram={f(26, 32)} "
              f"xsize={f(80, 16)} ysize={f(64, 16)} xstride={f(96, 16)}")
    s += f" deps(pop_prev={f(3,1)} pop_next={f(4,1)} push_prev={f(5,1)} push_next={f(6,1)})"
    return s


def show_traces(regs):
    for base, side, what in ((0x88, "ld_enq", "Fetch -> LOAD queue"),
                             (0xbc, "ld_deq", "LOAD dequeued")):
        n = regs.get(base)
        if n is None:
            continue
        print(f"[dbg] {what}: {n} instruction(s); first {min(n, 3)}:")
        for k in range(min(n, 3)):
            v = sum(regs.get(base + 4 + 16 * k + 4 * w, 0) << (32 * w) for w in range(4))
            print(f"[dbg]   [{k}] {v:032x}  {decode_inst(v)}")


def read_board(host="root@192.168.100.2"):
    cmd = " ".join(f"devmem2 0x{BASE + off:08x} w | tail -1;" for off in sorted(NAMES))
    out = subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", host, cmd],
                         capture_output=True, text=True, check=True).stdout
    vals = [int(m, 16) for m in re.findall(r":\s*(0x[0-9A-Fa-f]+)\s*$", out, re.M)]
    return dict(zip(sorted(NAMES), vals))


def read_tsim(text):
    lines = [ln for ln in text.splitlines() if ln.startswith("VTA_DBG")]
    if not lines:
        raise SystemExit("no VTA_DBG line - run with VTA_DUMP_DBG=1 and rebuilt libvta_tsim.so")
    # The last line belongs to the last program run.
    return {int(a, 16): int(b, 16) for a, b in re.findall(r"0x([0-9a-f]+)=0x([0-9a-f]+)", lines[-1])}


def show(regs, label):
    print(f"[dbg] {label}")
    for off in sorted(o for o in NAMES if o < 0x88 or o >= 0xec):
        v = regs.get(off)
        note = ""
        if NAMES[off].endswith(".or") and v == 0:
            note = "   <- no nonzero bit ever"
        print(f"[dbg]   0x{off:02x} {NAMES[off]:<16} {'n/a' if v is None else f'0x{v:08x} ({v})'}{note}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "board"
    if src == "board":
        r = read_board()
        show(r, "board")
    else:
        r = read_tsim(sys.stdin.read())
        show(r, "tsim")
    show_traces(r)
