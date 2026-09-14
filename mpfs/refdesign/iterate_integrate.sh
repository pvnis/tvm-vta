#!/bin/bash
# Fast iteration on vta_integrate.tcl: keep a pristine copy of the generated base design and
# restore it before each attempt, so only the integration step is re-run. The full
# full_cycle.sh regenerates the base every time, which is ~90 s of pure repetition when all
# you are doing is debugging the integration Tcl.
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -e
if [ ! -d MPFS_DISCOVERY.base ]; then
    echo "STEP: generate pristine base (once)"
    rm -rf MPFS_DISCOVERY
    xvfb-run -a $L script:MPFS_DISCOVERY_KIT_REFERENCE_DESIGN.tcl logfile:gen.log > gen.stdout 2>&1
    cp -a MPFS_DISCOVERY MPFS_DISCOVERY.base
fi
rm -rf MPFS_DISCOVERY
cp -a MPFS_DISCOVERY.base MPFS_DISCOVERY
echo "STEP: integrate VTA"
xvfb-run -a $L script:vta_integrate.tcl logfile:integrate.log > integrate.stdout 2>&1
echo "STEP: integration OK"
