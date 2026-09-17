"""Run a matrix of VTA programs, classifying every execution three ways.

The reason this exists: a wedged VTA (one that has stopped executing programs) looks
exactly like a broken computation if you only compare the output against a reference.
Today that cost a whole chain of wrong conclusions - "GEMM contributes exactly zero",
"one-hot weights pass but general weights fail", "the MAC array pairing is broken" -
all measured after the device had silently stopped running anything.

So every run here pre-fills the output with a sentinel and varies the input, giving
three distinguishable outcomes rather than pass/fail:

    NOTRUN  output still holds the sentinel      -> VTA never wrote anything
    WRONG   output changed but differs from ref  -> VTA ran and computed wrongly
    ok      output matches the reference

Everything is built and uploaded once and run from a single process over one RPC
connection, so what varies between runs is only the number of executions. The report
says which program was running when execution first stopped.

usage: matrix.py [reps]
"""
import os
import sys
import time

import numpy as np
import tvm
from tvm import te, rpc
from tvm.contrib import utils
import vta

HOST = os.environ.get("VTA_RPC_HOST", "192.168.100.2")
PORT = int(os.environ.get("VTA_RPC_PORT", "9091"))
CC = os.environ.get("VTA_CROSS_CC", "/home/dmd/polarfire_sandbox/vta/tools/rvcc/rv64gc-gcc")
env = vta.get_env()

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
# VTA_DEBUG is a runtime debug flag compiled into the module. 32 = VTA_DEBUG_FORCE_SERIAL,
# which rewrites the instruction dependencies so LOAD, COMPUTE and STORE never overlap.
# GEMM is the only program here that makes LOAD (inp/wgt) run concurrently with COMPUTE,
# so this separates a synchronisation fault from an arithmetic one.
DEBUG_FLAG = int(os.environ.get("VTA_DEBUG", "0"), 0)
SENTINEL = 77
SHIFT = 1


def build_alu(n=6, m=6):
    """ACC load -> ALU shift -> store. Touches the COMPUTE and STORE modules only."""
    x = te.placeholder((n, m, env.BATCH, env.BLOCK_OUT), name="x", dtype=env.acc_dtype)
    x_buf = te.compute(x.shape, lambda *i: x(*i), "x_buf")
    y_buf = te.compute(x.shape, lambda *i: x_buf(*i) >> SHIFT, "y_buf")
    y = te.compute(x.shape, lambda *i: y_buf(*i).astype(env.inp_dtype), "y")
    s = te.create_schedule(y.op)
    s[x_buf].set_scope(env.acc_scope); s[x_buf].pragma(x_buf.op.axis[0], env.dma_copy)
    s[y_buf].set_scope(env.acc_scope); s[y_buf].pragma(y_buf.op.axis[0], env.alu)
    s[y].pragma(y.op.axis[0], env.dma_copy)
    with vta.build_config(debug_flag=DEBUG_FLAG):
        mod = vta.build(s, [x, y], tvm.target.Target("ext_dev", host=env.target_host))
    return mod, y.dtype


def build_gemm(o, m, red):
    """inp/wgt load -> MAC array -> store. The only program that uses the LOAD module."""
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
    with vta.build_config(debug_flag=DEBUG_FLAG):
        mod = vta.build(s, [x, w, y], tvm.target.Target("ext_dev", host=env.target_host))
    return mod, y.dtype


def gemm_ref(x_np, w_np, dtype):
    o, red = x_np.shape[0], x_np.shape[1]
    m = w_np.shape[0]
    ref = np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype="int32")
    for b in range(o):
        for i in range(m):
            for j in range(red):
                ref[b, i, :] += np.dot(x_np[b, j, :].astype("int32"),
                                       w_np[i, j].T.astype("int32"))
    return ref.astype(dtype)


def onehot_w(m, red, dtype):
    w_np = np.zeros((m, red, env.BLOCK_OUT, env.BLOCK_IN), dtype=dtype)
    for i in range(m):
        for j in range(red):
            for c in range(env.BLOCK_OUT):
                w_np[i, j, c, c] = 1
    return w_np


if env.TARGET in ("sim", "tsim"):
    # Same programs against the simulators, so a failure seen on the board can be checked
    # against the very same RTL in Verilator without the board in the loop.
    from vta.testing import simulator            # noqa: F401  (starts the TSIM thread)
    remote = rpc.LocalSession()
