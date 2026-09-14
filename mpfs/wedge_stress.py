"""Characterise the VTA wedging: how many program executions until it stops completing?

Compiles and uploads ONE module, then runs it repeatedly over a single RPC connection, so
what varies is the number of accelerator executions and nothing else. Each earlier
observation of the wedge was confounded by a fresh RPC connection (and so a fresh forked
server process and a fresh device mapping) per run.

Reports the first iteration whose result is wrong or which fails to complete, and re-checks
the device afterwards to see whether the wedge is sticky.

usage: wedge_stress.py [iterations] [alu|gemm]
"""
import os, sys, time
import numpy as np
import tvm
from tvm import te, topi, rpc
from tvm.contrib import utils
import vta

HOST = os.environ.get("VTA_RPC_HOST", "192.168.100.2")
PORT = int(os.environ.get("VTA_RPC_PORT", "9091"))
CC = os.environ.get("VTA_CROSS_CC", "/home/dmd/polarfire_sandbox/vta/tools/rvcc/rv64gc-gcc")
env = vta.get_env()

iters = int(sys.argv[1]) if len(sys.argv) > 1 else 100
kind = sys.argv[2] if len(sys.argv) > 2 else "alu"
SHIFT = 1


def build_alu():
    """ACC load -> ALU shift -> OUT -> store: uses the COMPUTE module only."""
    n = m = 6
    x = te.placeholder((n, m, env.BATCH, env.BLOCK_OUT), name="x", dtype=env.acc_dtype)
    x_buf = te.compute((n, m, env.BATCH, env.BLOCK_OUT), lambda *i: x(*i), "x_buf")
    y_buf = te.compute((n, m, env.BATCH, env.BLOCK_OUT), lambda *i: x_buf(*i) >> SHIFT, "y_buf")
    y = te.compute((n, m, env.BATCH, env.BLOCK_OUT), lambda *i: y_buf(*i).astype(env.inp_dtype), "y")
    s = te.create_schedule(y.op)
    s[x_buf].set_scope(env.acc_scope); s[x_buf].pragma(x_buf.op.axis[0], env.dma_copy)
    s[y_buf].set_scope(env.acc_scope); s[y_buf].pragma(y_buf.op.axis[0], env.alu)
    s[y].pragma(y.op.axis[0], env.dma_copy)
    with vta.build_config():
        mod = vta.build(s, [x, y], tvm.target.Target("ext_dev", host=env.target_host))
    rng = np.random.default_rng(1)
    x_np = rng.integers(1, 100, size=(n, m, env.BATCH, env.BLOCK_OUT)).astype(x.dtype)
    exp = (x_np >> SHIFT).astype(y.dtype)
    return mod, [x_np], exp, y.dtype


mod, inputs, exp, out_dtype = build_alu()
remote = rpc.connect(HOST, PORT)
temp = utils.tempdir()
path = temp.relpath("stress.so")
mod.export_library(path, cc=CC)
remote.upload(path)
f = remote.load_module("stress.so")
dev = remote.ext_dev(0)
print(f"[stress] {iters} iterations of '{kind}' over one connection, TARGET={env.TARGET}")

# Upload the operands once; only the execution repeats.
in_nd = [tvm.nd.array(a, dev) for a in inputs]
out_nd = tvm.nd.array(np.zeros(exp.shape, dtype=out_dtype), dev)

first_bad = None
t0 = time.time()
base_x = inputs[0]
for i in range(1, iters + 1):
    # Vary the input every iteration. With an identical program each time, the output buffer
    # still holds the PREVIOUS run's (correct) result, so a run that never executed is
    # indistinguishable from one that succeeded - which is exactly how an earlier version of
    # this test produced the false conclusion that the compute finished and only the
    # completion handshake was lost.
    # STATIC=1 reproduces the original harness: identical input every iteration and no
    # buffer rewrites between runs, which is a separate variable from the driver changes.
    if os.environ.get("STATIC"):
        x_i = base_x
        exp = (x_i >> SHIFT).astype(out_dtype)
    else:
        x_i = (base_x + (i % 7)).astype(base_x.dtype)
        exp = (x_i >> SHIFT).astype(out_dtype)
        in_nd[0].copyfrom(x_i)
        out_nd.copyfrom(np.full(exp.shape, 123, dtype=out_dtype))  # sentinel
    try:
        f(*in_nd, out_nd)
        got = out_nd.numpy()
    except Exception as e:                       # noqa: BLE001 - want any failure, incl. RPC
        print(f"[stress] iteration {i}: EXCEPTION after {time.time()-t0:.1f}s")
        print("   " + str(e).strip().splitlines()[-1][:200])
        first_bad = i
        # Did the program actually run? The output was pre-filled with a sentinel and the
        # input differs from the previous iteration, so this distinguishes a run that
        # executed from one that never started - which reading a stale buffer cannot.
        try:
            got = out_nd.numpy()
            n_ok = int((got == exp).sum())
            n_sentinel = int((got == 123).sum())
            print(f"[stress] output after the failed run: {n_ok}/{got.size} correct,"
                  f" {n_sentinel} still sentinel"
                  f" -> {'the run DID execute' if n_ok == got.size else 'the run never executed'}")
        except Exception as e2:                  # noqa: BLE001
            print(f"[stress] could not read output back: {str(e2).splitlines()[-1][:120]}")
        break
    if not np.array_equal(got, exp):
        print(f"[stress] iteration {i}: WRONG RESULT ({(got == exp).sum()}/{got.size} correct)")
        first_bad = i
        break
    if i % 25 == 0:
        print(f"[stress]   {i} ok ({time.time()-t0:.1f}s)")

if first_bad is None:
    print(f"[stress] all {iters} iterations correct in {time.time()-t0:.1f}s - no wedge")
else:
    print(f"[stress] first failure at iteration {first_bad}")
    # Is the wedge sticky? Try once more on the same connection.
    try:
        f(*in_nd, out_nd)
        ok = np.array_equal(out_nd.numpy(), exp)
        print(f"[stress] retry on same connection: {'RECOVERED' if ok else 'still wrong'}")
    except Exception as e:                       # noqa: BLE001
        print(f"[stress] retry on same connection: still failing ({str(e).strip().splitlines()[-1][:120]})")
sys.exit(0 if first_bad is None else 1)
