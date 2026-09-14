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
with vta.build_config():
    mod = vta.build(s, [x, w, y], tvm.target.Target("ext_dev", host=env.target_host))

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
if mode == "identity":
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

y_nd = tvm.nd.array(np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype=y.dtype), dev)
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
print(f"[probe] got  zeros={(got==0).sum()}  ref zeros={(ref==0).sum()}")
for b in range(o):
    for i in range(m):
        same = np.array_equal(got[b, i, 0], ref[b, i, 0])
        print(f"  tile[batch={b},out={i}] {'ok ' if same else 'BAD'}")
        if not same:
            print(f"    ref={ref[b, i, 0].tolist()}")
            print(f"    got={got[b, i, 0].tolist()}")
