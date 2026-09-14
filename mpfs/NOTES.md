# VTA on PolarFire SoC Discovery Kit (MPFS095T) - working notes

## Step 1: Verilog generation (2026-09-11)
- apache/tvm-vta @ d4a15f6 (2022-01-27, "Port to new Chisel stable release (3.5)"); repo otherwise dormant.
- Toolchain: Scala 2.12.15, Chisel 3.5.0, project-pinned sbt 1.3.2 -> needs JDK 11
  (installed openjdk-11-jdk-headless). Launcher: sbt 1.13.0 in vta/tools/sbt (NOT sbt 2.x).
    export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
    PATH=$JAVA_HOME/bin:~/polarfire_sandbox/vta/tools/sbt/bin:$PATH
    cd tvm-vta/hardware/chisel
    sbt -batch 'runMain vta.DefaultPynqConfig --target-dir build/chisel -o VTA.DefaultPynqConfig'
- DefaultPynqConfig = CoreConfig (1x16x16 int8 GEMM, 32-bit acc; buffers uop 32K, inp 32K,
  wgt 256K, acc 128K) + XilinxShell:
    s_axi_control : AXI4-Lite slave, 16-bit addr, 32-bit data   (control registers)
    m_axi_gmem    : AXI4 master, 32-bit addr, 64-bit data, 8-bit ID (DMA)
  437 top-level I/O bits in total. Buffers come out as 64-bit-wide arrays.

## Step 2: trial fit, standalone (vta/trial/)
Wrapper vta_trial_top.v: xorshift generators drive all 165 input bits, 270 output bits
XOR-reduced to one pin. Libero project MPFS095T / FCSG325 / -1 / EXT, clock constrained to 8 ns.
  4LUT 33639 (36%)   DFF 19083 (20%)   LSRAM 214/308 (69%)   uSRAM 24   Math 128/292 (44%)
  Math: 128 MACC_PA, every one .DOTP(VCC) = dot-product mode, 2 int8 products per block,
        so all 256 multipliers are present. 164 math blocks remain.
  Timing @125 MHz: FAIL, WNS -1.179 ns (slow_lv_ht), min period 9.116 ns -> Fmax ~110 MHz.
        Hold met (+0.059 ns).
  All 751 violating paths (394 endpoints) START at an instruction-queue RAM read
  (compute/inst_q = SyncQueue, load/inst_q = Queue) and end in DMA command generation
  (vmeCmd/rdCmdStartIdx, blocksReadNb) after 16-23 logic stages. GEMM/ALU datapath: no violations.
  Full path list: libero/designer/vta_trial_top/max_report.json (pathlist_header + data).
Combined with the reference design (23 LSRAM) -> ~237/308 LSRAM (77%).

## Proposed timing fix (not yet applied)
Register the instruction right after the instruction queue, before decode, in
core/Load.scala and core/Compute.scala, e.g. Queue(inst_q.io.deq, 1, pipe = true).
Costs 1 cycle per instruction (instructions are coarse tensor ops). Fallback: run VTA
at 100 MHz in its own FIC clock domain.

## Timing fix applied (branch polarfire-timing, commit c7e7f30, pushed to pvnis)
Remote: pvnis = git@github.com:pvnis/tvm-vta.git (SSH as GitHub user dmd17).
Repo-local git identity: Dan Mihai Dumitriu <dmd17@cornell.edu>.
Change: val inst_head = Queue(inst_q.io.deq, 1, pipe = true) in core/Load.scala and
core/Compute.scala; every inst_q.io.deq consumer now reads inst_head.
Trial fit (vta/trial_fix, default single-pass P&R):
  WNS -1.179 -> -0.120 ns, failing paths 751 -> 14 (all compute/loadUop), Fmax ~109 -> ~123 MHz,
  hold +0.027 ns. +25 4LUT, +120 DFF; LSRAM/Math unchanged.
Chisel unit tests 30/30 pass - but they do NOT cover Load/Compute. TSIM validation pending.
Next: multi-pass P&R with the reference design's PLACEROUTE settings (vta/trial_fix/pr_multi.tcl).

