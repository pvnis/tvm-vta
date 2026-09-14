"""Compare VTA's GEMM output against the reference tile by tile, to characterise a failure.

Runs the same schedule as hw_test.py's test_gemm but prints the structure of the
mismatch instead of a pass/fail: which output tiles are right, whether the wrong
values are zero, and whether they match a transposed or shifted reference. Uses
simple int8 operands (a one-hot weight matrix by default) so the expected result is
readable by eye.

usage: gemm_probe.py [identity|random] [red]
"""
import os, sys
import numpy as np
import tvm
from tvm import te, rpc
from tvm.contrib import utils
import vta

HOST = os.environ.get("VTA_RPC_HOST", "192.168.100.2")
PORT = int(os.environ.get("VTA_RPC_PORT", "9091"))
CC = os.environ.get("VTA_CROSS_CC", "/home/dmd/polarfire_sandbox/vta/tools/rvcc/rv64gc-gcc")
env = vta.get_env()

mode = sys.argv[1] if len(sys.argv) > 1 else "identity"
red = int(sys.argv[2]) if len(sys.argv) > 2 else 1
o, m = 2, 2

x = te.placeholder((o, red, env.BATCH, env.BLOCK_IN), name="x", dtype=env.inp_dtype)
w = te.placeholder((m, red, env.BLOCK_OUT, env.BLOCK_IN), name="w", dtype=env.wgt_dtype)
x_buf = te.compute((o, red, env.BATCH, env.BLOCK_IN), lambda *i: x(*i), "x_buf")
w_buf = te.compute((m, red, env.BLOCK_OUT, env.BLOCK_IN), lambda *i: w(*i), "w_buf")
ko = te.reduce_axis((0, red), name="ko")
ki = te.reduce_axis((0, env.BLOCK_IN), name="ki")
y_gem = te.compute(
    (o, m, env.BATCH, env.BLOCK_OUT),
    lambda bo, co, bi, ci: te.sum(
        x_buf[bo, ko, bi, ki].astype(env.acc_dtype) * w_buf[co, ko, ci, ki].astype(env.acc_dtype),
        axis=[ko, ki]),
    name="y_gem")
y = te.compute((o, m, env.BATCH, env.BLOCK_OUT), lambda *i: y_gem(*i).astype(env.inp_dtype), "y")
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
# VTA_DEBUG selects runtime debug flags at build time (they are compiled into the module as
# a VTASetDebugMode call). 32 = VTA_DEBUG_FORCE_SERIAL, which rewrites the instruction
# dependencies so LOAD/COMPUTE/STORE never overlap - GEMM is the only test that makes the
# LOAD module (inp/wgt) run concurrently with COMPUTE (uop/acc), so this tests directly
# whether that concurrency is what breaks it.
DEBUG_FLAG = int(os.environ.get("VTA_DEBUG", "0"), 0)
with vta.build_config(debug_flag=DEBUG_FLAG):
    mod = vta.build(s, [x, w, y], tvm.target.Target("ext_dev", host=env.target_host))
if DEBUG_FLAG:
    print(f"[probe] debug_flag=0x{DEBUG_FLAG:x}")

if env.TARGET in ("sim", "tsim"):
    from vta.testing import simulator
    remote = rpc.LocalSession()
else:
    remote = rpc.connect(HOST, PORT)
temp = utils.tempdir()
path = temp.relpath("gemm_probe.so")
mod.export_library(path, **({"cc": CC} if env.TARGET == "mpfs" else {}))
remote.upload(path)
f = remote.load_module("gemm_probe.so")
dev = remote.ext_dev(0)

rng = np.random.default_rng(3)
if mode == "diag":
    # One program that separates the three permutations the identity test cannot tell
    # apart. Batch tile 0 carries distinct input values, batch tile 1 uniform ones;
    # weight tile 0 has identical rows, weight tile 1 has a distinct value per row. So:
    #   out[batch=0, wgt=0, c]  is blind to a wgt ROW permutation (rows are identical)
    #                           -> all 1s if correct; all 9s if inp (or the wgt k-axis)
    #                              is rotated by 8.
    #   out[batch=1, wgt=1, c]  is blind to an inp permutation (input is uniform)
    #                           -> c+1 if correct; permuted if the wgt ROWS are rotated.
    assert o >= 2 and m >= 2
    x_np = np.zeros((o, red, env.BATCH, env.BLOCK_IN), dtype=x.dtype)
    for j in range(red):
        x_np[0, j, 0, :] = np.arange(1, env.BLOCK_IN + 1, dtype=x.dtype)  # distinct
        x_np[1, j, 0, :] = 1                                              # uniform
    w_np = np.zeros((m, red, env.BLOCK_OUT, env.BLOCK_IN), dtype=w.dtype)
    for j in range(red):
        for c in range(env.BLOCK_OUT):
            w_np[0, j, c, 0] = 1        # every row identical: selects input channel 0
            w_np[1, j, c, 0] = c + 1    # row c scaled by c+1
elif mode == "identity":
    # x holds a distinct small value per input channel; w is one-hot, so each output
    # channel simply selects one input channel. Any permutation/addressing error in the
    # inp or wgt load shows up directly as the wrong channel being picked.
    x_np = np.zeros((o, red, env.BATCH, env.BLOCK_IN), dtype=x.dtype)
    for b in range(o):
        for j in range(red):
            x_np[b, j, 0, :] = np.arange(1, env.BLOCK_IN + 1, dtype=x.dtype)
    w_np = np.zeros((m, red, env.BLOCK_OUT, env.BLOCK_IN), dtype=w.dtype)
    for i in range(m):
        for j in range(red):
            for c in range(env.BLOCK_OUT):
                w_np[i, j, c, c] = 1
else:
    x_np = rng.integers(-2, 2, size=(o, red, env.BATCH, env.BLOCK_IN)).astype(x.dtype)
    w_np = rng.integers(-2, 2, size=(m, red, env.BLOCK_OUT, env.BLOCK_IN)).astype(w.dtype)

# Pre-fill the output with a sentinel rather than zeros: that distinguishes "VTA stored a
# computed zero" from "VTA never stored anything at all", which zeros cannot.
SENTINEL = 77
y_nd = tvm.nd.array(np.full((o, m, env.BATCH, env.BLOCK_OUT), SENTINEL, dtype=y.dtype), dev)
f(tvm.nd.array(x_np, dev), tvm.nd.array(w_np, dev), y_nd)
got = y_nd.numpy()

ref = np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype="int32")
for b in range(o):
    for i in range(m):
        for j in range(red):
            ref[b, i, :] += np.dot(x_np[b, j, :].astype("int32"), w_np[i, j].T.astype("int32"))
ref = ref.astype(y.dtype)

if env.TARGET in ("sim", "tsim"):
    # Cycle count for the identical program, to compare against the board's 0x04 register:
    # if the board is much faster, its DMA reads never happened.
    print(f"[probe] stats={dict(simulator.stats())}")
print(f"[probe] TARGET={env.TARGET} mode={mode} red={red} correct={(got==ref).sum()}/{got.size}")
print(f"[probe] got  zeros={(got==0).sum()}  still_sentinel={(got==SENTINEL).sum()}  ref zeros={(ref==0).sum()}")
for b in range(o):
    for i in range(m):
        same = np.array_equal(got[b, i, 0], ref[b, i, 0])
        print(f"  tile[batch={b},out={i}] {'ok ' if same else 'BAD'}")
        if not same:
            print(f"    ref={ref[b, i, 0].tolist()}")
            print(f"    got={got[b, i, 0].tolist()}")
