#!/bin/bash
# Rebuild the riscv64 board runtime (libtvm_runtime.so, libvta.so, tvm_rpc) and
# deploy it to the Discovery Kit.
#
# VTA_HW_PATH MUST be exported: cmake/modules/VTA.cmake falls back to
# tvm/3rdparty/vta-hw (whose config targets "sim") when it is unset, which
# silently produces a libvta.so with no mpfs driver in it.
set -euo pipefail

VTA_ROOT=/home/dmd/polarfire_sandbox/vta
export VTA_HW_PATH="$VTA_ROOT/tvm-vta"
BUILD="$VTA_ROOT/tvm/build-riscv64"
BOARD="${BOARD:-root@192.168.100.2}"

cd "$BUILD"
cmake . > cmake_build.log 2>&1
target=$(grep -o 'Build VTA runtime with target: .*' cmake_build.log | tail -1 | awk '{print $NF}')
[[ "$target" == "mpfs" ]] || { echo "!! configured for target '$target', expected mpfs"; exit 1; }
ninja vta tvm_runtime tvm_rpc

echo "== deploying to $BOARD =="
# The server runs as vta-rpc.service. Stop it (and any stray copy) before overwriting the
# libraries, then let systemd start it again: a stray nohup'd server left holding port 9091
# makes the restarted one silently fall back to 9092, and the host then talks to whichever
# stale binary owns 9091.
ssh -n "$BOARD" 'systemctl stop vta-rpc 2>/dev/null; pkill -9 -f "[t]vm_rpc" || true; sleep 1'
scp libvta.so libtvm_runtime.so "$BOARD:/root/vta-board/lib/"
scp tvm_rpc "$BOARD:/root/vta-board/"
ssh -n "$BOARD" 'systemctl start vta-rpc; sleep 3
    port=$(journalctl -u vta-rpc --no-pager -n 20 | grep -o "bind to 0.0.0.0:[0-9]*" | tail -1)
    case "$port" in
        *9091) echo "rpc server up on 9091" ;;
        "")    echo "!! rpc server did not report a bind"; exit 1 ;;
        *)     echo "!! rpc server came up on the wrong port: $port"; exit 1 ;;
    esac'
