# Running VTA on the PolarFire SoC Discovery Kit

Everything here supports `src/mpfs/mpfs_driver.cc`, which drives VTA from Linux on an
MPFS095T Discovery Kit with VTA in the fabric behind FIC0. No kernel module is needed.

## Contents

| file | what it is |
| --- | --- |
| `tvm-v0.18.0-mpfs.patch` | the TVM v0.18.0 changes needed to target `mpfs` (apply in a TVM checkout) |
| `run_hw.sh` | run a script against the board: sets `TARGET=mpfs`, `VTA_HW_PATH`, `PYTHONPATH`, `VTA_RPC_*` |
| `run_sim.sh` | the same against `sim`/`tsim`; refuses to run if the RTL is newer than the built library |
| `rebuild_rtl.sh` | regenerate the TSIM library from the Chisel; `-v` also emits the synthesis Verilog |
| `build_board_runtime.sh` | rebuild the riscv64 board runtime and deploy it |
| `reprogram_board.sh` | reprogram the FPGA over JTAG (the only reliable way to reset the fabric) |
| `hw_test.py` | mem / alu / pad / gemm tests |
| `matrix.py` | runs a matrix of programs, classifying every execution `NOTRUN`/`WRONG`/`ok` |
| `conv_probe.py` | real ResNet-18 conv2d layers through the full TVM stack (`CONV_ONLY=C11` for one) |
| `gemm_probe.py`, `bias_probe.py`, `ysweep.py`, `pad_probe.py`, `wedge_stress.py` | targeted probes |
| `dbg_regs.py` | decode VTA's debug registers, from the board (`board`) or from TSIM (`tsim`) |
| `trace_cycle.sh` | regenerate RTL, rebuild the bitstream, program, measure - unattended |
| `refdesign/` | the Libero flow: integration tcl, build scripts, component configs |
| `NOTES.md` | the running record: what was tried, what was measured, what was retracted |

## The three things that have to be right

1. **`iomem=relaxed` in the kernel command line.** The driver maps the reserved non-cached
   pool through `/dev/mem`. The stock kernel sets `CONFIG_STRICT_DEVMEM` and
   `CONFIG_IO_STRICT_DEVMEM`, and the generic `devmem_is_allowed()` rejects on
   `iomem_is_exclusive()` before reaching the `page_is_ram()` test the pool would pass.
   Add it to U-Boot's `mpfs_set_bootargs` — `ubenv.py` rewrites `uboot.env` in place
   (check the round-trip is byte-exact before trusting it, and keep a backup).

2. **The RISC-V ABI must be pinned on both sides.** Ubuntu's riscv64 GCC defaults to RVA23,
   whose vector instructions SIGILL on this rv64gc part, and LLVM emits soft-float-ABI
   objects that an `lp64d` linker refuses to merge. The patch pins
   `-mattr=+m,+a,+f,+d,+c -mabi=lp64d`; link modules with a wrapper that forces
   `-march=rv64gc -mabi=lp64d`.

3. **`VTA_HW_PATH` must be exported when configuring TVM.** `cmake/modules/VTA.cmake`
   silently falls back to `tvm/3rdparty/vta-hw`, whose config targets `sim`, producing a
   `libvta.so` with no mpfs driver in it. `build_board_runtime.sh` asserts the configured
   target is `mpfs` for exactly this reason.

## Running something on the accelerator

The board must be reachable (`ping 192.168.100.2`) with `vta-rpc` running on it, and the
fabric already programmed - `./reprogram_board.sh` does that over JTAG and waits for Linux.

Everything goes through `run_hw.sh`, which sets the target, paths and RPC address:

    ./run_hw.sh hw_test.py mem          # DMA round-trip
    ./run_hw.sh hw_test.py alu 1        # ACC load -> ALU shift -> OUT -> store
    ./run_hw.sh hw_test.py gemm 1       # GEMM on the MAC array
    ./run_hw.sh matrix.py 5             # 4 programs x 5 reps, every execution classified
    CONV_ONLY=C11 ./run_hw.sh conv_probe.py    # one real ResNet-18 conv2d layer
    ./run_hw.sh conv_probe.py                  # all ten layers (see Status)

`VTA_RPC_HOST` / `VTA_RPC_PORT` override the board address; `VTA_CROSS_CC` the cross
compiler. To run the *same* schedules in a simulator instead - which is how you tell a bad
schedule from bad hardware - use `./run_sim.sh tsim <script>` (or `sim`).

## Writing your own program

The pattern is ordinary TVM, plus two board-specific details. `gemm_probe.py` is the
smallest complete example and `conv_probe.py` the realistic one.

    env = vta.get_env()
    # ... build a schedule, set_scope to env.inp_scope / wgt_scope / acc_scope,
    # pragma env.dma_copy on the copies, tensorize with env.gemm or pragma env.alu ...
    with vta.build_config():
        mod = vta.build(s, [a, b, c], tvm.target.Target("ext_dev", host=env.target_host))

    remote = rpc.connect(HOST, PORT)            # or rpc.LocalSession() under sim/tsim
    path = temp.relpath("mykernel.so")
    mod.export_library(path, cc=CC)             # 1. cross compile for riscv64
    remote.upload(path)
    f = remote.load_module("mykernel.so")
    dev = remote.ext_dev(0)                     # 2. tensors live on the ext_dev device

The two details: `export_library` needs the riscv64 cross compiler (`VTA_CROSS_CC`), because
the stock VTA tests save a `.o` for the RPC server to link and this board's runtime cannot;
and operands must be `tvm.nd.array(..., dev)` on `remote.ext_dev(0)` so they land in the
non-cached DMA pool VTA reads.

One habit worth keeping: pre-fill the output with a sentinel value rather than zeros. A
device that has stopped executing writes nothing, which is indistinguishable from computing
zeros if the buffer started at zero. `matrix.py` does this for you.

## Status

Working. `mem`, `alu`, `pad` and `gemm` pass on hardware at every size tried, and all ten
ResNet-18 conv2d layers pass through the full TVM stack, 8.4 to 10.9 GOPS on the 3x3 layers
(the 1x1 layers run near 1 GOPS: they move nearly as much data for a ninth of the
arithmetic). 60 consecutive executions run correctly with no wedge.

The long-standing "GEMM returns zeros" and "the device stops accepting work" faults had a
single cause: LOAD and STORE each used a plain Chisel `Queue` for their instruction queue.
That is an asynchronous-read `Mem`, and at 512 x 128 bits PolarFire synthesis maps it into
LSRAM, which can only read synchronously - so instructions came out corrupted. Both now use
`SyncQueue`, which COMPUTE always used. See `NOTES.md`.

Known issue: running all ten conv layers in ONE process kills the RPC server part way,
while each passes on its own, so something accumulates across workloads in a process
(pool fragmentation or a leak in the driver's free list is the current suspect). Run one
layer per process with `CONV_ONLY=` until that is fixed.
