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
echo "--- drive to the wedge ---"
./run_hw.sh matrix.py 5 2>&1 | grep -E "^\[matrix\] (alu|gemm|execution|every)"
echo "--- control reg + counters after that ---"
ssh -n -o BatchMode=yes root@192.168.100.2 'printf "ctrl="; devmem2 0x60020000 w | tail -1 | sed "s/.*: //"'

python3 dbg_regs.py board
