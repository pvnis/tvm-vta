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
    for off in sorted(NAMES):
        v = regs.get(off)
        note = ""
        if NAMES[off].endswith(".or") and v == 0:
            note = "   <- no nonzero bit ever"
        print(f"[dbg]   0x{off:02x} {NAMES[off]:<16} {'n/a' if v is None else f'0x{v:08x} ({v})'}{note}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "board"
    if src == "board":
        show(read_board(), "board")
    else:
        show(read_tsim(sys.stdin.read()), "tsim")
