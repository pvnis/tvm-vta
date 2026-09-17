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
export PATH=$ROOT/venv/bin:$PATH
exec python "$@"
