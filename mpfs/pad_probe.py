"""Does the GEMM failure depend on the physical address the operands land at?

VTA's burst splitting is a function of the absolute DRAM address, not just the transfer
size:

    rdLen1stMaxTransBytes = maxTrBytes - rdLineClBeginAddr % maxTrBytes

TSIM cannot exercise this the way hardware does - its DPI memory model hands out small,
conveniently aligned addresses, while on the board the operands come from the CMA pool at
real physical addresses. That asymmetry is a candidate explanation for a GEMM that is
correct in simulation at every size and all-zero on hardware at o=4.

So: allocate a dummy buffer of varying size before the operands, shifting where they land,
and run the same o=4 GEMM each time. If correctness tracks the padding, the fault is
address-dependent burst arithmetic rather than anything about the program.

Run with VTA_MPFS_DEBUG=1 on the board to get the driver's allocation trace alongside.

usage: pad_probe.py [o] [red]
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

O = int(sys.argv[1]) if len(sys.argv) > 1 else 4
RED = int(sys.argv[2]) if len(sys.argv) > 2 else 1
M = 2
SENTINEL = 77

if env.TARGET in ("sim", "tsim"):
    from vta.testing import simulator                       # noqa: F401
    remote = rpc.LocalSession()
else:
    remote = rpc.connect(HOST, PORT)
dev = remote.ext_dev(0)
temp = utils.tempdir()

x = te.placeholder((O, RED, env.BATCH, env.BLOCK_IN), name="x", dtype=env.inp_dtype)
w = te.placeholder((M, RED, env.BLOCK_OUT, env.BLOCK_IN), name="w", dtype=env.wgt_dtype)
x_buf = te.compute(x.shape, lambda *i: x(*i), "x_buf")
w_buf = te.compute(w.shape, lambda *i: w(*i), "w_buf")
ko = te.reduce_axis((0, RED), name="ko")
ki = te.reduce_axis((0, env.BLOCK_IN), name="ki")
y_gem = te.compute(
    (O, M, env.BATCH, env.BLOCK_OUT),
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
    mod = vta.build(s, [x, w, y], tvm.target.Target("ext_dev", host=env.target_host))
path = temp.relpath("pad_probe.so")
mod.export_library(path, **({"cc": CC} if env.TARGET == "mpfs" else {}))
remote.upload(path)
f = remote.load_module("pad_probe.so")

x_np = np.zeros((O, RED, env.BATCH, env.BLOCK_IN), dtype=env.inp_dtype)
for b in range(O):
    for j in range(RED):
        x_np[b, j, 0, :] = np.arange(1, env.BLOCK_IN + 1, dtype=env.inp_dtype)
w_np = np.zeros((M, RED, env.BLOCK_OUT, env.BLOCK_IN), dtype=env.wgt_dtype)
for i in range(M):
    for j in range(RED):
        for c in range(env.BLOCK_OUT):
            w_np[i, j, c, c] = 1
ref = np.zeros((O, M, env.BATCH, env.BLOCK_OUT), dtype="int32")
for b in range(O):
    for i in range(M):
        for j in range(RED):
            ref[b, i, :] += np.dot(x_np[b, j, :].astype("int32"), w_np[i, j].T.astype("int32"))
ref = ref.astype(y.dtype)

print(f"[pad] TARGET={env.TARGET} o={O} m={M} red={RED}")
print(f"[pad] {'pad B':>7}  {'correct':>9}   verdict")
for pad in [0, 16, 32, 64, 128, 192, 256, 384, 512, 1024]:
    # Hold a dummy allocation across the run so the operands land at a shifted address.
    keep = tvm.nd.array(np.zeros(pad, dtype="int8"), dev) if pad else None
    x_nd = tvm.nd.array(x_np, dev)
    w_nd = tvm.nd.array(w_np, dev)
    out = tvm.nd.array(np.full(ref.shape, SENTINEL, dtype=y.dtype), dev)
    try:
        f(x_nd, w_nd, out)
        got = out.numpy()
    except Exception as e:                                  # noqa: BLE001
        print(f"[pad] {pad:>7}  {'EXC':>9}   {str(e).strip().splitlines()[-1][:60]}")
        del keep
        continue
    if np.array_equal(got, np.full(ref.shape, SENTINEL, dtype=y.dtype)):
        verdict = "NOTRUN (sentinel intact)"
    elif np.array_equal(got, ref):
        verdict = "ok"
    elif not got.any():
        verdict = "all zeros"
    else:
        verdict = f"partial, e.g. {got[0,0,0][:6].tolist()}"
    print(f"[pad] {pad:>7}  {int((got==ref).sum()):>4}/{got.size:<4}   {verdict}")
    del keep
