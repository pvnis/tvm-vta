#!/bin/bash
# Usage: run_sim.sh <sim|tsim> <script.py> [args...]  - runs a host-side script against a simulator
set -e
ROOT=/home/dmd/polarfire_sandbox/vta
HW=$ROOT/tvm-vta
TGT=$1; shift
python3 - "$HW/config/vta_config.json" "$TGT" <<'PY'
import json,sys; p,t=sys.argv[1],sys.argv[2]; c=json.load(open(p)); c["TARGET"]=t; json.dump(c,open(p,"w"),indent=2)
PY
export VTA_HW_PATH=$HW TVM_LIBRARY_PATH=$ROOT/tvm/build
export PYTHONPATH=$ROOT/tvm/python:$ROOT/tvm/vta/python
export PATH=$ROOT/venv/bin:$ROOT/tools/sbt/bin:$PATH

# TSIM loads a PREBUILT libvta_hw.so and nothing in this flow rebuilds it when the Chisel
# changes, so a stale library silently simulates old RTL and reports a pass. That happened:
# a change to TensorGemm.scala was "validated" against a library built the previous day,
# and it was only caught by accident. Refuse to run rather than report a meaningless pass.
LIB=$HW/build/libvta_hw.so
if [ ! -f "$LIB" ]; then
    echo "[run_sim] no $LIB - build it first:" >&2
    echo "    cd $HW/hardware/chisel && VTA_HW_PATH=$HW PATH=$ROOT/tools/sbt/bin:\$PATH make lib CONFIG=DefaultPynqConfig" >&2
    exit 1
fi
NEWER=$(find "$HW/hardware/chisel/src" -name "*.scala" -newer "$LIB" -print -quit 2>/dev/null)
if [ -n "$NEWER" ]; then
    echo "[run_sim] REFUSING TO RUN: $LIB is older than the Chisel sources." >&2
    echo "[run_sim]   e.g. $NEWER" >&2
    echo "[run_sim] TSIM would simulate stale RTL and report a pass that means nothing." >&2
    echo "[run_sim] Rebuild (a clean build/verilator is needed when the RTL partitioning changes):" >&2
    echo "    rm -rf $HW/build/verilator" >&2
    echo "    cd $HW/hardware/chisel && VTA_HW_PATH=$HW PATH=$ROOT/tools/sbt/bin:\$PATH make lib CONFIG=DefaultPynqConfig" >&2
    exit 1
fi
exec python "$@"