## Multi-pass P&R (reference design PLACEROUTE settings) on the fixed core
Seeds: #1 -0.255, #2 -0.083, #3 -0.863, #4 0.000 (stopped). Sign-off multi-corner:
  setup WNS +0.000 ns (slow_lv_ht), hold +0.076 ns, 0 failing paths; 18 paths < 0.1 ns, 54 < 0.25 ns.
=> Meets 125 MHz standalone, but with zero margin and strong seed dependence.
All near-critical paths: compute/inst_head bits 80-86 (inside MemDecode x_size) ->
  loadUop/tensorLoad/vmeCmd(GenVMECmd)/cmdGen/rdCmdStartIdx, 28 logic stages.
Candidate fix #2: register io.start + io.inst inside GenVMECmd (TensorLoadNarrowVME.scala ~l.549).
Riskier than fix #1 - GenVMECmd is cycle-coupled to readData/fillPadding (cf. the existing
"RegNext(io.start) // stage it to move from instr que" on fillPadding). Do NOT apply without
TSIM (Verilator) regression comparing original vs patched RTL.

## TSIM (Verilator) validation setup - 2026-09-11
Layout under ~/polarfire_sandbox/vta:
  tvm/            TVM v0.18.0 (last release with VTA), built with LLVM 18, USE_VTA_FSIM/TSIM ON
                  (cmake 4 needs -DCMAKE_POLICY_VERSION_MINIMUM=3.5; gcc 15 OK)
  venv/           Python 3.11 via uv (system Python 3.14 too new); numpy<2 (1.26.4)
  tvm-vta/        branch polarfire-timing = timing fix (c7e7f30) + TSIM compat (c6b5580)  [PATCHED]
  tvm-vta-base/   worktree at d4a15f6 + same TSIM compat, uncommitted            [BASELINE]
  tvm-vta-de10/   worktree, baseline with DefaultDe10Config (discriminator run)
  run_vta.sh <hw tree> <sim|tsim> <pytest args>   - sets TARGET, env, runs TVM's VTA tests
  probe_acc_path.py <save|pad> <shift> [pads]     - ACC->ALU->OUT->store probe with sentinel
TSIM hw lib: (cd <tree>/hardware/chisel && make lib CONFIG=DefaultPynqConfig TVM_PATH=... VTA_HW_PATH=<tree>)
  -> <tree>/build/libvta_hw.so ; runtime picks it from $VTA_HW_PATH/build (TVM build dir is
  searched FIRST - never leave a libvta_hw.so there).

Bit-rot fixed (commit c6b5580): TVM 0.18 String API in DPIModule::GetFunction; Verilator 5
thread pool (verilated_threads) in both libs; force-include VTest__Dpi.h (C linkage of DPI
functions); -std=c++17. Known: abort at process exit (VerilatedContext teardown) - harmless.

Results:
  FSIM test_vta_insn: 7/7.   TSIM test_vta_insn, baseline AND patched: 5 pass, same 2 fail.
  The 2 failures (save_load_out, padded_load) = FSIM/RTL semantic gap: TVM folds `>> 0`,
  FSIM stores from ACC (RunStore -> acc_.TruncStore), RTL stores the OUT buffer which only
  GEMM/ALU write -> stale randomized OUT contents. Same with DefaultDe10Config.
  With a real ALU op (probe_acc_path.py shift>=1, several pad shapes) RTL passes.
  A/B probe matrix: identical PASS/FAIL on every case; patched = exactly +1 cycle per program
  (the 1-entry pipe queue only costs latency once, not per instruction).
  Integration (TSIM, both builds): test_benchmark_gemm, test_benchmark_topi_dense,
  test_benchmark_topi_conv2d -> 4/4 pytest pass; all 10 ResNet-18 VTA conv2d layers (C2-C11)
  PASS vs reference on BOTH builds. GEMM-benchmark cycles: 414498/195741/67347 (base) vs
  +21 each (patched) = +0.005..0.03%. (Printed "GOPS" in these logs is TSIM wall-clock speed,
  not hardware throughput.)
  => Fix #1 (inst_head) validated: behaviour-identical, negligible cycle cost.

## Fix #2: latch the instruction in the VME command generators (commit 1a6f3f7)
Two parts, because TensorLoad picks a different implementation per tensor type:
  2a GenVMECmd        (TensorLoadNarrowVME) - inp/wgt/acc at this config
  2b GenVMECmdWideTL  (TensorLoadWideVME)   - the uop path = the CRITICAL one
