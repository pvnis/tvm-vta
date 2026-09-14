#!/bin/bash
# Synthesis -> P&R -> timing -> programming data, on an already-integrated project.
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -e
echo "STEP: build (synth, P&R, timing)"
xvfb-run -a $L script:vta_build.tcl logfile:build.log > build.stdout 2>&1
echo "STEP: programming data"
xvfb-run -a $L script:vta_prog.tcl logfile:prog.log > prog.stdout 2>&1
echo "STEP: BUILD DONE"
