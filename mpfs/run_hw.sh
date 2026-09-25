#!/bin/bash
# Usage: run_hw.sh <script.py> [args...]   - runs a host-side script against the board (TARGET=mpfs)
set -e
ROOT=/home/dmd/polarfire_sandbox/vta
HW=$ROOT/tvm-vta
python3 - "$HW/config/vta_config.json" mpfs <<'PY'
import json,sys; p,t=sys.argv[1],sys.argv[2]; c=json.load(open(p)); c["TARGET"]=t; json.dump(c,open(p,"w"),indent=2)
PY
export VTA_HW_PATH=$HW TVM_LIBRARY_PATH=$ROOT/tvm/build
export PYTHONPATH=$ROOT/tvm/python:$ROOT/tvm/vta/python
export PATH=$ROOT/venv/bin:$PATH
# The stock VTA test helpers (vta.testing.run) locate the board through these.
export VTA_RPC_HOST=${VTA_RPC_HOST:-192.168.100.2} VTA_RPC_PORT=${VTA_RPC_PORT:-9091}
exec python "$@"