Only 2b moved the critical path; 2a alone actually made WNS worse (dead weight on a
non-critical path). Both validated; kept for symmetry.

Trial fits, 8 ns, DEFAULT single-pass P&R (apples-to-apples):
  baseline        WNS -1.179  751 failing paths   worst path starts at instr-queue RAM
  fix1            WNS -0.120   14                 worst path starts at inst_head
  fix1+2a         WNS -0.448  112                 (worse - extra FFs, no benefit)
  fix1+2a+2b      WNS +0.016    0                 worst path now starts INSIDE cmdGen
fix1 alone only closed with multi-pass P&R on seed 4; the full set closes on an ordinary
single-pass run. Area: +37 4LUT, +153 DFF vs fix1; LSRAM 214 and Math 128 unchanged.

DON'T over-constrain to measure Fmax: a 7.0 ns run gave WNS -1.668 (max period 8.668 ns),
WORSE than the 8.0 ns run's 7.984 ns, because the placer spreads effort over hundreds of
failing paths. Measure margin at the target period with multi-pass + STOP_ON_FIRST_PASS:false.

## Real margin at 125 MHz (multi-pass, STOP_ON_FIRST_PASS:false, 5 seeds, 8 ns)
fix1+2a+2b seeds: -0.103, -0.155, +0.067, +0.003, -0.021  -> best +0.067 ns (~126 MHz).
So VTA standalone sits ON the 125 MHz edge: 2 of 5 seeds meet, spread ~0.22 ns.
(fix1 alone, same style of run: -0.255, -0.083, -0.863, 0.000 -> much worse distribution.)
Remaining critical path is internal to cmdGen: rdLineElemBeginAddr -> rdCmdStartIdx, 30 levels.
Going further needs real pipelining of the DMA address arithmetic, not just latching.

IMPLICATION FOR INTEGRATION: with the reference design also in the device (VTA alone is
36% 4LUT / 69% LSRAM; together ~47% LUT, ~84% LSRAM) congestion will likely push this
negative. Plan A: give VTA its own FIC clock at 100 MHz (each MSS FIC has an independent
fabric clock, so no fabric CDC needed - the MSS handles it). Plan B: 125 MHz shared with
FIC0 as a stretch goal, decided by the integrated build.

