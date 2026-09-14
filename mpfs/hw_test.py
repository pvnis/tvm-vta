"""Run VTA workloads on the Discovery Kit over RPC.

Compiles on the host (LLVM, riscv64 target), links the module with the cross toolchain,
uploads it, and executes on the board where our mpfs driver drives the accelerator.

usage: hw_test.py mem                       - device memory round-trip (driver DMA path only)
       hw_test.py alu <shift>               - ACC load -> ALU(>>shift) -> OUT -> store
       hw_test.py pad <shift> <pb_h pb_w pa_h pa_w>
       hw_test.py gemm                      - GEMM: 1x16x16 int8 matmul through the MAC array
"""
import os, sys
import numpy as np
import tvm
from tvm import te, topi, rpc
from tvm.contrib import utils
import vta

HOST = os.environ.get("VTA_RPC_HOST", "192.168.100.2")
PORT = int(os.environ.get("VTA_RPC_PORT", "9091"))
CC = os.environ.get("VTA_CROSS_CC", "/home/dmd/polarfire_sandbox/vta/tools/rvcc/rv64gc-gcc")
env = vta.get_env()


def upload(mod, remote, name):
    temp = utils.tempdir()
    path = temp.relpath(name + ".so")
    # Only the board needs the pinned rv64gc cross compiler; under the simulator the
    # module is for this host, so let TVM pick its default.
    mod.export_library(path, **({"cc": CC} if env.TARGET == "mpfs" else {}))
    remote.upload(path)
    return remote.load_module(name + ".so")


def test_mem(remote):
    """Round-trip data through VTA's DMA pool: exercises VTAMemAlloc/CopyFromHost/ToHost."""
    dev = remote.ext_dev(0)
    ok = True
    for shape in [(64,), (1024,), (16, 16), (64, 64)]:
        a = np.random.randint(-128, 128, size=shape).astype("int8")
        nd = tvm.nd.array(a, dev)
        back = nd.numpy()
        good = np.array_equal(a, back)
        ok &= good
        print(f"  {str(shape):10s} {a.nbytes:7d} bytes  {'ok' if good else 'MISMATCH'}")
    return ok


def build_alu(shift, mode="save", pads=None):
    if mode == "pad":
        n, m = 3, 5
        pb, pa = [pads[0], pads[1], 0, 0], [pads[2], pads[3], 0, 0]
    else:
        n, m, pb, pa = 6, 6, [0, 0, 0, 0], [0, 0, 0, 0]
    H, W = n + pb[0] + pa[0], m + pb[1] + pa[1]
    x = te.placeholder((n, m, env.BATCH, env.BLOCK_OUT), name="x", dtype=env.acc_dtype)
    x_buf = topi.nn.pad(x, pb, pa, name="x_buf") if mode == "pad" else \
        te.compute((n, m, env.BATCH, env.BLOCK_OUT), lambda *i: x(*i), "x_buf")
    y_buf = te.compute((H, W, env.BATCH, env.BLOCK_OUT), lambda *i: x_buf(*i) >> shift, "y_buf")
    y = te.compute((H, W, env.BATCH, env.BLOCK_OUT), lambda *i: y_buf(*i).astype(env.inp_dtype), "y")
    s = te.create_schedule(y.op)
    s[x_buf].set_scope(env.acc_scope); s[x_buf].pragma(x_buf.op.axis[0], env.dma_copy)
    s[y_buf].set_scope(env.acc_scope); s[y_buf].pragma(y_buf.op.axis[0], env.alu)
    s[y].pragma(y.op.axis[0], env.dma_copy)
    with vta.build_config():
        mod = vta.build(s, [x, y], tvm.target.Target("ext_dev", host=env.target_host))
    return mod, (x, y, n, m, pb, pa, H, W)


def test_alu(remote, shift, mode="save", pads=None):
    mod, (x, y, n, m, pb, pa, H, W) = build_alu(shift, mode, pads)
    f = upload(mod, remote, "alu")
    dev = remote.ext_dev(0)
    rng = np.random.default_rng(1)
    x_np = rng.integers(1, 100, size=(n, m, env.BATCH, env.BLOCK_OUT)).astype(x.dtype)
    exp = (np.pad(x_np, list(zip(pb, pa))) >> shift).astype(y.dtype)
    y_nd = tvm.nd.array(np.full(exp.shape, 77, dtype=y.dtype), dev)
    f(tvm.nd.array(x_np, dev), y_nd)
    got = y_nd.numpy()
    good = np.array_equal(got, exp)
    print(f"  mode={mode} shift={shift} pads={pads}: {'PASS' if good else 'FAIL'} "
          f"correct={(got == exp).sum()}/{got.size}")
    return good


