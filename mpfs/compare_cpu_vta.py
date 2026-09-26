"""Same ResNet-18 conv layers, same board, same TVM stack: riscv64 CPU vs VTA.

The CPU path uses all four U54 cores (measured: 4.09 cores, 4 threads) with topi's
spatial-pack schedule; VTA uses its packed schedule. NEITHER is autotuned - tophub has no
entries for this board, so both run on fallback schedules and TVM says so for each.
"""
import os, re, subprocess, sys

LAYERS = ["C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"]
OPS = {}
for name, h, ci, co, k, st in [("C2",56,64,64,3,1), ("C3",56,64,128,3,2), ("C4",56,64,128,1,2),
                               ("C5",28,128,128,3,1), ("C6",28,128,256,3,2), ("C7",28,128,256,1,2),
                               ("C8",14,256,256,3,1), ("C9",14,256,512,3,2), ("C10",14,256,512,1,2),
                               ("C11",7,512,512,3,1)]:
    o = (h + 2 * (k // 2) - k) // st + 1
    OPS[name] = 2 * o * o * k * k * ci * co


def run(layer, dev, samples):
    env = {**os.environ, "CONV_ONLY": layer, "CONV_DEV": dev, "CONV_SAMPLES": str(samples)}
    r = subprocess.run(["./run_hw.sh", "conv_probe.py"], capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    m = re.search(r"CONV2D TEST (PASSED|FAILED): Time cost = ([0-9.e-]+) sec/op", out)
    if not m:
        return None, None
    return m.group(1), float(m.group(2))


print(f"{'layer':<5} {'MOP':>7} | {'CPU ms':>9} {'CPU GOPS':>8} | {'VTA ms':>8} {'VTA GOPS':>8} | {'speedup':>7}")
tc = tv = to = 0.0
for L in LAYERS:
    sc, c = run(L, "cpu", 3)
    sv, v = run(L, "vta", 4)
    if c is None or v is None:
        print(f"{L:<5} {'':>7} | {'FAILED' if c is None else 'ok':>9} {'':>8} | "
              f"{'FAILED' if v is None else 'ok':>8}")
        continue
    n = OPS[L]; tc += c; tv += v; to += n
    flag = "" if sc == "PASSED" and sv == "PASSED" else "  <- CORRECTNESS FAIL"
    print(f"{L:<5} {n/1e6:>7.1f} | {c*1e3:>9.1f} {n/c/1e9:>8.2f} | {v*1e3:>8.2f} {n/v/1e9:>8.2f} | "
          f"{c/v:>6.0f}x{flag}")
print(f"\ntotal  {to/1e6:.0f} MOP | CPU {tc*1e3:.0f} ms ({to/tc/1e9:.2f} GOPS, {1/tc:.2f} inf/s) "
      f"| VTA {tv*1e3:.1f} ms ({to/tv/1e9:.2f} GOPS, {1/tv:.1f} inf/s) | {tc/tv:.0f}x")