## VTA integrated into the Discovery Kit reference design (2026-09-12)
Working copy: ~/polarfire_sandbox/refdesign-vta (clone of the reference design repo; the
verified plain reference design in ~/polarfire_sandbox/polarfire-soc-discovery-kit-reference-design
is untouched). Scripts: vta_integrate.tcl, vta_build.tcl,
script_support/additional_configurations/vta/{FIC0_INITIATOR,DMA_INITIATOR}.vta.tcl
(copies of Microchip's smarthls .mod.tcl; FIC0 SLAVE2 changed to DATA_WIDTH:32 TYPE:1=AXI4Lite).

Topology (follows Microchip's SmartHLS integration pattern):
  CPU -> FIC0 -> FIC0_INITIATOR slave port 2 @ 0x7000_0000..0x707FFFFF -> VTA s_axi_control
         (interconnect does 64-bit AXI4 -> 32-bit AXI4Lite protocol + width conversion)
  VTA m_axi_gmem (64-bit AXI4) -> DMA_INITIATOR MASTER1 -> FIC0 -> MSS -> DDR
  clock/reset: FIC_0_CLK 125 MHz / RESETN_FIC_0_CLK (no clock or MSS changes at all)
  VTA's WID and 2-bit AWLOCK/ARLOCK left unmapped (AXI3 leftovers; AXI4 bif has no place
  for them, and VTA never issues exclusive accesses).
HDL+ flow: create_hdl_core + hdl_core_add_bif {AXI4:AMBA:AMBA4:master|slave} with signal
maps generated from the RTL port list; sd_instantiate_hdl_core into FIC_0_PERIPHERALS.
Libero has no AXI4Lite bus definition - a Lite port is AXI4 with only the Lite subset mapped.

RESULT (first build, 0 errors):
  125 MHz domain: setup +0.113 ns, hold +0.057 ns    50 MHz: +2.164 / +0.057   ALL MET
  4LUT 47889/93516 (51%)  DFF 29606 (32%)  LSRAM 237/308 (77%)  Math 128/292 (44%)
  uSRAM 89/876  User I/O 58/80.  All 128 MACC_PA present in DOTP mode = VTA's 256 int8 MACs.
  All 1000 worst paths are VTA's; worst is an LSRAM A_CLK->B_DIN inside tensorLoad.
NB: this contradicts my prediction that congestion would eat the standalone margin - the
integrated build is actually better (+0.113) than standalone (+0.016 single-pass / +0.067 best-of-5).

## VTA running on hardware (2026-09-12)
MISTAKE + FIX worth remembering: sourcing Microchip's smarthls FIC0_INITIATOR.mod.tcl wholesale
silently REMAPPED the existing fabric DMA controller (0x6001_0000 32-bit AXI4Lite -> 0x6000_1000
64-bit AXI4) because that template targets a different revision of the reference design. The
build reported 0 errors; the symptom was a load access fault reading VTA. Correct approach:
copy THIS design's own script_support/components/{FIC0_INITIATOR,DMA_INITIATOR}.tcl and change
only NUM_SLAVES 2->3 (+ widen SLAVE2 window) and NUM_MASTERS 1->2, and turn the leading
create_and_configure_core into configure_core (the component already exists). Also must call
generate_component -component_name {FIC_0_PERIPHERALS} -recursive 1 before synthesis.

This design's FIC0_INITIATOR already had SLAVE2 configured as 32-bit AXI4Lite at 0x6002_0000 -
exactly what VTA's control port needs, so no width/protocol shim and no template needed.
FINAL MAP: VTA control 0x6002_0000..0x6002_ffff (64 KB); DMA controller stays at 0x6001_0000;
LSRAM 0x6000_0000. VTA DMA master -> DMA_INITIATOR MASTER1 -> FIC0 -> DDR.
(FIC0's CPU window is 0x6000_0000-0x7FFF_FFFF per Microchip docs; whether 0x7000_0000 would
also have worked was never established - the first failure had the corrupted config too.)

Build: 0 errors, 125 MHz setup +0.056 / hold +0.041, 50 MHz +2.352. 4LUT 50563 (54%), DFF 31032.
Programmed OK; Linux rebooted in ~25 s.
HARDWARE PROOF: registers at 0x6002_0000 read 0 (no bus fault); write 0xDEADBEEF to +0x10 reads
back 0xDEADBEEF, clears to 0. Reference design intact: mss-dma-uio bound at 60010000, LSRAM OK,
systemctl running, 0 faults in dmesg.
Register map (Chisel VCR): 0x00 control (write 1 = launch), 0x04 cycle counter, 0x08 insn count,
0x0c insn addr, 0x10.. DMA pointers.

## Linux-side bring-up: VTA executing on silicon (2026-09-14)

Driver: tvm-vta/src/mpfs/mpfs_driver.cc (no kernel module; /dev/mem + the reserved
non-cached pool at 0xC400_0000, 64 MiB, so cache maintenance is a no-op).

Three things had to be fixed before anything ran:

1. The DMA pool was not mappable from userspace. The kernel has CONFIG_STRICT_DEVMEM=y
   and CONFIG_IO_STRICT_DEVMEM=y with CONFIG_GENERIC_LIB_DEVMEM_IS_ALLOWED, whose check is
       if (iomem_is_exclusive(...)) return 0;   <- we failed here
       if (!page_is_ram(pfn))       return 1;   <- we would have passed here
   /proc/iomem shows "c4000000-c9ffffff : Reserved" as a TOP-LEVEL entry (not a child of
   System RAM), so page_is_ram() is already false for the pool; only the exclusivity test
   blocked it. "iomem=relaxed" clears strict_iomem_checks and short-circuits that test.
   No device-tree overlay and no kernel module are needed.
   Applied by editing U-Boot's mpfs_set_bootargs. uboot.env is a plain
   "4-byte LE CRC32 + NUL-separated key=value, 0xff padded" blob on the FAT boot
   partition; scratchpad/ubenv.py rewrites it (verified byte-exact round-trip first).
   Backup kept on the board as /mnt/boot/uboot.env.bak.
2. LLVM emitted soft-float-ABI objects that the lp64d cross linker refused to merge
   ("can't link soft-float modules with double-float modules"). TVM does expose -mabi, so
   environment.py now pins MPFS_LLVM_TARGET =
     llvm -mtriple=riscv64-unknown-linux-gnu -mattr=+m,+a,+f,+d,+c -mabi=lp64d
3. VTA_MAX_XFER defaults to 32 MB and the uop and insn queues each reserve that much up
   front - 64 MB, i.e. the entire pool, leaving nothing for tensors. VTA.cmake now sets
   4 MB for the mpfs target (the on-chip uop buffer is 32 KB, and the runtime flushes the
   insn queue when full, so this only changes flush frequency).

Also: the board's stock /etc/systemd/network/eth.network matches "eth*", which never
matches this board's end0, so it came up with no address at all; added 10-end0-static.network
(192.168.100.2/24). Host side is netplan 50-polarfire-board.yaml (eno1 192.168.100.1/24).
build_board_runtime.sh rebuilds + deploys the board runtime. It EXPORTS VTA_HW_PATH and
asserts the configured target is mpfs: cmake/modules/VTA.cmake silently falls back to
tvm/3rdparty/vta-hw (TARGET "sim") when VTA_HW_PATH is unset, which produces a libvta.so
with no mpfs driver in it. That trap cost an hour.

RESULTS ON HARDWARE (RPC to 192.168.100.2:9091, C++ tvm_rpc + LD_PRELOAD=libvta.so):
  hw_test.py mem                PASS  (DMA round-trip, 4 shapes)
  hw_test.py alu 1 / 2 / 4      PASS  576/576  (ACC load -> ALU shift -> OUT -> store)
  hw_test.py pad ... (2 cases)  PASS  560/560  (padded ACC load)
  hw_test.py gemm               FAIL  output is exactly zero, always

## The GEMM-returns-zero problem (open)

GEMM is deterministically all zeros on hardware: every run, every size (red=1/4/16), every
operand set. ALU and padded-load pass. What has been ruled out, and how:

- The schedule is right. The identical schedule passes in FSIM (hw_test.py and gemm_probe.py
  run against sim/tsim by pointing VTA_HW_PATH at a config whose TARGET is sim/tsim).
- The RTL is right. The same program passes in TSIM (cycle-accurate Verilator of our Chisel,
  including both timing fixes) - gemm_probe identity red=1 gives 64/64.
- The synthesized netlist is the validated RTL: md5 of the .sv imported into Libero equals
  hardware/chisel/build/chisel_fpga/VTA.DefaultPynqConfig.sv, generated in the same run as
  the TSIM model.
- It is not timing. Post-layout worst slack is +0.056 ns at 125 MHz, and the FIC clock domain
  has 0 unconstrained setup paths (45164 constrained). A marginal path would also not fail
  identically on every run.
- It is not a resource problem: Math 128 (same as standalone), LSRAM 241/308.
- The driver's launch sequence matches the reference TSIM driver exactly (0x08 insn count,
  0x0c insn addr, zero 0x10..0x20, then 0x00=1).
