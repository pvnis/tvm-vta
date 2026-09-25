#!/bin/bash
# Build the VTA design and CHECK it, rather than trusting Libero's exit code.
#
# Two failure modes have each cost this project days, and neither shows up as a non-zero
# exit: Libero exits 0 after a failed step (and only flushes its log at script end), and a
# design whose I/O constraints were dropped builds perfectly but places the board's 50 MHz
# oscillator on the wrong pin, so the board never boots. Both are checked here explicitly.
cd /home/dmd/polarfire_sandbox/refdesign-vta
. /usr/local/microchip/libero-env.sh
L=/usr/local/microchip/Libero_SoC_2026.1/Libero_SoC/Designer/bin/libero
set -u

step () {   # step <name> <tcl> <logfile>
    echo "STEP: $1"
    xvfb-run -a $L script:"$2" logfile:"$3" > "${3%.log}.stdout" 2>&1
    if grep -qE "^Error" "$3" 2>/dev/null; then
        echo "  FAILED - errors from $3:"
        grep -E "^Error" "$3" | head -10
        exit 1
    fi
    echo "  ok"
}

step "build (synth, P&R, timing)" vta_build.tcl buildv.log
step "programming data"           vta_prog.tcl  progv.log

D=MPFS_DISCOVERY/designer/MPFS_DISCOVERY_KIT

echo
echo "CHECK: reference clock pin (must be R18, the board oscillator)"
PIN=$(grep -rhoE "REF_CLK_50MHz -DIRECTION INPUT -pin_name [A-Z0-9]+" $D/*.pdc 2>/dev/null | head -1 | awk '{print $NF}')
echo "  REF_CLK_50MHz -> ${PIN:-UNKNOWN}"
[ "$PIN" = "R18" ] || { echo "  WRONG PIN - the I/O constraints were dropped again; do not program this."; exit 1; }

echo
echo "CHECK: worst VTA timing path"
python3 - "$D/MPFS_DISCOVERY_KIT_max_timing_multi_corner.xml" <<'PY'
import re, sys
try:
    t = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    print("  no timing report"); raise SystemExit(1)
best = None
for r in re.findall(r"<row>(.*?)</row>", t, re.S):
    c = [re.sub(r"<.*?>", "", x).strip() for x in re.findall(r"<cell>(.*?)</cell>", r, re.S)]
    if len(c) >= 9 and c[0].startswith("Path") and "VTA_0" in c[1] + c[2]:
        try: s = float(c[4])
        except ValueError: continue
        if best is None or s < best[0]: best = (s, c)
if not best:
    print("  NO VTA PATHS IN THE TIMING REPORT - VTA's clock is unconstrained,"
          "\n  so this build's timing means nothing. Do not program it."); raise SystemExit(1)
s, c = best
short = lambda x: x.replace("FIC_0_PERIPHERALS_0/VTA_0/shell/core/", "VTA/")[:78]
print(f"  worst VTA slack {s:+.3f} ns  (min period {c[-2]} ns, {c[-1]})")
print(f"    from {short(c[1])}")
print(f"    to   {short(c[2])}")
print("  " + ("PLENTY of margin" if s > 1.0 else
              "thin - better than 0.139 but watch it" if s > 0.4 else
              "STILL MARGINAL - this will not fix the GEMM"))
PY
echo
echo "STEP: ALL DONE - ready to program"
