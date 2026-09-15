#!/bin/bash
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -e
# Check for Tcl errors explicitly: Libero exits 0 even when a script step fails, which
# previously let a build "run" for hours after it had already died at configure_tool.
echo "STEP: build (synth, multi-pass P&R, timing)"
xvfb-run -a $L script:vta_build_multipass.tcl logfile:build_mp.log > build_mp.stdout 2>&1
if grep -qE "^Error" build_mp.log; then
    echo "STEP: BUILD FAILED"; grep -E "^Error" build_mp.log | head -3; exit 1
fi
echo "STEP: programming data"
xvfb-run -a $L script:vta_prog.tcl logfile:prog_mp.log > prog_mp.stdout 2>&1
echo "STEP: BUILD DONE"