- The DMA reads really happen and move the right volume. Cycles for red=1/4/16:
      board  262 / 963 / 3657      tsim  244 / 639 / 2211
  Both scale linearly with weight volume; the board is ~1.7x slower per unit, which is what
  real DDR latency should cost against TSIM's ideal DPI memory.
- AXI IDs are fully wired on VTA's DMA path: VTA_0_m_axi_gmem_{AR,R,AW,B}ID are 8 bits and
  connect to DMA_INITIATOR MASTER1 in both directions (ID_WIDTH:8, MASTER1_DATA_WIDTH:64).
  (The RID tied to 9'h000 at FIC_0_PERIPHERALS.v:944 is inside FIC0_INITIATOR - the CPU
  control path, which is AXI4Lite and has no IDs - not the DMA path.)

What is left, and why it fits: GEMM is the first and only test that makes the LOAD module
(inp + wgt) run CONCURRENTLY with the COMPUTE module (uop + acc). The ALU tests use COMPUTE
alone, which is why they pass. VME multiplexes all of these read clients onto the single
m_axi_gmem port and demultiplexes responses by AXI ID: it tags each read with
io.mem.ar.bits.id (a tag-array index) and routes the data by io.mem.r.bits.id
(shell/VME.scala:280, 292, 297). Correct operation therefore REQUIRES the ID to round-trip
through CoreAXI4Interconnect -> FIC0 -> MSS -> DDR with more than one read in flight from
different clients. TSIM cannot see this: its Test top replaces XilinxShell with a DPI memory
model, so the AXI bridge is exactly the part that has never been exercised in simulation.

