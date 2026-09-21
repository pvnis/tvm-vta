#!/bin/bash
# Rebuild VTA's RTL from the Chisel sources: the TSIM library and, with -v, the synthesis
# Verilog that the Libero flow imports.
#
# This exists because none of it is discoverable. sbt is not on PATH (it lives in
# tools/sbt/bin), the Makefile's default TVM_PATH does not match this tree's layout, and a
# stale build/verilator makes the link fail with duplicate symbols whenever the new RTL is
# partitioned differently. Getting any of the three wrong either fails confusingly or - far
# worse - leaves a stale libvta_hw.so in place, so TSIM simulates old RTL and reports a pass
# that means nothing. That happened once already.
set -e
ROOT=/home/dmd/polarfire_sandbox/vta
HW=$ROOT/tvm-vta
export PATH=$ROOT/tools/sbt/bin:$PATH
export VTA_HW_PATH=$HW
export TVM_PATH=$ROOT/tvm

cd $HW/hardware/chisel
echo "[rebuild_rtl] cleaning generated RTL, build/verilator and the library"
# The generated Verilog must go too: the Makefile rule that produces it has no dependency on
# the .scala sources, so a leftover Test.*.sv is silently reused and the "rebuilt" library is
# just the OLD RTL recompiled. That happened on 09-21 with the debug-counter change.
rm -rf $HW/build/verilator $HW/build/libvta_hw.so $HW/build/chisel/Test.DefaultPynqConfig.sv
echo "[rebuild_rtl] building TSIM library"
make lib CONFIG=DefaultPynqConfig
ls -la $HW/build/libvta_hw.so

if [ "${1:-}" = "-v" ]; then
    echo "[rebuild_rtl] generating synthesis Verilog"
    rm -f $HW/build/chisel/VTA.DefaultPynqConfig.sv
    make verilog CONFIG=DefaultPynqConfig
    DEST=/home/dmd/polarfire_sandbox/refdesign-vta/vta_hw/VTA.DefaultPynqConfig.sv
    cp $HW/build/chisel/VTA.DefaultPynqConfig.sv $DEST
    echo "[rebuild_rtl] copied to $DEST"
    ls -la $DEST
fi
