#!/bin/bash
# Regenerate RTL, rebuild the bitstream, program the board, run one GEMM and read the
# debug registers. Written as a script so an interrupted shell cannot lose the chain.
set -u
V=/home/dmd/polarfire_sandbox/vta
R=/home/dmd/polarfire_sandbox/refdesign-vta
cd $V
echo "STEP: regenerate RTL (TSIM lib + synthesis Verilog)"
./rebuild_rtl.sh -v || exit 1
grep -q dbg_ld_deq $R/vta_hw/VTA.DefaultPynqConfig.sv || { echo "FAILED: traces missing from generated Verilog"; exit 1; }
echo "STEP: bitstream"
( cd $R && ./full_rebuild.sh ) || exit 1
echo "STEP: program + measure"
./reprogram_board.sh || exit 1
# Do NOT filter this through grep: when the run fails, grep swallows the error and the
# register dump that follows looks like data when it is really "nothing ran".
./run_hw.sh gemm_probe.py identity 1 > /tmp/trace_gemm.out 2>&1
echo "gemm rc=$? $(grep -oE 'correct=[0-9]+/[0-9]+|Check failed: timeout' /tmp/trace_gemm.out | head -1)"
python3 dbg_regs.py board