NEXT STEP: close that coverage gap in simulation rather than guessing - drive XilinxShell
(not the DPI shell) against an AXI4 memory model / protocol checker and exercise concurrent
inp+wgt+uop reads, checking that RID round-trips and that responses are not interleaved in a
way VME cannot handle. Only after that, rebuild the FPGA (a VME RequestQueueDepth of 1 would
serialize the clients and is both the diagnostic and a possible workaround, at a throughput
cost). Do not start a multi-hour Libero rebuild on a guess.

### CORRECTION (same day): it is a beat-ordering bug, and a failed GEMM wedges VTA

Two things above were measured wrong, because **a failed GEMM leaves VTA wedged**: every
later program returns garbage until the fabric is reset. ALU and pad, which passed earlier,
started failing after the GEMM runs and came back only after a reboot. So all the repeated
"deterministically exactly zero" GEMM measurements - and the red=1/4/16 cycle counts - were
taken from an already-broken accelerator. reset_board.sh reboots and waits for the RPC
service (now a systemd unit, vta-rpc.service) so a test can start from a known-good device.

Run ONCE from a clean reset, GEMM is not zeros at all. With a one-hot weight matrix each
output channel just selects the matching input channel, so the result reads directly as the
contents of the inp tensor:

    ref = [1, 2, 3, 4, 5, 6, 7, 8,  9,10,11,12,13,14,15,16]
    got = [9,10,11,12,13,14,15,16,  1, 2, 3, 4, 5, 6, 7, 8]

The two halves of the 128-bit tensor are SWAPPED. 16 int8 = 128 bits = two 64-bit beats on
this bus, so the tensor's two AXI read beats are being assembled in the wrong order. (The
first tile of the run shows a partially-populated variant - [0,0,0,0,-40,-40,0,0,1..8] -
i.e. the first beat is stale on the very first load.) Bit-for-bit reproducible across
resets.

Note what this does NOT affect: acc tensors are 16x32b = 512 bits = 8 beats and are
assembled correctly (ALU and padded-load are bit-exact), and uop loads are correct. So it is
specific to the 2-beat case, not a global beat-order or endianness error. TSIM misses it
because its DPI memory model returns beats back-to-back with no back-pressure, while real
DDR through CoreAXI4Interconnect -> FIC0 -> MSS returns them with gaps - and VME's read path
registers data, valid and the tag-array lookup in parallel (shell/VME.scala:289-297), which
is exactly the kind of thing that only desynchronises when beats are not contiguous.

So the earlier "concurrent LOAD + COMPUTE clients / AXI ID round-trip" theory is superseded:
the fault is in per-tensor beat assembly, not in client demultiplexing.

NEXT STEP (revised): simulate the narrow tensor load against an AXI model that inserts gaps
and de-asserts RVALID between beats, for a 2-beat tensor, and check the order in which beats
land in the scratchpad. That reproduces the bug without the board and without a Libero
rebuild. The wedging is a second, separate bug worth fixing regardless: a load that goes
wrong should not leave the dependency state unrecoverable.

### Wedging is not reliably cleared by a warm reboot

After GEMM has run, `systemctl reboot` does not dependably return VTA to a good state: a
Linux reboot does not necessarily re-assert the fabric reset, so VTA's internal state can
survive it. Observed afterwards: `mem` passes, then `alu` sometimes passes and sometimes
HANGS (no completion, the host call just blocks). It is intermittent, which is why the
"reset, test once" loop above still produced a mixture of PASS and hang.

To get a genuinely known-good accelerator, reprogram the FPGA (vta_prog.tcl) or power-cycle
the board, not just reboot Linux.

Driver improvement worth making: VTADeviceRun polls `wait_cycles` times and only then
LOG(FATAL)s. With the counts TVM passes, that is minutes of spinning and it presents as a
hang. It should use a wall-clock deadline and return an error, so a wedged device is
reported instead of blocking the RPC server.