else:
    remote = rpc.connect(HOST, PORT)
dev = remote.ext_dev(0)
temp = utils.tempdir()


def upload(mod, name):
    path = temp.relpath(name + ".so")
    mod.export_library(path, **({"cc": CC} if env.TARGET == "mpfs" else {}))
    remote.upload(path)
    return remote.load_module(name + ".so")


class Case:
    """One program plus a way to make per-iteration operands and a reference."""

    def __init__(self, name, mod, dtype, make):
        self.name = name
        self.f = upload(mod, name)
        self.dtype = dtype
        self.make = make            # iteration -> (list of input arrays, reference array)

    def run(self, i):
        ins, ref = self.make(i)
        in_nd = [tvm.nd.array(a, dev) for a in ins]
        out_nd = tvm.nd.array(np.full(ref.shape, SENTINEL, dtype=self.dtype), dev)
        try:
            self.f(*in_nd, out_nd)
            got = out_nd.numpy()
        except Exception as e:                                  # noqa: BLE001
            return "EXC", str(e).strip().splitlines()[-1][:90]
        if np.array_equal(got, np.full(ref.shape, SENTINEL, dtype=self.dtype)):
            return "NOTRUN", ""
        if np.array_equal(got, ref):
            return "ok", ""
        n = int((got == ref).sum())
        return "WRONG", f"{n}/{got.size} correct"


rng = np.random.default_rng(2)
cases = []

mod, dt = build_alu()
base = rng.integers(1, 100, size=(6, 6, env.BATCH, env.BLOCK_OUT)).astype(env.acc_dtype)
cases.append(Case("alu", mod, dt,
                  lambda i, b=base, d=dt: ([(b + (i % 7)).astype(b.dtype)],
                                           ((b + (i % 7)) >> SHIFT).astype(d))))

# Ordered deliberately: a small GEMM, then a large one, then the SAME small one again.
# This morning the 2x2 GEMM passed twice on a long-running board, the 4x4 then ran, and
# every GEMM after that failed - including the 2x2 that had just worked. So the question
# is not only "which sizes work" but "does the large one poison the device for the small
# one", which only an ordered sequence in a single process can answer.
for label, o, m in (("gemm-2x2-before", 2, 2), ("gemm-4x4", 4, 4), ("gemm-2x2-after", 2, 2)):
    red = 1
    mod, dt = build_gemm(o, m, red)
    w_np = onehot_w(m, red, env.wgt_dtype)

    def make(i, w_np=w_np, o=o, red=red, dt=dt):
        x_np = np.zeros((o, red, env.BATCH, env.BLOCK_IN), dtype=env.inp_dtype)
        for b in range(o):
            for j in range(red):
                x_np[b, j, 0, :] = ((np.arange(1, env.BLOCK_IN + 1) + i) % 5).astype(env.inp_dtype)
        return [x_np, w_np], gemm_ref(x_np, w_np, dt)

    cases.append(Case(label, mod, dt, make))

print(f"[matrix] {len(cases)} programs x {REPS} reps, one process, TARGET={env.TARGET}"
      f"{'  debug_flag=0x%x' % DEBUG_FLAG if DEBUG_FLAG else ''}")
print(f"[matrix] NOTRUN = output still holds the sentinel, i.e. VTA executed nothing\n")
t0 = time.time()
first_notrun = None
n_exec = 0
for case in cases:
    outcomes = []
    for i in range(REPS):
        verdict, detail = case.run(i)
        n_exec += 1
        outcomes.append(verdict)
        if verdict == "NOTRUN" and first_notrun is None:
            first_notrun = (case.name, i, n_exec)
        if detail and verdict != "ok":
            print(f"[matrix]   {case.name} rep {i}: {verdict} {detail}")
    tally = {v: outcomes.count(v) for v in sorted(set(outcomes))}
    print(f"[matrix] {case.name:<14} {tally}")

print(f"\n[matrix] {n_exec} executions in {time.time()-t0:.1f}s")
if first_notrun:
    name, i, n = first_notrun
    print(f"[matrix] execution first stopped during '{name}' rep {i} "
          f"({n} executions in) - everything after that is a wedge, not a wrong answer")
else:
    print("[matrix] every execution ran - no wedge in this sequence")
