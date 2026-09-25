#!/bin/bash
# Rebuild and program the known-good 125 MHz project after restoring it. Libero refuses to
# program a project whose flow state is stale (moving the directory does that), so the
# synthesis/P&R/programming-data steps have to be re-run even though the design is unchanged.
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -e
echo "STEP: build"
xvfb-run -a $L script:vta_build.tcl logfile:restore_build.log > restore_build.stdout 2>&1
echo "STEP: programming data"
xvfb-run -a $L script:vta_prog.tcl logfile:restore_prog.log > restore_prog.stdout 2>&1
echo "STEP: program device"
xvfb-run -a $L script:vta_program.tcl logfile:restore_program.log > restore_program.stdout 2>&1
echo "STEP: RESTORE DONE"
