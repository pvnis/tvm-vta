"""Per-layer performance: wall time from the test, accelerator busy time from VTA's own
cycle counter (register 0x04, the last launch's cycles), and what the gap implies.

Run after conv_probe.py has run a single layer, so the counter belongs to that layer.
"""
import re, subprocess, sys

WKLS = {  # name: (height, in_filter, out_filter, kernel, stride)
    "C2": (56, 64, 64, 3, 1),   "C3": (56, 64, 128, 3, 2),  "C4": (56, 64, 128, 1, 2),
    "C5": (28, 128, 128, 3, 1), "C6": (28, 128, 256, 3, 2), "C7": (28, 128, 256, 1, 2),
    "C8": (14, 256, 256, 3, 1), "C9": (14, 256, 512, 3, 2), "C10": (14, 256, 512, 1, 2),
    "C11": (7, 512, 512, 3, 1),
}
FREQ = 125e6
PEAK = 16 * 16 * 2 * FREQ      # 256 MACs, 2 ops each


def ops(h, ci, co, k, st):
    o = (h + 2 * (k // 2) - k) // st + 1
    return 2 * o * o * k * k * ci * co


def cycles():
    out = subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", "root@192.168.100.2",
                          "devmem2 0x60020004 w"], capture_output=True, text=True).stdout
    return int(re.findall(r":\s*(0x[0-9A-Fa-f]+)", out)[-1], 16)


print(f"peak = {PEAK/1e9:.0f} GOPS (16x16 MACs, 2 ops, {FREQ/1e6:.0f} MHz)\n")
print(f"{'layer':<5} {'MOP':>7} {'wall ms':>8} {'GOPS':>6} {'busy ms':>8} {'busy GOPS':>10} "
      f"{'MAC util':>9} {'in VTA':>7}")
tot_w = tot_b = tot_o = 0.0
for name, wl in WKLS.items():
    r = subprocess.run(["./run_hw.sh", "conv_probe.py"], capture_output=True, text=True,
                       env={**__import__("os").environ, "CONV_ONLY": name})
    m = re.search(r"Time cost = ([0-9.]+) sec/op", r.stdout + r.stderr)
    if not m:
        print(f"{name:<5} FAILED"); continue
    wall = float(m.group(1))
    busy = cycles() / FREQ
    n = ops(*wl)
    tot_w += wall; tot_b += busy; tot_o += n
    print(f"{name:<5} {n/1e6:>7.1f} {wall*1e3:>8.2f} {n/wall/1e9:>6.2f} {busy*1e3:>8.2f} "
          f"{n/busy/1e9:>10.2f} {100*n/busy/PEAK:>8.0f}% {100*busy/wall:>6.0f}%")
print(f"\ntotal: {tot_o/1e6:.0f} MOP, wall {tot_w*1e3:.1f} ms -> {tot_o/tot_w/1e9:.2f} GOPS")
print(f"       accelerator busy {tot_b*1e3:.1f} ms ({100*tot_b/tot_w:.0f}% of wall), "
      f"{tot_o/tot_b/1e9:.2f} GOPS while busy = {100*tot_o/tot_b/PEAK:.0f}% of peak")
print(f"       a full ResNet-18 pass of these layers would take ~{tot_w*1e3:.0f} ms "
      f"({1/tot_w:.1f} inferences/s) at today's overhead, ~{tot_b*1e3:.0f} ms "
      f"({1/tot_b:.1f}/s) if the per-call cost were amortised")
