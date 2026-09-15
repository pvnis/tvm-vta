#!/bin/bash
# Reprogram the FPGA and wait for the board. This is the ONLY reliable way to get a clean
# accelerator: a Linux reboot (reset_board.sh) does not reset the fabric, so a device that
# has been left in a bad state by a previous program stays bad across it.
set -u
cd /home/dmd/polarfire_sandbox/refdesign-vta
ssh -n -o BatchMode=yes -o ConnectTimeout=5 root@192.168.100.2 'sync' >/dev/null 2>&1
./vta_program_run.sh > program_cycle.log 2>&1
grep -q "Chain programming PASSED" program.log || { echo "[reprogram] PROGRAMMING FAILED"; exit 1; }
for _ in $(seq 1 30); do
    sleep 6
    if ssh -n -o BatchMode=yes -o ConnectTimeout=4 root@192.168.100.2 \
         'systemctl is-active --quiet vta-rpc' 2>/dev/null; then
        echo "[reprogram] board up on a freshly reset fabric"; exit 0
    fi
done
echo "[reprogram] board did not come back"; exit 1
