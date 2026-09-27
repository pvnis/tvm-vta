"""Autotune VTA's conv2d schedules for this board.

tophub has no entries for mpfs, so every measurement so far ran on a FALLBACK schedule and
TVM said so ("Cannot find config for target=..."). This searches the schedule space on the
real hardware and writes the winners to a log that conv_probe.py can load.

Setup it depends on (already done, see mpfs/README.md):
  - an RPC tracker on the host:  python -m tvm.exec.rpc_tracker --host 0.0.0.0 --port 9190
  - the board registered with it under key "mpfs" (VTA_RPC_TRACKER/VTA_RPC_KEY in the
    systemd drop-in that start_rpc_server.sh reads)

Two VTA-specific details:
  - the builder must cross compile for riscv64, so build_func is cc.cross_compiler(our gcc)
    rather than the default tar export;
  - the module loader must NOT be vta.module_loader(), which reprograms the FPGA before
    every measurement by downloading a bitstream. This board's fabric is programmed over
    JTAG and no such bitstream exists, so use the plain default_module_loader.

usage:
    tune_vta.py [n_trial]        TUNE_ONLY=C11 to tune a single layer
"""
import os
import sys
import time

import numpy as np
import tvm
from tvm import autotvm, te
from tvm.autotvm.measure import default_module_loader
from tvm.contrib import cc
import vta

TRACKER_HOST = os.environ.get("TVM_TRACKER_HOST", "127.0.0.1")
TRACKER_PORT = int(os.environ.get("TVM_TRACKER_PORT", "9190"))
CC = os.environ.get("VTA_CROSS_CC", "/home/dmd/polarfire_sandbox/vta/tools/rvcc/rv64gc-gcc")
LOG = os.environ.get("TUNE_LOG", "/home/dmd/polarfire_sandbox/vta/vta_tuning.log")
N_TRIAL = int(sys.argv[1]) if len(sys.argv) > 1 else 40

env = vta.get_env()

# (name, height, width, in_filter, out_filter, k, pad, stride) - the ResNet-18 layers, the
# same set conv_probe.py and compare_cpu_vta.py use.
WKLS = [
    ("C2", 56, 56, 64, 64, 3, 1, 1),   ("C3", 56, 56, 64, 128, 3, 1, 2),
    ("C4", 56, 56, 64, 128, 1, 0, 2),  ("C5", 28, 28, 128, 128, 3, 1, 1),
    ("C6", 28, 28, 128, 256, 3, 1, 2), ("C7", 28, 28, 128, 256, 1, 0, 2),
    ("C8", 14, 14, 256, 256, 3, 1, 1), ("C9", 14, 14, 256, 512, 3, 1, 2),
    ("C10", 14, 14, 256, 512, 1, 0, 2), ("C11", 7, 7, 512, 512, 3, 1, 1),
]


def make_task(h, w, ci, co, k, pad, st):
    layout = "NCHW%dn%dc" % (env.BATCH, env.BLOCK_IN)
    data = te.placeholder((1 // env.BATCH, ci // env.BLOCK_IN, h, w, env.BATCH, env.BLOCK_IN),
                          dtype=env.inp_dtype, name="data")
    kernel = te.placeholder((co // env.BLOCK_OUT, ci // env.BLOCK_IN, k, k,
                             env.BLOCK_OUT, env.BLOCK_IN), dtype=env.wgt_dtype, name="kernel")
    return autotvm.task.create(
        "conv2d_packed.vta",
        args=(data, kernel, (st, st), (pad, pad, pad, pad), (1, 1), layout, env.acc_dtype),
        target=tvm.target.Target(env.target, host=env.target_host),
    )


measure_option = autotvm.measure_option(
    builder=autotvm.LocalBuilder(build_func=cc.cross_compiler(CC)),
    runner=autotvm.RPCRunner(
        env.TARGET, host=TRACKER_HOST, port=TRACKER_PORT,
        number=4, repeat=1, timeout=60,
        # NOT vta.module_loader(): that reprograms the FPGA per measurement.
        module_loader=default_module_loader(),
    ),
)

only = os.environ.get("TUNE_ONLY")
todo = [w for w in WKLS if not only or w[0] == only]
print(f"[tune] {len(todo)} task(s), {N_TRIAL} trials each, log -> {LOG}", flush=True)

t0 = time.time()
for name, h, w, ci, co, k, pad, st in todo:
    task = make_task(h, w, ci, co, k, pad, st)
    space = len(task.config_space)
    n = min(N_TRIAL, space)
    print(f"\n[tune] {name}: {h}x{w} {ci}->{co} {k}x{k} /{st}, config space {space}, "
          f"{n} trials", flush=True)
    tuner = autotvm.tuner.XGBTuner(task, loss_type="rank")
    tuner.tune(
        n_trial=n,
        early_stopping=max(16, n // 2),
        measure_option=measure_option,
        callbacks=[autotvm.callback.progress_bar(n, prefix=name),
                   autotvm.callback.log_to_file(LOG)],
    )
print(f"\n[tune] done in {(time.time()-t0)/60:.1f} min -> {LOG}")
