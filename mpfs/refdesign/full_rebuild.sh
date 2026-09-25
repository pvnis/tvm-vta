#!/bin/bash
# Full clean build of the VTA design from a regenerated base.
#
# The base is regenerated rather than reused because MPFS_DISCOVERY.base had been poisoned:
# its .prjx and smartgen/ still referenced a VTA_CTRL_BRIDGE component from an abandoned
# bridge attempt, and Libero reported "Unable to find VTA_CTRL_BRIDGE.cxf" on every
# integration run. Nothing instantiated it, but carrying stale component references into a
# build is not worth the doubt.
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -u

rm -rf MPFS_DISCOVERY MPFS_DISCOVERY.base

step () {   # step <name> <tcl> <logfile>
    echo "STEP: $1"
    xvfb-run -a $L script:"$2" logfile:"$3" > "${3%.log}.stdout" 2>&1
    if grep -qE "^Error" "$3" 2>/dev/null; then
        echo "  FAILED - errors from $3:"; grep -E "^Error" "$3" | head -10; exit 1
    fi
    echo "  ok"
}

step "generate base reference design" MPFS_DISCOVERY_KIT_REFERENCE_DESIGN.tcl gen100.log
step "integrate VTA"       vta_integrate.tcl                       integrate100.log
exec ./build_check.sh
