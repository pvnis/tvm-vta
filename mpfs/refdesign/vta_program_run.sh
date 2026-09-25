#!/bin/bash
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
xvfb-run -a $L script:vta_program.tcl logfile:program.log > program.stdout 2>&1
echo "exit=$?"