def test_gemm(remote, red=1):
    """y = x . w^T on VTA's 16x16 INT8 MAC array, accumulated in int32.

    o/m/red count tiles of batch / output channels / input channels; the BLOCK_IN-deep
    dot product inside each tile is what the MAC array does in one go. `red` > 1 makes
    the accumulator carry across tiles, which needs the reduction split so that the
    resetting and accumulating micro-op kernels stay distinct.
    """
    o, m = 4, 4
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
    # Stage the operands into SRAM inside the reduction loop, so only one tile of each
    # is resident at a time -- the same structure the conv2d schedules use.
    s[x_buf].compute_at(s[y_gem], ko); s[x_buf].pragma(s[x_buf].op.axis[0], env.dma_copy)
    s[w_buf].compute_at(s[y_gem], ko); s[w_buf].pragma(s[w_buf].op.axis[0], env.dma_copy)
    s[y].pragma(s[y].op.axis[0], env.dma_copy)
    # GEMM is a tensor intrinsic, not a pragma: the reduction axes go outermost and
    # innermost, leaving a bare BATCH x BLOCK_OUT block for the MAC array.
    bo, co, bi, ci = s[y_gem].op.axis
    s[y_gem].reorder(ko, bo, co, bi, ci, ki)
    s[y_gem].tensorize(bi, env.gemm)
    with vta.build_config():
        mod = vta.build(s, [x, w, y], tvm.target.Target("ext_dev", host=env.target_host))
    f = upload(mod, remote, "gemm")
    dev = remote.ext_dev(0)
    rng = np.random.default_rng(2)
    # Keep |sum| inside int8: BLOCK_IN*red products of magnitude <= 4.
    x_np = rng.integers(-2, 2, size=(o, red, env.BATCH, env.BLOCK_IN)).astype(x.dtype)
    w_np = rng.integers(-2, 2, size=(m, red, env.BLOCK_OUT, env.BLOCK_IN)).astype(w.dtype)
    y_nd = tvm.nd.array(np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype=y.dtype), dev)
    f(tvm.nd.array(x_np, dev), tvm.nd.array(w_np, dev), y_nd)
    ref = np.zeros((o, m, env.BATCH, env.BLOCK_OUT), dtype="int32")
    for b in range(o):
        for i in range(m):
            for j in range(red):
                ref[b, i, :] += np.dot(x_np[b, j, :].astype("int32"),
                                       w_np[i, j].T.astype("int32"))
    ref = ref.astype(y.dtype)
    got = y_nd.numpy()
    good = np.array_equal(got, ref)
    print(f"  gemm {o*env.BATCH}x{red*env.BLOCK_IN} . {red*env.BLOCK_IN}x{m*env.BLOCK_OUT} "
          f"(red={red}): {'PASS' if good else 'FAIL'} correct={(got == ref).sum()}/{got.size}")
    return good


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "mem"
    # Point VTA_HW_PATH at a tree whose config says TARGET=sim to run the very same
    # schedules against the fast simulator, which is how we tell a bad schedule from
    # bad hardware.
    if env.TARGET in ("sim", "tsim"):
        from vta.testing import simulator  # loads libvta_fsim/libvta_tsim, registering ext_dev
        assert simulator.enabled()
        remote = rpc.LocalSession()
        print(f"[hw_test] local simulator, TARGET={env.TARGET}")
    else:
        remote = rpc.connect(HOST, PORT)
        print(f"[hw_test] connected to {HOST}:{PORT}, TARGET={env.TARGET}")
    if what == "mem":
        ok = test_mem(remote)
    elif what == "alu":
        ok = test_alu(remote, int(sys.argv[2]))
    elif what == "pad":
        ok = test_alu(remote, int(sys.argv[2]), "pad", [int(v) for v in sys.argv[3:7]])
    elif what == "gemm":
        ok = test_gemm(remote, int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    else:
        raise SystemExit("unknown test " + what)
    print("[hw_test]", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
