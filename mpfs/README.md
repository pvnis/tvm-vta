# Running VTA on the PolarFire SoC Discovery Kit

Everything here supports `src/mpfs/mpfs_driver.cc`, which drives VTA from Linux on an
MPFS095T Discovery Kit with VTA in the fabric behind FIC0. No kernel module is needed.

## Contents

| file | what it is |
| --- | --- |
| `tvm-v0.18.0-mpfs.patch` | the TVM v0.18.0 changes needed to target `mpfs` (apply in a TVM checkout) |
| `build_board_runtime.sh` | rebuild the riscv64 board runtime and deploy it |
| `hw_test.py` | mem / alu / pad / gemm tests, over RPC on hardware or locally in a simulator |
| `gemm_probe.py` | prints the *structure* of a GEMM mismatch rather than pass/fail |
| `ubenv.py` | read and rewrite a U-Boot "env in FAT" blob (`uboot.env`) |

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

## Running the tests

On the board, start the C++ RPC server (the Python one needs numpy, which has no riscv64
wheels) with `LD_PRELOAD` pointing at `libvta.so`. Then from the host:

    python3 hw_test.py mem            # DMA round-trip
    python3 hw_test.py alu 1          # ACC load -> ALU shift -> OUT -> store
    python3 hw_test.py pad 1 2 1 0 1  # padded ACC load
    python3 hw_test.py gemm 1         # GEMM on the MAC array

`VTA_RPC_HOST` / `VTA_RPC_PORT` select the board; `VTA_CROSS_CC` selects the cross compiler.

To run the *same* schedules in a simulator instead — which is how you tell a bad schedule
from bad hardware — point `VTA_HW_PATH` at a tree whose `config/vta_config.json` has
`TARGET` set to `sim` or `tsim`; the scripts then use a local session automatically.

## Status

`mem`, `alu` and `pad` pass on hardware. `gemm` returns zeros on hardware while passing in
both FSIM and TSIM against the same RTL; see the "GEMM-returns-zero problem" section of the
project notes for what has been ruled out and what to try next.
