#!/bin/bash
# Reboot the Discovery Kit and wait for the VTA RPC service to come back.
# A wedged VTA (see NOTES.md) only clears on a fabric reset, so tests that must
# start from a known-good accelerator go through here.
set -u
BOARD=${BOARD:-root@192.168.100.2}
ssh -n -o BatchMode=yes -o ConnectTimeout=5 "$BOARD" 'sync; systemd-run --on-active=1 systemctl reboot' >/dev/null 2>&1
sleep 45
for _ in $(seq 1 30); do
    if ssh -n -o BatchMode=yes -o ConnectTimeout=4 "$BOARD" 'systemctl is-active --quiet vta-rpc' 2>/dev/null; then
        sleep 2; echo "[reset_board] VTA RPC up"; exit 0
    fi
    sleep 5
done
echo "[reset_board] board did not come back"; exit 1
