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
ssh -n "$BOARD" 'pkill -f "[t]vm_rpc" || true'
scp libvta.so libtvm_runtime.so "$BOARD:/root/vta-board/lib/"
scp tvm_rpc "$BOARD:/root/vta-board/"
ssh -n "$BOARD" 'rm -f /root/vta-board/rpc.log; nohup /root/vta-board/start_rpc_server.sh 9091 > /root/vta-board/rpc.log 2>&1 & sleep 3; grep -q "bind to" /root/vta-board/rpc.log && echo "rpc server up" || cat /root/vta-board/rpc.log'
