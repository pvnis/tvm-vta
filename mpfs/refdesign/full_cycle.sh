#!/bin/bash
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -e
# MPFS_DISCOVERY_KIT_REFERENCE_DESIGN.tcl only OPENS the project when it already exists, so
# a leftover project makes the integration step fail ("a core already exists with this
# name") or, worse, silently build the previous design. Start from nothing every time.
# Anything worth keeping from a previous build must be copied out beforehand - see
# ../refdesign-vta-125mhz-backup for the last 125 MHz build.
rm -rf /home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY

echo "STEP: generate base reference design"
xvfb-run -a $L script:MPFS_DISCOVERY_KIT_REFERENCE_DESIGN.tcl logfile:gen.log > gen.stdout 2>&1
echo "STEP: integrate VTA"
xvfb-run -a $L script:vta_integrate.tcl logfile:integrate.log > integrate.stdout 2>&1
echo "STEP: build (synth, P&R, timing)"
xvfb-run -a $L script:vta_build.tcl logfile:build.log > build.stdout 2>&1
echo "STEP: programming data"
xvfb-run -a $L script:vta_prog.tcl logfile:prog.log > prog.stdout 2>&1
echo "STEP: ALL DONE"
