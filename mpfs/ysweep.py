"""Sweep the GEMM batch-tile count, which is what sets ysize on the inp DMA transfer.

The inp operand is staged with compute_at inside the reduction loop, so the load of x_buf
walks the batch axis with a stride: the DMA descriptor gets ysize = o, xsize = 1,
xstride = red. So o is a direct handle on the multi-line (ysize > 1) path of
GenVMECmdWide - the path the PolarFire re-timing changed, and the only part of that
re-timing whose equivalence argument does not obviously hold.

Prediction if the re-timing is the fault: o = 1 correct (single line, start path only),
failures appearing as o grows. Run this against tsim with the current RTL and again with
the re-timing reverted; the difference is the answer, and it needs no board.

Every run pre-fills a sentinel, so "never executed" is never mistaken for "computed wrong".

usage: ysweep.py [max_o] [red]
"""
import os
import sys

import numpy as np
import tvm
from tvm import te, rpc
from tvm.contrib import utils
import vta

HOST = os.environ.get("VTA_RPC_HOST", "192.168.100.2")
PORT = int(os.environ.get("VTA_RPC_PORT", "9091"))
CC = os.environ.get("VTA_CROSS_CC", "/home/dmd/polarfire_sandbox/vta/tools/rvcc/rv64gc-gcc")
env = vta.get_env()

MAX_O = int(sys.argv[1]) if len(sys.argv) > 1 else 6
RED = int(sys.argv[2]) if len(sys.argv) > 2 else 1
SENTINEL = 77

if env.TARGET in ("sim", "tsim"):
    from vta.testing import simulator                       # noqa: F401
    remote = rpc.LocalSession()
else:
    remote = rpc.connect(HOST, PORT)
dev = remote.ext_dev(0)
temp = utils.tempdir()


def build(o, m, red):
    x = te.placeholder((o, red, env.BATCH, env.BLOCK_IN), name="x", dtype=env.inp_dtype)
    w = te.placeholder((m, red, env.BLOCK_OUT, env.BLOCK_IN), name="w", dtype=env.wgt_dtype)
    x_buf = te.compute(x.shape, lambda *i: x(*i), "x_buf")
    w_buf = te.compute(w.shape, lambda *i: w(*i), "w_buf")
    ko = te.reduce_axis((0, red), name="ko")
    ki = te.reduce_axis((0, env.BLOCK_IN), name="ki")
    y_gem = te.compute(
        (o, m, env.BATCH, env.BLOCK_OUT),
        lambda bo, co, bi, ci: te.sum(
            x_buf[bo, ko, bi, ki].astype(env.acc_dtype)
            * w_buf[co, ko, ci, ki].astype(env.acc_dtype), axis=[ko, ki]),
        name="y_gem")
    y = te.compute(y_gem.shape, lambda *i: y_gem(*i).astype(env.inp_dtype), "y")
    s = te.create_schedule(y.op)
    s[x_buf].set_scope(env.inp_scope)
    s[w_buf].set_scope(env.wgt_scope)
    s[y_gem].set_scope(env.acc_scope)
    s[x_buf].compute_at(s[y_gem], ko); s[x_buf].pragma(s[x_buf].op.axis[0], env.dma_copy)
    s[w_buf].compute_at(s[y_gem], ko); s[w_buf].pragma(s[w_buf].op.axis[0], env.dma_copy)
    s[y].pragma(s[y].op.axis[0], env.dma_copy)
    bo, co, bi, ci = s[y_gem].op.axis
    s[y_gem].reorder(ko, bo, co, bi, ci, ki)
    s[y_gem].tensorize(bi, env.gemm)
    with vta.build_config():
        return vta.build(s, [x, w, y], tvm.target.Target("ext_dev", host=env.target_host)), y.dtype


print(f"[ysweep] TARGET={env.TARGET} red={RED}; o sets ysize on the inp transfer")
print(f"[ysweep] {'o':>3} {'ysize':>5}  {'correct':>9}  {'tiles ok':>8}   note")
for o in range(1, MAX_O + 1):
    m = 2
    mod, dt = build(o, m, RED)
    path = temp.relpath(f"ysweep{o}.so")
    mod.export_library(path, **({"cc": CC} if env.TARGET == "mpfs" else {}))
    remote.upload(path)
    f = remote.load_module(f"ysweep{o}.so")

    x_np = np.zeros((o, RED, env.BATCH, env.BLOCK_IN), dtype=env.inp_dtype)
    for b in range(o):
        for j in range(RED):
            # A distinct value per batch tile, so a tile fed from the WRONG DRAM line is
            # recognisable rather than merely wrong.
            x_np[b, j, 0, :] = (b + 1)
    w_np = np.zeros((m, RED, env.BLOCK_OUT, env.BLOCK_IN), dtype=env.wgt_dtype)
    for i in range(m):
        for j in range(RED):
            for c in range(env.BLOCK_OUT):
                w_np[i, j, c, c] = 1

    ref = np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype="int32")
    for b in range(o):
        for i in range(m):
            for j in range(RED):
                ref[b, i, :] += np.dot(x_np[b, j, :].astype("int32"),
                                       w_np[i, j].T.astype("int32"))
    ref = ref.astype(dt)

    out = tvm.nd.array(np.full(ref.shape, SENTINEL, dtype=dt), dev)
    try:
        f(tvm.nd.array(x_np, dev), tvm.nd.array(w_np, dev), out)
        got = out.numpy()
    except Exception as e:                                  # noqa: BLE001
        print(f"[ysweep] {o:>3} {o:>5}  {'EXC':>9}  {'':>8}   "
              f"{str(e).strip().splitlines()[-1][:70]}")
        continue
    if np.array_equal(got, np.full(ref.shape, SENTINEL, dtype=dt)):
        print(f"[ysweep] {o:>3} {o:>5}  {'NOTRUN':>9}  {'':>8}   sentinel intact")
        continue
    ntile = sum(1 for b in range(o) for i in range(m)
                if np.array_equal(got[b, i, 0], ref[b, i, 0]))
    # Each batch tile should read back its own value b+1; report what each actually got,
    # which names the DRAM line the tile was actually fed from.
    seen = [int(got[b, 0, 0, 0]) for b in range(o)]
    note = "ok" if ntile == o * m else f"tile0 per batch = {seen} (expect {list(range(1, o+1))})"
    print(f"[ysweep] {o:>3} {o:>5}  {int((got==ref).sum()):>4}/{got.size:<4}  "
          f"{ntile:>3}/{o*m:<4}   {note}")
