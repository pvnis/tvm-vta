"""One program that exercises the working path and the broken path together.

Computes y = x . w^T + bias, where:
  - x (inp) and w (wgt) are loaded by the LOAD module and consumed by the MAC array
  - bias is loaded into the ACC scratchpad by the COMPUTE module, the same mechanism the
    ALU tests use, and added on top with an ALU op

Every previous comparison was across DIFFERENT programs (an ALU program that works versus a
GEMM program that does not), which leaves open that the difference is the program rather
than the datapath. Here both happen in one instruction stream, so the reading is direct:

    result == bias exactly     -> the ACC load and the ALU and the store all work, and the
                                  GEMM contributed nothing: the fault is in inp/wgt loading
                                  or in the MAC array
    result == bias + product   -> everything works
    result == product only     -> the bias (ACC) load is what is broken
    anything else              -> neither, and the pattern says what

usage: bias_probe.py [red]
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

red = int(sys.argv[1]) if len(sys.argv) > 1 else 1
o, m = 2, 2

x = te.placeholder((o, red, env.BATCH, env.BLOCK_IN), name="x", dtype=env.inp_dtype)
w = te.placeholder((m, red, env.BLOCK_OUT, env.BLOCK_IN), name="w", dtype=env.wgt_dtype)
b = te.placeholder((o, m, env.BATCH, env.BLOCK_OUT), name="b", dtype=env.acc_dtype)

x_buf = te.compute((o, red, env.BATCH, env.BLOCK_IN), lambda *i: x(*i), "x_buf")
w_buf = te.compute((m, red, env.BLOCK_OUT, env.BLOCK_IN), lambda *i: w(*i), "w_buf")
b_buf = te.compute((o, m, env.BATCH, env.BLOCK_OUT), lambda *i: b(*i), "b_buf")

ko = te.reduce_axis((0, red), name="ko")
ki = te.reduce_axis((0, env.BLOCK_IN), name="ki")
y_gem = te.compute(
    (o, m, env.BATCH, env.BLOCK_OUT),
    lambda bo, co, bi, ci: te.sum(
        x_buf[bo, ko, bi, ki].astype(env.acc_dtype) * w_buf[co, ko, ci, ki].astype(env.acc_dtype),
        axis=[ko, ki]),
    name="y_gem")
y_add = te.compute((o, m, env.BATCH, env.BLOCK_OUT),
                   lambda *i: y_gem(*i) + b_buf(*i), name="y_add")
y = te.compute((o, m, env.BATCH, env.BLOCK_OUT),
               lambda *i: y_add(*i).astype(env.inp_dtype), name="y")

s = te.create_schedule(y.op)
s[x_buf].set_scope(env.inp_scope)
s[w_buf].set_scope(env.wgt_scope)
s[b_buf].set_scope(env.acc_scope)
s[y_gem].set_scope(env.acc_scope)
s[y_add].set_scope(env.acc_scope)
s[x_buf].compute_at(s[y_gem], ko); s[x_buf].pragma(s[x_buf].op.axis[0], env.dma_copy)
s[w_buf].compute_at(s[y_gem], ko); s[w_buf].pragma(s[w_buf].op.axis[0], env.dma_copy)
s[b_buf].pragma(s[b_buf].op.axis[0], env.dma_copy)
s[y_add].pragma(s[y_add].op.axis[0], env.alu)
s[y].pragma(s[y].op.axis[0], env.dma_copy)
bo, co, bi, ci = s[y_gem].op.axis
s[y_gem].reorder(ko, bo, co, bi, ci, ki)
s[y_gem].tensorize(bi, env.gemm)

with vta.build_config():
    mod = vta.build(s, [x, w, b, y], tvm.target.Target("ext_dev", host=env.target_host))

if env.TARGET in ("sim", "tsim"):
    from vta.testing import simulator
    remote = rpc.LocalSession()
else:
    remote = rpc.connect(HOST, PORT)
temp = utils.tempdir()
path = temp.relpath("bias_probe.so")
mod.export_library(path, **({"cc": CC} if env.TARGET == "mpfs" else {}))
remote.upload(path)
f = remote.load_module("bias_probe.so")
dev = remote.ext_dev(0)

# x is 1..BLOCK_IN, w is one-hot, so the product is just x again per output channel.
# The bias is a distinct, easily recognised constant per output channel.
x_np = np.zeros((o, red, env.BATCH, env.BLOCK_IN), dtype=x.dtype)
for bb in range(o):
    for j in range(red):
        x_np[bb, j, 0, :] = np.arange(1, env.BLOCK_IN + 1, dtype=x.dtype)
w_np = np.zeros((m, red, env.BLOCK_OUT, env.BLOCK_IN), dtype=w.dtype)
for i in range(m):
    for j in range(red):
        for c in range(env.BLOCK_OUT):
            w_np[i, j, c, c] = 1
b_np = np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype=b.dtype)
for bb in range(o):
    for i in range(m):
        b_np[bb, i, 0, :] = 100                      # flat, unmistakable

SENTINEL = 77
y_nd = tvm.nd.array(np.full((o, m, env.BATCH, env.BLOCK_OUT), SENTINEL, dtype=y.dtype), dev)
f(tvm.nd.array(x_np, dev), tvm.nd.array(w_np, dev), tvm.nd.array(b_np, dev), y_nd)
got = y_nd.numpy()

product = np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype="int32")
for bb in range(o):
    for i in range(m):
        for j in range(red):
            product[bb, i, :] += np.dot(x_np[bb, j, :].astype("int32"),
                                        w_np[i, j].T.astype("int32"))
full = (product + b_np.astype("int32")).astype(y.dtype)
bias_only = b_np.astype(y.dtype)
prod_only = product.astype(y.dtype)

print(f"[bias] TARGET={env.TARGET} red={red}")
print(f"[bias]   == bias + product (all correct) : {np.array_equal(got, full)}")
print(f"[bias]   == bias only (GEMM contributed nothing) : {np.array_equal(got, bias_only)}")
print(f"[bias]   == product only (bias load broken)      : {np.array_equal(got, prod_only)}")
print(f"[bias]   still sentinel (never stored)           : {int((got == SENTINEL).sum())}/{got.size}")
print(f"[bias] tile[0,0] expected {full[0,0,0].tolist()}")
print(f"[bias] tile[0,0] got      {got[0,0,0].tolist()}")
