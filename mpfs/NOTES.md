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

## RETRACTION of the beat-ordering claim, and a real bug found (2026-09-14, later)

The "two 64-bit halves are swapped" finding above is WRONG. It was an artifact of a genuine
software bug that was corrupting the weight address, so the GEMM was multiplying by whatever
happened to be in the output buffer. Retained above only because the reasoning chain is
worth seeing; the conclusion is retracted.

### The real bug: VTA addresses DRAM in tensor elements, so buffers must be element-aligned

A load/store instruction's `dram_base` does not count bytes. It counts ELEMENTS of the
buffer's tensor type, and the runtime forms it by dividing the physical address by that
element size. At this configuration:

    wgt element = BLOCK_OUT * BLOCK_IN * WGT_WIDTH/8 = 256 bytes
    acc element = BATCH * BLOCK_OUT * ACC_WIDTH/8    =  64 bytes
    inp element = BATCH * BLOCK_IN  * INP_WIDTH/8    =  16 bytes
    out element = BATCH * BLOCK_OUT * INP_WIDTH/8    =  16 bytes
    uop element                                      =   4 bytes

A buffer not aligned to its own element size has the remainder silently truncated. Our
allocator aligned to 64 bytes, so a 512-byte weight buffer landed at ...0080 and the wgt
load addressed ...0000 - the OUTPUT buffer. Nothing warns; VTA just reads the wrong place.

This is exactly why ALU and padded-load passed while GEMM did not: those only use acc
(64-byte elements) and out (16-byte), both satisfied by 64-byte alignment. GEMM is the only
test that needs a wgt tensor. The other VTA ports never hit this because they allocate whole
pages through CMA.

Fixed by deriving the alignment from the VTA config (max element size, 256 here) instead of
a hardcoded 64. The driver cannot know which tensor a buffer will hold, so it has to align
every allocation to the largest element size.

How it was found: an env-gated instruction dump in the driver (VTA_MPFS_DEBUG=1) that prints
each load/store's dram_base converted back to a byte address, next to a log of every
allocation's physical address. Comparing the two columns made it obvious in one run. Worth
keeping - it is the only way to see what VTA was actually told to do.

### State after the fix

Addresses are now correct and verified independently: the wgt instruction points at
0xC4000200, and reading that DRAM from a separate process via devmem2 shows exactly the
one-hot pattern the host wrote (0x01 at byte c*16+c). So the entire software path - buffer
contents, physical addresses, instruction encoding - is confirmed good.

GEMM nevertheless still returns all zeros on a freshly reset device, while ALU and padded
load pass. So there is a second, independent fault, and it is in hardware: VTA is told the
right address, the right data is at that address, and the result is still zero. The earlier
eliminations still stand (schedule correct in FSIM, RTL correct in TSIM against a
byte-identical netlist, timing met and fully constrained, DMA volume scales correctly), and
the remaining uncovered surface is still the XilinxShell AXI bridge, which TSIM replaces
with a DPI memory model.

Also fixed: build_board_runtime.sh was starting its own nohup'd server, which fought with
vta-rpc.service for port 9091 - the loser silently binds 9092, so the host keeps talking to
a stale binary and tests appear not to respond to code changes. It now goes through
systemctl and asserts the server came up on 9091.

## The wedging is NOT GEMM-specific - marginal timing is back in play (2026-09-14, later still)

New observation that changes the diagnosis. From a fresh reset, with NO GEMM anywhere in the
sequence:

    alu 1  PASS
    alu 2  PASS
    alu 4  HANG      <- and everything after it hangs too

So VTA stops completing programs after a handful of runs on its own. Earlier in the day it
managed five in a row (alu 1/2/4 + two pads) before any GEMM had been issued, so the number
of programs before it wedges varies. That is not the signature of a logic bug in the GEMM
datapath; it is the signature of something marginal.

Which puts timing back in the frame, despite the clean report. Post-layout worst slack is
+0.056 ns on an 8 ns period - 0.7% margin - and the notes above already predicted this:
VTA standalone sat ON the 125 MHz edge across seeds (-0.155 to +0.067), and integration with
the reference design was expected to push it negative. A 0.7% margin does not survive IR
drop, jitter or delay-model error. On-die temperature during these runs is 62.8 C
(/sys/class/hwmon/hwmon0, mpfs_tvs), within the analyzed 0-100 C range, so temperature alone
does not explain it - but it does not need to, at that margin.

It also re-explains GEMM: GEMM lights up far more of the fabric (the MAC array plus the wgt
scratchpad) than an ALU shift does, so if the part is marginal, GEMM is what fails first and
every time, while ALU fails only occasionally.

RECOMMENDED NEXT STEP, revised: rebuild the integrated design with VTA on a slower clock -
Plan A from the integration notes, 100 MHz on its own FIC clock (each MSS FIC has an
independent fabric clock, so no CDC work is needed). If GEMM then computes correctly and
long ALU sequences stop wedging, this was timing all along and the AXI-bridge theory can be
dropped. That is a much cheaper experiment than building an AXI testbench, and unlike the
earlier situation the evidence now points at it. The one caveat is that it is a multi-hour
Libero run.

Keep in mind when interpreting any future hardware result: every measurement taken after a
program has wedged the device is worthless. Reset first (reset_board.sh), and treat only the
first program after a reset as trustworthy.

## Rebuilding with VTA at 100 MHz in its own clock domain (2026-09-14)

Approach: VTA gets a dedicated PLL at 100 MHz and its two AXI ports cross back into the
125 MHz FIC0 domain inside CoreAXI4Interconnect, which does the CDC itself when a port has
CLOCK_DOMAIN_CROSSING enabled. FIC0, the MSS and the rest of the reference design are
untouched and still run at 125 MHz.

Why not simply retune a FIC clock, which would have been a one-line change:
MSS_WRAPPER ANDs the lock outputs of all four MSS FIC DLLs into MSS_DLL_LOCKS
(MSS_WRAPPER.tcl:676-680), and CLOCKS_AND_RESETS feeds that into the EXT_RST_N of every
CORERESET (CLOCKS_AND_RESETS.tcl:100-102). So if any FIC DLL fails to lock at the new
frequency, the ENTIRE fabric is held in reset - including the path we use to reach VTA. The
MSS has no explicit FIC frequency setting to check against; it just locks a DLL to whatever
the fabric supplies. Not worth the risk for a change that only needs to affect VTA.

FIC_2 initially looked like a free ride - its AXI interface is explicitly marked unused
(MPFS_DISCOVERY_KIT.tcl:185) and its 125 MHz clock drives nothing but a dead MSS port - but
it is caught by the same DLL-lock AND, so retuning it carries exactly the same risk.

This is the arrangement Microchip already uses for VectorBlox in this reference design
(script_support/additional_configurations/Vectorblox): a second CCC, its own CORERESET, and
a CoreAXI4Interconnect instance with MASTER0_CLOCK_DOMAIN_CROSSING:true as the bridge.

Changes, all confined to the VTA integration:
  VTA_CCC.tcl        new PLL, 50 MHz ref -> 100 MHz on GL0. Derived from Microchip's
                     VectorBlox PF_CCC_C1 because that is a known-good 100 MHz config
                     (1200 MHz VCO, GL0 divider 12, feedback 24). GL1-GL3 IS_USED false.
  DMA_INITIATOR      MASTER1_CLOCK_DOMAIN_CROSSING true  (VTA's DMA master port)
  FIC0_INITIATOR     SLAVE2_CLOCK_DOMAIN_CROSSING  true  (VTA's control port)
  vta_integrate.tcl  instantiates VTA_CCC + a second CORERESET inside FIC_0_PERIPHERALS,
                     drives VTA's ap_clk and both crossings' fabric-side clocks
                     (DMA_INITIATOR:M_CLK1, FIC0_INITIATOR:S_CLK2) from the PLL, and brings
                     the board's 50 MHz oscillator down from the top level as VTA_REF_CLK.
  vta_clocks.sdc     declares the VTA and FIC0 clocks asynchronous, so the tools do not try
                     to time through the CDC synchronizers.

Tcl gotchas hit on the way, all of which cost a build iteration:
  - GLx_0_OUT_FREQ:0 does NOT disable a CCC output; frequencies must be 1-1250 MHz. The
    parameter is GLx_0_IS_USED, and the dividers (GLx_0_DIV) are explicit, so an arbitrary
    frequency is not reachable from an arbitrary VCO - copy a known-good config instead.
  - This CCC configuration exposes no PLL powerdown input, so CORERESET's PLL_POWERDOWN_B
    has to be marked unused (VectorBlox does the same).
  - After adding a port to a sub-design, the parent needs both a regenerate and an
    sd_update_instance before the new port is visible; and a top-level port that is already
    connected cannot be connected again - join the net by naming a pin already on it.
  - derive_constraints_sdc needs build_design_hierarchy + set_root after the top component
    is regenerated, and organize_tool_files only accepts files already imported into the
    project (import_files, not create_links).
  - MPFS_DISCOVERY_KIT_REFERENCE_DESIGN.tcl only OPENS an existing project (line 117), so a
    leftover project silently rebuilds the previous design or fails with "a core already
    exists". full_cycle.sh now removes the project first; iterate_integrate.sh keeps a
    pristine base copy so the integration Tcl can be iterated without regenerating it.

The previous working 125 MHz build (project + .ppd + timing report) is kept in
../refdesign-vta-125mhz-backup, so the board can always be put back to a known state.

## The 100 MHz build is correct but the board will not boot with it (2026-09-14)

Build results were excellent: VTA domain worst slack +1.670 ns at 100 MHz (16.7% of the
period, against +0.056 ns at 125 MHz), FIC0 improved to +3.054 ns because VTA is no longer
the critical path in that domain, no violations, resources exactly as expected (PLL 1->2,
globals 9->10, uSRAM +33 for the CDC FIFOs, LSRAM 241 and Math 128 unchanged).
Programming reported PROGRAM PASSED / Chain programming PASSED.

But the board does not boot. HSS gets as far as

    HSS: decompressing from eNVM to L2 Scratch ... Passed
    wdog_service monitoring [u54_1] [u54_2] [u54_3] [u54_4]
    beu_service :: [init] -> [monitoring]
    Initializing Mi-V IHC V2          <- stops here, then watchdog-resets, repeatedly

where a good boot continues "u54 State Change: [Idle]..." within 50 ms. JTAG is fine
(reprogramming still passes), so the device is powered and alive - it is the design.

What has been ruled out by diffing the two generated netlists as SETS of connections
(ordering churn from regeneration makes a plain diff useless):
  - Top level: the ONLY difference is the added .VTA_REF_CLK(REF_CLK_50MHz). MSS, FIC3,
    CLOCKS_AND_RESETS and every other connection are identical.
  - FIC_0_PERIPHERALS: the only differences are exactly the intended ones - VTA_CCC,
    VTA_RESET and its constant ties, M_CLK1/S_CLK2 on the new clock, and VTA's ap_clk/
    ap_rst_n moved to the new domain. Nothing unintended changed.
  - No CCC placement, routing or clock-resource warnings in the build log.

Why the hang looks like a clocking/reset problem rather than anything to do with VTA:
Mi-V IHC is not on FIC0 at all - it lives in FIC_3_PERIPHERALS on the APB bus - so HSS is
hanging on its first access to a FABRIC peripheral. FIC3's PRESETN comes from
RESET_FIC_3_CLK, whose EXT_RST_N is the AND that includes MSS_DLL_LOCKS, which is the AND of
all four MSS FIC DLL locks. If any FIC DLL fails to lock, every fabric peripheral stays in
reset and the first access to one hangs the E51 until the watchdog fires - exactly the
observed loop.

The only thing the change touches outside FIC_0_PERIPHERALS is the REF_CLK_50MHz input pad,
which now fans out to a second CCC instead of one. That is the leading suspect: it is the
sole way this change could affect the main CCC and the FIC clocks derived from it.

Next attempt: reference the VTA PLL from an existing clock global (e.g. FIC_0_CLK) instead
of the REF_CLK pad, leaving the pad's dedicated route to the main CCC exactly as it was.
The alternative suspect, if that does not fix it, is the CDC on FIC0_INITIATOR slave port 2
stalling the interconnect while VTA's domain is still in reset - but HSS is not known to
touch FIC0 during boot, so it explains the symptom less well.

Recovery: the known-good 125 MHz project was restored from ../refdesign-vta-125mhz-backup.
Libero refuses to program a project whose flow state is stale ("SYNTHESIZE Tool inputs are
out of date"), and moving a project directory makes it stale, so restoring means re-running
synthesis/P&R/programming-data even though the design is unchanged - budget for that.
The 100 MHz project is kept as MPFS_DISCOVERY.100mhz.

## 100 MHz attempt 2: same hang, so the CLKINT/pad theory was WRONG

v2 referenced VTA's PLL from FIC_0_CLK instead of the REF_CLK_50MHz pad. Verified before
building: the top-level netlist was connectivity-IDENTICAL to the working design (the v1
diff had shown the added VTA_REF_CLK; v2's diff is empty), no CLKINT_REF_CLK_50MHz in the
timing report, and timing was even better than v1 - VTA +1.853 ns at 100 MHz, FIC0 +3.280 ns.
The PLL config was DRC-validated in 90 seconds first (125 MHz / REFDIV 5 -> 25 MHz PFD,
feedback 48 -> 1200 MHz VCO, GL0 divider 12; Libero derives multiply_by 4 divide_by 5).

The board still does not boot, with exactly the same signature: HSS reaches
"Initializing Mi-V IHC V2" and stops. So the reference-clock pad was not the cause, and the
CLKINT was a red herring.

What is left, all inside FIC_0_PERIPHERALS and common to both attempts:
  1. a second fabric PLL (VTA_CCC) and a second CORERESET
  2. CDC enabled on FIC0_INITIATOR slave 2 and DMA_INITIATOR master 1
  3. VTA clocked at 100 MHz instead of ACLK

Both attempts added a PLL that takes an existing critical clock as its reference (the pad in
v1, FIC_0_CLK in v2). A CCC reference input is not an ordinary fabric load - it has to reach
the CCC's dedicated input - so it is still the prime suspect, but that is now a hypothesis
without evidence, and guessing has cost two build cycles.

### Recovery is cheap if you COPY rather than MOVE

Restoring the working project with `cp -a` (timestamps preserved) let Libero program it
directly - about 3 minutes. Restoring by `mv` earlier made the flow stale
("SYNTHESIZE Tool inputs are out of date") and forced a full 40-minute rebuild. Always keep
a `cp -a` copy of a known-good project and restore it the same way.

### Next: stop guessing, remove the PLL entirely

The design has four existing clocks: FIC_0/1/2 at 125 MHz and FIC_3 at 50 MHz. None is
100 MHz, so keeping 100 MHz REQUIRES a new PLL - the very thing under suspicion. Borrowing
FIC_3_CLK (50 MHz) needs no PLL and no new CORERESET, just an extra fabric load on a global
that is already routed, and FIC_3's MSS DLL is DISABLED (FIC_3_EMBEDDED_DLL_USED false), so
perturbing that clock cannot break the MSS_DLL_LOCKS chain that gates every fabric reset.
That makes it the safest clock in the design to borrow.

50 MHz costs throughput but gives VTA a 20 ns period against the 8 ns it was failing at, and
it is decisive either way: if the board boots and VTA behaves, the PLL was the problem and
the timing hypothesis is confirmed; if it still hangs, the culprit is the interconnect CDC,
which is the only remaining change.

## Attempt 3 (50 MHz, no PLL): same hang - the culprit is the interconnect CDC

Borrowed FIC_3_CLK (50 MHz) with NO new PLL and no new CORERESET. Timing was comfortable
(VTA domain +4.147 ns on a 20 ns period, FIC0 +3.806 ns). Same failure: HSS stops at
"Initializing Mi-V IHC V2" and watchdog-loops.

Three attempts, three different clock arrangements, one identical failure:
  v1  new PLL referenced from the REF_CLK_50MHz pad      -> hang
  v2  new PLL referenced from FIC_0_CLK                  -> hang
  v3  no PLL at all, borrowed FIC_3_CLK                  -> hang

So neither the PLL nor the reference-clock pad was ever the cause. Both earlier theories
are retracted.

A component-by-component netlist comparison against the working build settles what is left.
Exactly four components differ, all of them intended:
  DMA_INITIATOR      MASTER1_CLOCK_DOMAIN_CROSSING 0 -> 1, M_CLK1 GND -> M_CLK1
  FIC0_INITIATOR     SLAVE2_CLOCK_DOMAIN_CROSSING  0 -> 1, S_CLK2 GND -> S_CLK2
  FIC_0_PERIPHERALS  VTA's clock/reset and the two crossing clocks
  MPFS_DISCOVERY_KIT the two taps onto the existing FIC_3 clock and reset nets
MSS, FIC_3_PERIPHERALS (which contains the Mi-V IHC that HSS hangs on) and
CLOCKS_AND_RESETS are byte-identical in connectivity. The build flow is not corrupting
anything, and the constraint sets match too.

=> Enabling CLOCK_DOMAIN_CROSSING on a port of the SHARED system interconnects is what
breaks the design.

### The likely mistake: where the CDC belongs

Re-reading what Microchip actually does for VectorBlox: they do NOT enable CDC on a port of
the main FIC0 interconnect. They instantiate a SEPARATE CoreAXI4Interconnect
(vectorblox_axi_resize) as a dedicated 1x1 bridge with MASTER0_CLOCK_DOMAIN_CROSSING:true,
sitting between the accelerator and the rest of the system, with its own ACLK and M_CLK0.
The system interconnects stay single-clock.

I copied the parameter but not the topology: CDC went onto FIC0_INITIATOR and DMA_INITIATOR,
the interconnects that carry ALL the MSS traffic to FIC0. If enabling CDC on one port
disturbs that interconnect's reset or ready behaviour, everything behind it stalls, which is
consistent with the MSS hanging on its first fabric access.

Next step if this is pursued: leave FIC0_INITIATOR and DMA_INITIATOR untouched (CDC off) and
put a dedicated CoreAXI4Interconnect bridge instance on each side of VTA to do the crossing,
exactly as VectorBlox does.

### Cost so far

Three full build+program cycles (~1 h each) to eliminate two wrong theories and land on the
right suspect. Recovery is now cheap (cp -a the known-good project, program, ~3 min), and
MPFS_DISCOVERY.{100mhz,100mhz.v2,50mhz} are kept for comparison.

## #3 done (timeout + diagnostics); #2 investigated, NOT fixed (2026-09-14)

### Delivered: VTADeviceRun no longer hangs

It waited on the caller's spin count, which TVM sets huge, so a device that never reported
completion blocked for minutes and looked like a hang. It now uses a wall-clock deadline
(VTA_MPFS_TIMEOUT_MS, default 10 s), spins briefly for short programs then backs off with a
200 us sleep (polling the control register is itself AXI traffic competing with VTA's DMA),
and on expiry dumps the whole register window plus the cycle count before/after, then
returns 1. runtime.cc does CHECK_EQ(timeout, 0), so that surfaces as a clean error on the
host instead of a hang. Verified: it fired correctly on every wedge observed.

### What the wedge actually looks like

With the timeout in place, at the moment of failure:
    regs: 0x00=0x00000001 0x04=0x00000271 0x08=0x00000007 0x0c=0xc4400c00 0x10..0x20=0
ctrl bit0 (launch) still set, bit1 (finish) never set; insn count and address correct; and
the cycle count UNCHANGED from before the launch. Since the VCR only latches that register
when the core pulses its event count at the end of a run, the core never reported a
completion - it did not execute that run at all. The wedge is sticky: every subsequent run
fails until the fabric is reset.

### A wrong conclusion, and the flaw that produced it

I briefly concluded "the program completes and only the finish handshake is lost", because
the output buffer held the correct result after a failed run. That was an artifact of the
test: it ran the SAME program with the SAME inputs every iteration, so the buffer still held
the previous run's correct output. wedge_stress.py now varies the input each iteration and
pre-fills the output with a sentinel, which distinguishes "ran" from "never started". The
cycle-count evidence above contradicts the original claim and is the one to trust.

### No validated fix - and the attempts that looked like one

  - Adding a device read before the launch write: first looked decisive (1500 clean after
    failures at 2-6). Then it failed at iteration 166, and a controlled A/B under the same
    harness showed the SAME behaviour with and without it. Not a fix.
  - "fence iorw,iorw" instead of __sync_synchronize(): does not fix it either (failed at
    iteration 10 in testing). Kept anyway, because MMIO ordering on RISC-V genuinely needs
    the I/O bits and "fence rw,rw" does not set them - but it is correctness hygiene, not a
    remedy.

The wedge then stopped reproducing entirely: 16,000+ consecutive executions clean across
both driver variants and both harnesses, where it previously failed within 2-6 runs. Die
temperature was 63.6 C during the clean runs versus 62.8 C during the failing ones, so
thermal drift does not explain it either. The variable that changed is not identified.

State: real, observed many times, sticky, currently not reproducible, cause unknown. Do not
record it as fixed. The next person should use wedge_stress.py (which now has an honest
oracle) to re-establish a reproduction before trying anything, and should be suspicious of
any fix "confirmed" by a few hundred clean iterations - the failure rate varies by at least
two orders of magnitude between sessions.

## #1 (GEMM zeros): hypotheses eliminated, and TSIM's coverage is much weaker than assumed

Re-verified first: GEMM still returns all zeros on a freshly booted board with the alignment
fix in place, and the output sentinel is overwritten, so the store happens and the MAC
genuinely produces zero.

Eliminated this session, cheaply and with evidence:

  - Module concurrency. vta.build_config(debug_flag=32) = VTA_DEBUG_FORCE_SERIAL rewrites
    the instruction dependencies so LOAD/COMPUTE/STORE never overlap. GEMM still returns
    zeros, so the LOAD-vs-COMPUTE overlap that GEMM uniquely creates is NOT the cause.
    (gemm_probe.py now takes VTA_DEBUG=<flags>.)
  - A different instruction stream. Dumped both: TSIM executes
    LOAD UOP / GEMM / LOAD INP / LOAD WGT / LOAD UOP / GEMM / STORE / NOP / NOP / FINISH,
    identical to the driver's dump on hardware. Software is exonerated end to end - same
    program, same addresses, correct data in DRAM, correct alignment.
  - A different load implementation for inp/wgt vs acc. TensorLoad picks by
    mp.dataBits >= tp.tensorSizeBits; at 64-bit bus, inp (128b), wgt (2048b) AND acc (512b)
    all use TensorLoadNarrowVME. acc works, inp/wgt do not, but it is the same module.
  - Gapped read beats. Added VTA_TSIM_RD_GAP to the DPI memory model so it delivers a beat
    only every Nth cycle instead of every cycle. GEMM still passes in TSIM at gaps of 2 and 4.

### The finding that matters: TSIM never tests the memory path VTA actually uses

The DPI memory model held ONE outstanding read in a single (addr, len, id) triple that each
new request overwrote. Extending it to a proper queue and instrumenting the depth shows
what VTA does under TSIM:

    TSIM memory: outstanding reads reached 1 (ids in flight: 0)

One read at a time, tag 0, always. So VME's tag array and ID-based response demultiplexing
(VME.scala:280-297) - the logic that decides which client each read response belongs to -
has NO simulation coverage whatsoever, because the DPI shell serializes requests. On
hardware VME can have up to RequestQueueDepth (16) reads in flight across fetch, load and
compute clients, and demultiplexes them by AXI ID.

Every earlier "the RTL is correct, TSIM passes" claim in these notes should be read with
that caveat: TSIM validates the compute pipeline, not the memory interface.

Relatedly, on VTA's DMA path the ID is truncated 9 -> 4 bits at the MSS boundary
(MPFS_DISCOVERY_KIT.v: ARID_0_3to0 = ARID[3:0], and RID_0_8to4 forced to 5'h0), dropping the
bit CoreAXI4Interconnect appends to identify which master issued the transaction. VTA's own
tags are 0-15 so they survive, and whether the interconnect needs that bit for response
routing (or tracks it internally) is not established - but it is the kind of thing that
would break inp/wgt loads while leaving simpler traffic working.

### Model improvements committed (src/dpi/module.cc)

  - multiple outstanding reads (a deque instead of one overwritten triple)
  - VTA_TSIM_RD_GAP=N   deliver a read beat only every Nth cycle
  - VTA_TSIM_RD_OOO=1   complete whole bursts newest-first (legal AXI4: bursts may complete
                        out of order, though beats of different transactions may not
                        interleave)
  - logs the maximum outstanding-read depth and the ids in flight

With the shell as it is, RD_OOO is vacuous - the queue never exceeds one entry. Making it
meaningful needs the Verilog side (VTAMemDPI.v and the VME-to-DPI adapter) to accept a new
read request before the previous one completes. That is the next concrete step for #1: with
it, the ID demux can be tested in simulation, and if it breaks there we have the hardware
bug reproduced without an FPGA build.

## ============ STATUS AT THIS POINT (2026-09-15) ============

### What works on hardware
  hw_test.py mem                DMA round-trip through the reserved non-cached pool
  hw_test.py alu 1 / 2 / 4      ACC load -> ALU shift -> OUT -> store, bit-exact (576/576)
  hw_test.py pad (2 cases)      padded ACC load, bit-exact (560/560)
  wedge_stress.py 2000          2000 back-to-back executions, varying inputs, all correct

### What does not work
  1. GEMM returns all zeros. THE blocker: conv2d and dense are GEMM, so no DNN layer runs.
  2. Intermittent wedge. Real, observed many times, sticky until a fabric reset, but has not
     reproduced in 16,000+ executions since; cause unknown, no validated fix.
  3. (done) Driver hangs -> now a wall-clock timeout with a register dump.
  4. (parked) VTA on a slower clock - blocked on the CDC topology, and NOT on the critical
     path for 1 or 2.

### Fixed this session
  - DMA buffers must be aligned to the VTA tensor ELEMENT size (256 B for wgt here), not 64.
    Misalignment silently truncated the weight address to the output buffer. Real bug,
    independent of everything else, would have corrupted any weight-using workload.
  - VTADeviceRun wall-clock timeout + register diagnostics instead of an unbounded spin.
  - Board bring-up chain: iomem=relaxed, rv64gc/lp64d ABI pinning, VTA_MAX_XFER sizing.

### Hypotheses for #1 (GEMM zeros), with the evidence

  RULED OUT
    Schedule wrong .............. same schedule passes in FSIM
    RTL compute wrong ........... passes in TSIM on a netlist md5-identical to the synthesized
                                  one - but see the caveat below, this covers compute only
    Instruction stream differs .. dumped both; TSIM and hardware execute identical streams
    Wrong addresses ............. driver dump: wgt -> 0xC4000200, matching the allocation
    Wrong data in DRAM .......... read back from a separate process: exact one-hot pattern
    Store never happens ......... output sentinel is overwritten with zeros
    Buffer misalignment ......... found and fixed; GEMM still zero afterwards
    Timing ...................... met, and fully constrained
    Module concurrency .......... VTA_DEBUG_FORCE_SERIAL does not fix it
    Gapped read beats ........... VTA_TSIM_RD_GAP=2,4 still pass in TSIM
    Different load module ....... inp, wgt AND acc all use TensorLoadNarrowVME

  OPEN, most likely first
    H1. VME's ID-based response demultiplexing. VME tags each read with ar.bits.id and routes
        the data by r.bits.id (VME.scala:280,292,297). Under TSIM this is never exercised:
        max outstanding reads = 1, tag always 0. On hardware up to 16 can be in flight across
        fetch/load/compute. This is the only major block of logic with zero coverage, and it
        sits exactly where inp/wgt loads would break while simpler traffic survives.
    H2. AXI ID truncation in the integration. VTA's DMA-path ID is cut 9 -> 4 bits at the MSS
        boundary (ARID_0_3to0 = ARID[3:0], RID_0_8to4 = 5'h0), dropping the bit
        CoreAXI4Interconnect appends to identify the issuing master. VTA's own tags (0-15)
        survive, so this only bites if the interconnect needs that bit on the return path.
        Related to H1 and testable on the same rig.
    H3. Something else in XilinxShell's AXI bridge. TSIM replaces the whole shell with a DPI
        model, so nothing in it is simulated.

  Note H1/H2/H3 all live in the same untested region: the memory interface. That is why the
  next step is to give TSIM the ability to exercise it, rather than another FPGA build.

### Next step (in progress)
  Make the Verilog DPI shell accept a new read request before the previous one completes, so
  multiple reads with distinct tags are in flight. Then VTA_TSIM_RD_OOO (already in the C++
  model) becomes meaningful and H1/H2 can be tested in simulation. If GEMM breaks there, the
  hardware bug is reproduced with no FPGA build in the loop.

## H1 and H2 RULED OUT: VTA issues one read at a time (2026-09-15)

Built the thing the previous entry proposed: VTAMemDPIToAXI now has a multi-outstanding mode
(elaborate with VTA_TSIM_MULTI_RD=1) that hands every queued AR to the DPI as it arrives
instead of serving one burst at a time, with per-id beat counters for r.last. One trap worth
recording: the C++ model resolves rd_req_addr through the virtual memory manager whenever it
is non-zero WITHOUT checking rd_req_valid, so the request address must be forced to 0 when
not issuing - the in-order path never hit this because it drove a held register, and the
first version aborted the simulation on a bogus address.

With that in place, and even with the memory model slowed to one beat every 32 cycles:

    TSIM memory: outstanding reads reached 1 (ids in flight: 0)

VTA never has more than ONE read outstanding, whatever the memory latency. So for this
workload:

  H1 (VME's ID-based response demultiplexing) - RULED OUT. With a single transaction in
     flight and a single tag, there is nothing to demultiplex. The logic is still untested,
     but it cannot be what breaks GEMM.
  H2 (the 9->4 bit AXI ID truncation at the MSS boundary) - RULED OUT for the same reason.
     Response routing is unambiguous with one outstanding transaction.

Burst shapes, logged per request, also fail to separate the working case from the broken one:

    GEMM: 20 beats (insn), 1 (uop), 4 (inp = 2 tensors x 2), 64 (wgt = 2 x 32), 1 (uop)
    ALU:  14 beats (insn), 256 (acc), 32, 1

All legal AXI4, all correctly sized for their tensors, none crossing a 4 KB boundary - and
the ALU case that WORKS on hardware issues a bigger burst (256 beats) than anything GEMM
does. So burst length is not the discriminator either.

That leaves H3: something in XilinxShell's AXI bridge itself, which TSIM replaces wholesale
with the DPI shell and therefore never simulates. Narrowing it further from the host side
looks exhausted; the honest next step is hardware observation (Libero SmartDebug live probes
on the VME/AXI signals, or an ILA on m_axi_gmem) rather than more simulation.

Note: tvm-vta/build/libvta_hw.so is currently built WITH VTA_TSIM_MULTI_RD=1 (it passes
GEMM and ALU in TSIM). Rebuild without the variable for stock behaviour; the Makefile does
not track Scala changes, so delete build/chisel, build/verilator and build/libvta_hw.so
first, and sbt needs JDK 11 plus tools/sbt on PATH (see the top of this file).

## Tightest localization yet: bias_probe.py (2026-09-15)

Every earlier comparison was across DIFFERENT programs - an ALU program that works versus a
GEMM program that does not - which leaves open that the program, not the datapath, is what
differs. bias_probe.py closes that: one instruction stream computing

    y = x . w^T + bias

where x/w are loaded by the LOAD module and consumed by the MAC array, and bias is loaded
into the ACC scratchpad by the COMPUTE module and added with an ALU op. Validated in FSIM
first (bias + product, exactly right), so a hardware result is unambiguous.

On hardware, red=1:

    == bias only (GEMM contributed nothing) : True
    tile[0,0] expected [101,102,...,116]
    tile[0,0] got      [100,100,...,100]

So within ONE program: the ACC load works, the ALU works, the store works, and the MAC array
contributes EXACTLY zero - a clean zero, not garbage. That narrows the fault to inp/wgt
reaching the MAC: either the LOAD module's scratchpad writes, or the MAC array itself.

Note the structural point this raises: acc's TensorLoad is instantiated inside COMPUTE while
inp's and wgt's are instantiated inside LOAD. Same module, different instances - so the
working and broken paths differ by which parent instantiates them, not by the module code.

### GEMM at red=4 wedges the device

red=4 does not return zeros - it times out, with the same signature as the #2 wedge
(ctrl=0x1, cycle count unchanged, insn count and address correct):

    no completion after 10000 ms (insn_count=23, insn at 0xc4400b00). cycles 1129 -> 1129

That is the first thing all session to wedge the device on demand rather than by luck, which
makes it a candidate reproduction for #2 - and suggests #1 and #2 may share a cause after
all. It needs confirming across several resets before being relied on; two attempts gave
mixed results and the third could not be read because the board fell over (below).

### Board damage and recovery - a caution about the reset loop

Repeated hard resets left the board booting into emergency maintenance mode
("Give root password for maintenance"), with systemd-fsck-root and systemd-growfs-root
failed. e2fsck -fy on /dev/mmcblk0p3 found NOTHING to fix, so the filesystem was clean and
the emergency boot most likely cascaded from growfs failing (the partition is already at
maximum size since we grew it - that service cannot succeed again).

Recovery: log in on the serial console with the maintenance password, remount ro, fsck,
reboot -f. Then, because a Linux reboot does not reset the fabric, the device was STILL
wedged (mem passes - it never launches VTA - while alu hangs); reprogramming the FPGA
cleared it and all tests pass again.

Lesson for the next person: reset_board.sh reboots Linux, which is NOT enough to clear a
wedged accelerator, and hammering it risks the rootfs. To get a genuinely clean device,
reprogram the FPGA (~2 min, and it is what actually resets the fabric).

## MAJOR REVISION: GEMM is not fundamentally broken (2026-09-15)

After recovering the board from emergency mode and REPROGRAMMING the FPGA, GEMM computed
correctly - repeatedly, at every size tried:

    bias_probe red=1,2,3,4,8 (2x2 tiles)   all "bias + product (all correct) : True"
    gemm_probe identity red=1               64/64 correct

That last one is the exact probe that returned all zeros every time all session. So the
"GEMM returns zeros" symptom is NOT a logic bug in the GEMM datapath: the same bitstream,
the same program and the same driver produce correct results in some sessions.

What this invalidates: every GEMM measurement taken earlier in the session was made on a
device that had been left in a bad state by a previous program, because reset_board.sh only
reboots LINUX and the fabric is not reset by that. The conclusion that "the MAC array
contributes exactly zero" was a real observation of a degraded device, not of the design.

### But it is not reliable either

Subsequent programming sessions are bad again: on a freshly reprogrammed fabric, 2x2 GEMM
failed 10/10 (die temperature 57.2 C, i.e. COOLER than the 63.6 C during the good runs, so
a simple thermal explanation does not fit). Within a bad session the failure is uniform -
the GEMM contributes exactly zero to every output tile - and running a larger GEMM appears
to push a good session into a bad one.

So the behaviour varies per programming session, not per run:
    some sessions   GEMM correct at every size tried
    most sessions   GEMM contributes zero, uniformly
    ALU / pad / mem correct in every session observed

### What that means

Correct logic (FSIM and TSIM pass), correct software (instruction stream, addresses and DRAM
contents all verified byte-for-byte), simple traffic reliable, complex traffic
session-dependent, on a design with +0.056 ns of post-layout slack at 125 MHz - 0.7% of the
period. That is a physical marginality, not a functional bug, and it is consistent with
everything observed including the intermittent wedge (#2): the GEMM datapath and the MAC
array light up far more of the fabric than an ALU shift does.

It also means #1 and #2 are most likely the SAME root cause, which is why neither could be
pinned down as a logic error.

### Next step

Give VTA real timing margin - the work that was started and abandoned. The three failed
attempts all made the same mistake: CLOCK_DOMAIN_CROSSING was enabled on ports of the SHARED
system interconnects (FIC0_INITIATOR, DMA_INITIATOR), which carry all MSS traffic, and the
board stopped booting every time regardless of the clock source. Microchip's own VectorBlox
integration in this reference design does it differently: a SEPARATE CoreAXI4Interconnect
instance acts as a dedicated 1x1 CDC bridge, and the system interconnects stay single-clock.
Redo it that way, then VTA can run at 100 MHz (or 50) with the margin it needs.

Do NOT trust any hardware measurement that was not taken on a freshly REPROGRAMMED fabric.
reprogram_board.sh does that and waits for the board; reset_board.sh (Linux reboot) does not
reset the fabric and is not sufficient.

## The dedicated-bridge topology is blocked too (2026-09-15)

Built the VectorBlox arrangement: two 1x1 CoreAXI4Interconnect instances as dedicated CDC
bridges (VTA_DMA_BRIDGE, VTA_CTRL_BRIDGE), with CLOCK_DOMAIN_CROSSING reverted to false on
FIC0_INITIATOR and DMA_INITIATOR so the shared system interconnects stay single-clock.

The DMA-side bridge configures and connects without trouble. The CONTROL side does not:
SmartDesign rejects FIC0_INITIATOR:AXI4mslave2 <-> VTA_CTRL_BRIDGE_0:AXI4mmaster0 as "not
compatible", and it survived every fix:

  - the core caps ID_WIDTH at 8 (9, 10, 12 and 16 are all rejected as illegal), while
    FIC0_INITIATOR's slave ports emit ID_WIDTH + NUM_MASTERS_WIDTH = 9 bits;
  - setting FIC0_INITIATOR's NUM_MASTERS_WIDTH to 0 (legitimate - it has NUM_MASTERS:1) does
    bring its ARID down to 8 bits, and then the signal sets and widths match exactly
    (ARADDR 38, ARID 8, ARLEN 8, ARUSER 1, WDATA 32);
  - an AXI4Lite (TYPE:1) port on the bridge's master side has NO ID signals at all while
    FIC0_INITIATOR's Lite slave port DOES, so the types were matched as full AXI4 (TYPE:0)
    on both sides.

Widths, signal sets and types all agree and it is still refused. Cause not identified.

Also worth knowing: generating a component into MPFS_DISCOVERY.base (the pristine copy that
iterate_integrate.sh restores from) poisons every later run with "the folder ... already
exists". Keep the base copy clean.

The design is left in the known-good topology - VTA connected directly to both interconnects
and clocked from ACLK at 125 MHz - and integration verified to run clean end to end. The
bridge configs are kept in script_support/additional_configurations/vta/ for whoever picks
this up.

### Recommendation: stop trying to lower the clock, re-time the RTL instead

Four attempts at giving VTA its own clock have now failed at the Libero integration level,
for three different reasons. The goal was only ever more timing margin. That can be had
without touching the integration at all, by pipelining VTA's critical path so 125 MHz closes
comfortably:

  - the remaining critical path is known and internal to VTA:
    compute/loadUop/tensorLoad/vmeCmd/decR -> cmdGen/rdCmdStartIdx, ~30 logic levels,
    +0.056 ns slack;
  - it is our own Chisel, so it can be changed and validated in TSIM before any FPGA build;
  - the design stays single-clock: no CDC, no interconnect changes, no boot risk, and none
    of the three failure modes hit so far can recur.

That is the next thing to try.

## Multi-pass place-and-route buys nothing - P&R is exhausted (2026-09-16)

Ran PLACEROUTE with MULTI_PASS_LAYOUT, 5 seeds, STOP_ON_FIRST_PASS:false, EFFORT_LEVEL:true,
ranked by the slack of VTA's own clock domain. Per-seed worst slack on
CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0:

    seed 1  -0.413
    seed 2  -0.052
    seed 3  +0.043   <- best, saved as MPFS_DISCOVERY_KIT_r1_s3
    seed 4  -0.038
    seed 5  +0.002

Best of five is +0.043 ns, against +0.056 ns from a single DEFAULT pass. So more placement
effort does not buy margin here - the design is at its structural limit at 125 MHz, and the
seed-to-seed spread (0.46 ns) dwarfs the margin itself. Note most seeds FAIL timing; the
default single pass got lucky.

The resulting bitstream does not boot the board at all (both consoles silent), so it was not
even usable as a placement-variation experiment. Restored the known-good design; mem and
alu pass again.

Two process notes from this run, both of which cost time:
  - MULTI_PASS_CRITERIA has legal values SLOWEST_CLOCK, SPECIFIC_CLOCK, VIOLATIONS and
    TOTAL_POWER. TIMING is NOT legal. SLOWEST_CLOCK ranks by frequency and would pick the
    50 MHz FIC3 domain, so name the FIC0 clock with SPECIFIC_CLOCK.
  - Libero writes its logfile only when the script FINISHES, and exits 0 even when a step
    failed. A build that died at configure_tool therefore looks identical to one still
    running if you judge by the log. Check for a live 'libero_bin' process, and grep the log
    for '^Error' after each step (build_multipass.sh now does).
  - The multi-pass report files are per seed: MPFS_DISCOVERY_KIT_timing_r1_sN.rpt. Reading
    _r1_s1 gives seed 1, NOT the best pass. Read the seed the log says was saved.

### Where that leaves the timing hypothesis

It could not be tested this way: the experiment needed more margin to exist, and P&R cannot
produce it. Both remaining routes to margin are:
  1. lower VTA's clock  - blocked four times at the Libero integration level
  2. re-time the RTL    - pipeline compute/loadUop/tensorLoad/vmeCmd/cmdGen's address
                          arithmetic (~30 logic levels), our own Chisel, validated in TSIM,
                          single-clock, no integration changes
Route 2 is the only one not yet tried, and the seed spread above says it needs to buy
several hundred picoseconds to make the design robust rather than lucky.

## Route 2 done: re-timing WORKS, and it REFUTES the timing hypothesis (2026-09-16)

### The re-timing itself succeeded

rdLineClNb, rdLen1stMaxTransClNb, rd1stPulseOffsetTensNb and rdLastPulseTensNb in
GenVMECmdWide are pure functions of the DRAM line start address, which only changes on
io.start or stride. They were computed COMBINATIONALLY from the address register, putting a
modulo/shift/add/compare chain directly in front of rdLen -> stride -> rdCmdStartIdx, the
cone that was failing. Now they are computed from the NEXT address and registered under the
same condition, so they are valid on the same cycle as before.

Validated in TSIM against the original, same build otherwise:

    metric          original    re-timed
    GEMM result     64/64       64/64
    cycle count     244         244
    read bursts     20/1/4/64/1 20/1/4/64/1

Cycle-identical and burst-identical; bias_probe 2x2 and 4x4 and GEMM red=4 also correct.

FPGA result:  worst slack +0.056 ns -> +0.628 ns at 125 MHz (+572 ps, more than the 0.46 ns
seed spread), and the critical path MOVED OUT of cmdGen entirely - it is now in
store/tensorStore. Resources unchanged: LSRAM 241, Math 128.

Two bugs I introduced and fixed, both worth remembering:
  - rdLineClNb was declared chiselTypeOf(tmp) but is assigned Mux(..., tmp, tmp + 1.U),
    one bit wider - the carry was silently truncated.
  - The precompute fires on the io.start cycle, but cmdGen.io.xsize comes from decR =
    RegEnable(io.inst, io.start), which during that cycle still holds the PREVIOUS
    instruction. The precompute captured the wrong line length and issued 132 bursts of 256
    beats instead of 5 small ones (134x slower, still functionally correct). Fixed with an
    xsizeNow input driven from the combinational decode. This trap is a direct consequence
    of our own earlier decR change; note dram_offset and sram_offset are already wired from
    the combinational dec for exactly this reason.
  The 134x slowdown was diagnosed from the DPI burst logging added while chasing the GEMM
  bug - without it the symptom was just "much slower" with no cause.

### And the result that matters: GEMM still fails

On the re-timed design, freshly programmed, with mem / alu / pad all passing:

    bias_probe 2x2, 4x4, 8x8   all FAIL
    gemm_probe identity        0/64

With ELEVEN TIMES the timing margin. If marginal timing were the cause, this should have
fixed it or at least changed the behaviour. It did not.

=> THE TIMING HYPOTHESIS IS REFUTED. It drove the clock-lowering attempts, the multi-pass
experiment and this re-timing, and it is wrong. The GEMM failure and the wedge are something
else.

What that leaves: a functional fault that does not reproduce in FSIM or TSIM, is not
addressing, alignment, concurrency, ID handling, burst shape, beat gaps or timing - and yet
GEMM demonstrably computed correctly in one session earlier today. Whatever it is, it is
state-dependent rather than a fixed logic error, and simulation cannot see it.

The re-timing is worth KEEPING regardless: it is behaviourally identical, costs no cycles
and no resources, and takes the design from 0.7% margin (where most P&R seeds fail) to 7.9%.
It just is not the fix for GEMM.

Next honest step: hardware observation. SmartDebug live probes or an ILA on the inp/wgt
scratchpad write ports and the MAC operands would show directly whether data reaches the
scratchpads - the one question no host-side or simulation experiment has been able to answer.

## ROOT CAUSE OF EVERY BOOT FAILURE: my flow dropped the I/O constraints (2026-09-17)

The board constraint is explicit:

    set_io -port_name REF_CLK_50MHz -pin_name R18 -fixed true   (BOARD_MISC.pdc)

    working build (Sep 12):   REF_CLK_50MHz  R18  locked Yes
    every rebuild since:      REF_CLK_50MHz  E12  locked No     <- auto-placed

vta_integrate.tcl called organize_tool_files with only two SDC files. That call REPLACES a
tool's constraint list rather than adding to it, so all eight I/O PDCs and the floorplan PDC
were silently dropped from SYNTHESIZE, PLACEROUTE and VERIFYTIMING. The 50 MHz oscillator
constraint went with them and the fabric was placed with its reference clock on the wrong
pin.

That is why EVERY rebuilt design hung at "Initializing Mi-V IHC V2" - HSS's first access to
a fabric peripheral, exactly what a dead fabric clock looks like. The tell was there and I
walked past it: the multi-pass build was LOGICALLY IDENTICAL to the working design and still
would not boot. A design that cannot boot when nothing about it changed is a flow problem,
not a design problem.

RETRACTED as a result:
  - "enabling CLOCK_DOMAIN_CROSSING on the shared interconnects makes the board unbootable"
  - "a fabric PLL referenced from an existing clock breaks boot"
  - the +0.628 ns figure for the re-timed design: that placement was unconstrained. With the
    I/O constraints restored the same RTL gives +0.139 ns, against +0.056 ns for the
    original. Still an improvement, but 2.5x not 11x.
  - the "timing hypothesis is refuted" claim is therefore WEAKENED, not established: the
    decisive test (a large margin increase) has never actually been run on a valid build.

Fixed by removing the derive_constraints_sdc / import_files / organize_tool_files block from
vta_integrate.tcl entirely. The base design already associates its constraints correctly and
adding VTA introduces no new clock, so there was never anything to add. Verified: the
rebuild now has 15 io_pdc associations (matching the working build) and REF_CLK_50MHz back
on R18, locked.

Rule for the future: never call organize_tool_files unless you enumerate EVERY file the tool
already had. Check a rebuild with
    grep -a REF_CLK_50MHz <project>/designer/*/*_pinrpt_name.rpt
before trusting any hardware measurement from it.

### What this does NOT explain

On the corrected rebuild - boots reliably in 24 s, mem / alu / pad all pass - GEMM still
fails at every size, contributing exactly zero. GEMM also failed on the original Sep 12
design, which was built correctly. So the I/O constraint bug is a separate (serious) flow
bug, not the GEMM bug.

Every hardware measurement taken between the first rebuild and this fix was made on a design
with unconstrained I/O placement and should be treated as unreliable.

## Clean-platform measurements, and SmartDebug is not scriptable here (2026-09-17)

First measurements taken on a correctly-constrained build (REF_CLK on R18) and a freshly
reprogrammed fabric. VTA's cycle register (0x04) read after each run:

    fresh fabric        0
    after alu 1       641
    after gemm red=1  429
    after gemm red=16 3837      (~9x for 16x the weight data)

So on a clean fabric EVERY GEMM completes and reports its cycle count, and the time scales
with the weight volume. The DMA really happens, the program really finishes - and the MAC
output is still exactly zero. No wedge occurred during that sequence either.

(The same measurement taken minutes earlier on an already-wedged device showed the counter
frozen at 0x122 for every run. Any cycle-count measurement is meaningless unless the counter
is seen to CHANGE between runs.)

### SmartDebug cannot be driven from scripts in this setup

  - The Libero project tcl context has none of read_lsram / read_usram / read_active_probe /
    select_active_probe / set_live_probe / list_probes - all report "invalid command name".
  - run_tool -name {SMARTDEBUG} is not a valid tool name.
  - Designer/bin64/g5probe takes -s <script> but exposes none of those commands either.
  - Designer/bin64/sdbg is the Qt GUI; under xvfb it aborts
    (terminate called after throwing an instance of 'Jobtools::ToolStatus').

So the SmartDebug route needs a human at the GUI. Its Memory Blocks view can read LSRAM
contents over JTAG, which would answer the outstanding question - are the weights actually
in the wgt scratchpad? - directly.

### The alternative that IS within scripting reach: instrument the RTL

Add a read-only debug register to the VCR that latches the data being written into the
wgt (and inp) scratchpad, and read it from Linux over the control interface that is already
proven reliable. After a GEMM:
    register holds the expected weights -> the writes happen, fault is on the read/MAC side
    register holds zero                 -> the write data itself is zero, fault is upstream
That is a Chisel change plus a rebuild, but it gives permanent scriptable visibility instead
of a one-off GUI session, and it uses the register path we know works.

## The wedge has been masquerading as a GEMM bug (2026-09-17, later)

A run of bias_probe on a freshly programmed fabric came back **64/64 correct - bias plus
product**. gemm_probe identity then passed 64/64 as well. So the MAC array, the inp load,
the wgt load and the accumulate all work. "GEMM contributes exactly zero", carried in these
notes for weeks, is **wrong**.

What actually happens is that VTA stops executing programs partway through a session, and a
stopped VTA is indistinguishable from a broken computation if the only check is a comparison
against a reference. Two conclusions recorded earlier today were measured on an already-
stopped device and are hereby retracted:

  - "one-hot weights pass, general weights fail" (gemm_probe posrandom/random) - the device
    had wedged between the passing and failing runs.
  - "the MAC array's MATH-block pairing is broken" (pair_probe) - every run after the first
    left the sentinel untouched, i.e. executed nothing at all.

hw_test's gemm results are suspect for the same reason: it fills the output with ZEROS, so
"correct=9/256" is exactly what a never-executed run looks like when 9 reference values
happen to be zero. **Any VTA test that does not pre-fill the output with a sentinel cannot
tell a wrong answer from no answer.**

### matrix.py

New harness, and the one that should be used from now on. It builds and uploads several
programs once, runs them over a single connection, and classifies every execution:

    NOTRUN  output still holds the sentinel     -> VTA executed nothing
    WRONG   output changed but differs from ref -> VTA ran and computed wrongly
    ok      correct

First run on a clean fabric (4 programs x 5 reps):

    alu           {'ok': 5}
    gemm-onehot   {'WRONG': 3, 'EXC': 2}      48/256 on each of the first three
    gemm-pos      {'EXC': 5}
    gemm-signed   {'EXC': 5}

So: ALU is never wrong and never wedges. GEMM is wrong from the very first execution on a
clean fabric, and after three executions the device wedges for good (EXC = the driver's
10 s timeout, and it never recovers without reprogramming).

The 48/256 is itself informative. In that test all four batch tiles get identical x and all
four weight tiles identical one-hot w, so all sixteen output tiles should be identical.
Exactly three are right. Identical inputs giving different answers depending on tile
position is a pipelining symptom, not an arithmetic one.

### Working hypothesis: LOAD/COMPUTE synchronisation

One cause fits both symptoms. GEMM is the only program that makes the LOAD module (inp/wgt)
run concurrently with COMPUTE; the ALU program never touches LOAD. If the producer/consumer
dependency between them is broken, COMPUTE reads scratchpad tiles before LOAD has filled
them (wrong tiles, and only at sizes where the pipeline actually overlaps - which is why the
2x2 gemm_probe passed and the 4x4 does not), and the same dependency queues eventually
deadlock (the sticky wedge).

Next: VTA_DEBUG_FORCE_SERIAL (32) rewrites the dependencies so LOAD, COMPUTE and STORE never
overlap. If that makes GEMM correct and stops the wedging, the fault is in the dependency
logic rather than the datapath.

### FORCE_SERIAL does not fix it - the synchronisation hypothesis is refuted

Same matrix, clean fabric, debug_flag=0x20 (LOAD/COMPUTE/STORE never overlap):

    alu           {'ok': 5}
    gemm-onehot   {'WRONG': 1, 'EXC': 4}     48/256 on the first execution, as before
    gemm-pos      {'EXC': 5}
    gemm-signed   {'EXC': 5}

The wrong answer is **bit-identical (48/256) with and without serialisation**, so it is
deterministic, not a race between modules. Producer/consumer synchronisation is not the
cause.

(The `runtime.cc:963: not reached` that follows the first timeout is a separate, smaller
bug of mine: VTADeviceRun returns 1 on timeout and the runtime's queue state is left
inconsistent, so every later call fails differently. Worth fixing, but downstream.)

### Next suspect: my own re-timing, on the ysize > 1 path

The bitstream on the board contains the GenVMECmdWide re-timing (the generated Verilog has
the new signals). Reading it back with fresh eyes, the re-timing changed `rdLineClNb` from a
wire, combinational from the `rdLineElemBeginAddr` REGISTER, into a Reg **enabled by
`stride`** - while `stride` is itself computed from `rdLineClNb`:

    when((clReadIdx === rdLineClNb - rdLen) && (dramLineIdx =/= io.ysize - 1.U) && io.updateState) {
      stride := true.B }

Upstream broke that loop through the address register; my version interlocks the two. It is
not a combinational loop (the dependence is through a register enable, so Chisel accepts it)
but the equivalence argument in the comment only holds for the first line.

That path is only exercised when **ysize > 1**, and this GEMM schedule loads x_buf strided
over the batch axis, so **ysize = o**. Hence the prediction: o=1 unaffected, failures
appearing as o grows. The 2x2 GEMM that genuinely passed had ysize=2; the 4x4 that fails has
ysize=4. Test by sweeping o in TSIM, current RTL versus upstream RTL.

### The failure is a clean function of o (= ysize on the inp transfer)

Same program, same one-hot operands, same clean fabric, one execution each:

    o=m=2   64/64 correct
    o=m=4   0/256 - every tile all zeros, sentinel gone (so it DID execute and DID store)

Not a wedge, not a race, not arithmetic: at o=4 the accelerator runs to completion and
stores zeros. o is exactly the ysize of the strided inp DMA transfer, so this is the
multi-line path of GenVMECmdWide.

### ...but on hardware EVERY o fails, and TSIM passes every o

    ysweep, TSIM, current RTL (the same RTL as the bitstream): o=1..6 all correct
    ysweep, board, freshly reprogrammed:                       o=1..6 all zeros

So ysize is not the variable either, and the re-timing is not a logic fault - the exact RTL
in the bitstream computes every one of these correctly in Verilator. Hardware fails where
simulation passes.

Also checked and closed:

  - Address dependence. pad_probe holds a dummy allocation of 0..1024 B to shift where the
    operands land: all zeros at every padding. (Caveat: I did not verify the padding
    actually moved the operands, so this is inconclusive rather than a clean negative.)
  - Cache coherency. The driver maps the non-cached DDR alias at 0xC4000000 (a no-map
    reserved region) with O_SYNC, so no cached alias of the operands exists. And the ALU
    test reads its acc operand from that same pool correctly, so the DMA path works.
  - A bitstream mix-up. MPFS_DISCOVERY and MPFS_DISCOVERY.retimed_ok have byte-identical
    programming data (md5 2d4ce1ba, built 04:26 today), so the board this morning and the
    board now run the same design.

### What is actually left

The COMPUTE module's acc DMA works (the ALU test depends on it and never fails). Only the
LOAD module's inp/wgt DMA fails. The same LOAD logic is correct in Verilator. So the fault
is in something Verilator does not model: the real AXI interconnect, or timing.

And the ordering matters. On one boot this morning the 2x2 GEMM passed TWICE, the 4x4 then
ran, and every GEMM after it failed - including a repeat of the 2x2 that had just worked.
That is the shape of a device that gets poisoned by a large transfer and stays poisoned,
not of a wrong computation. Testing that ordering directly (2x2, then 4x4, then 2x2 again,
one process, fresh fabric) is the current experiment.

### The register dump at the moment it stops

From the board's own log when the driver's timeout fired:

    no completion after 10000 ms (insn_count=61, insn at 0xc4400c00)
    cycles 628 -> 628  (unchanged: no completion reported at all)
    regs: 0x00=0x00000001 0x04=0x00000274 0x08=0x0000003d 0x0c=0xc4400c00
          0x10..0x20 = 0

Reading it:

  - 0x00 = 1: the start bit is latched and readable, so the VCR (the AXI4Lite register
    file) is alive and the driver's writes land.
  - 0x04 frozen at 628 across a full 10 s wait. VTA is NOT stuck spinning on an AXI read
    that never returns - in that case the cycle counter would keep climbing. It is not
    running at all.
  - 0x08 = 61 and 0x0c = 0xc4400c00 are exactly what the driver wrote.
  - 0x10..0x20 = 0 is CORRECT, not a bug: the instructions carry absolute addresses
    (LOAD wgt dram_base=12845063, elem=256 -> 12845063*256 = 0xc4000700) and baddr is
    OR-ed in, so a zero base pointer is right.

A live register interface with a dead core is a clock/reset symptom, not a logic one. The
prime suspect is the reset tree: MSS_DLL_LOCKS is the AND of all four MSS FIC DLL locks and
gates EXT_RST_N on every fabric CORERESET, so a momentary loss of any FIC DLL lock parks
VTA in reset permanently while leaving the register file readable. That also explains why
only reprogramming the FPGA recovers it - a Linux reboot does not reset the fabric.

### The board degraded over the session

    first matrix run today   alu ok x5, then GEMMs wrong/wedged
    last matrix run today    alu ok x1, then EVERYTHING wedged - including the ALU

Five FPGA reprogram cycles in one hour. The ALU path, which was solid all morning, now
fails on its second execution. Measurements taken from here on are not trustworthy until
the board has been properly power-cycled (reprogramming is not enough, and is what the
session has been doing).

### Where this leaves the GEMM question

Honest summary: GEMM demonstrably WORKS on this hardware (bias_probe 64/64 = bias +
product, gemm_probe 2x2 64/64). It is not an arithmetic or RTL fault - TSIM passes every
size on the exact RTL in the bitstream. What breaks is that the accelerator stops, and it
stops sooner the longer the board has been mistreated. The next measurement to take, on a
freshly power-cycled board, is simply: how many executions does each program survive, using
matrix.py, before the cycle counter freezes.

## ROOT CAUSE: the inp-scratchpad -> MAC path passes 125 MHz by 0.139 ns (2026-09-17)

The three worst paths in the whole design, from the current build's own timing report
(max_timing_multi_corner, slow_lv_ht):

    Path 1  slack +0.139 ns  min period 7.726 ns
      from VTA/load/tensorLoad_0/tensorLoad/tensorFile_0_.../INST_RAM1K20_IP:A_CLK
      to   VTA/compute/tensorGemm/mvc_0/dot_0_15/m_1/...MACC_PHYS_INST/INST_MACC_IP:A[10]
    Path 2  slack +0.189 ns   (same endpoints, dot_0_9)
    Path 3  slack +0.366 ns   (same endpoints, dot_0_13)
    Path 4  slack +0.463 ns   VTA/load/tensorLoad_1/.../vmeCmd/rdCmdStartIdx[8]

The critical path is the inp scratchpad LSRAM output feeding the MAC array multiplier
inputs - the GEMM datapath - meeting 125 MHz by 1.7% of the period at the slow corner.

This accounts for every observation, including the ones that defeated the whole
investigation:

  - The ALU never uses this path, so it is correct 5/5, always.
  - GEMM depends on it entirely, so it is wrong nearly always and occasionally right.
  - The 14:02-14:05 window when GEMM was correct followed a ~10 hour idle: the die was
    cool. It failed once warm. Temperature is exactly what moves a 0.139 ns margin.
  - TSIM passes every size: functional simulation has no timing.
  - A power cycle changes nothing, because nothing is in a stuck state.
  - All zeros rather than garbage: late data at the multiplier inputs latches as zero.

It also puts the re-timing work in proportion. Paths 4 and 5 ARE the GenVMECmdWide cone
that was re-timed, now at +0.463/+0.477 and no longer critical - the re-timing did what it
was meant to, it just fixed the second-worst path while the real one was never touched.
"The timing hypothesis is refuted", recorded earlier in these notes, was simply wrong.

### The fix, and why the earlier attempts failed

Worst VTA slack by build:

    current, 125 MHz     +0.139 ns   inp scratchpad -> MAC input
    100mhz,  100 MHz     +1.670 ns   acc scratchpad internal
    100mhz.v2, 100 MHz   +1.853 ns   uop VME command

At 100 MHz the inp->MAC path is not even critical: 13x the margin. Both 100 MHz builds
already exist and their timing is good. They never booted for an unrelated reason - the
constraint-flow bug placed the board's 50 MHz oscillator on the wrong pin:

    MPFS_DISCOVERY      REF_CLK_50MHz -pin_name R18   (correct, boots)
    MPFS_DISCOVERY.100mhz    -pin_name E10            (never booted)
    MPFS_DISCOVERY.100mhz.v2 -pin_name E12            (never booted)

So the clock-lowering approach was never actually tested. That bug is now removed from
vta_integrate.tcl, so a 100 MHz build with correct I/O constraints is the fix.

## The 100 MHz rebuild (2026-09-17, evening)

vta_integrate.tcl now builds the fix rather than the known-good 125 MHz topology (the old
one is kept at vta_integrate.tcl.125mhz.bak):

  - VTA_CCC, a dedicated PLL at 100 MHz, referenced from ACLK (FIC_0_CLK) and NOT from the
    REF_CLK_50MHz pad. Loading the pad twice makes Libero insert a CLKINT buffer, the main
    CCC loses its dedicated CCC_SW_CLKIN route, and the board stops booting.
  - CDC enabled on exactly the two interconnect ports VTA uses: SLAVE2_CLOCK_DOMAIN_CROSSING
    on FIC0_INITIATOR (control) and MASTER1_CLOCK_DOMAIN_CROSSING on DMA_INITIATOR (DMA).
    That exposes one clock pin per crossed port - S_CLK2 and M_CLK1, no per-port reset -
    both driven from the new clock.
  - A CORERESET for VTA's domain, released on PLL lock, with EXT_RST_N taken from ARESETN
    directly - deliberately NOT the MSS_DLL_LOCKS-gated reset the other fabric resets use.
  - vta_clocks.sdc declares VTA_vs_FIC0 asynchronous, so the timing engine does not try to
    close paths through the CDC FIFOs that exist to make that unnecessary.

The wiring above is not guesswork: it was recovered from the generated Verilog of the
earlier MPFS_DISCOVERY.100mhz.v2 build, which had already solved it.

### Constraints, done properly this time

The new PLL introduces a clock the base design does not have, so derive_constraints_sdc MUST
run - otherwise VTA's paths are analysed against nothing and the timing report looks clean
and means nothing. The earlier bug was never that the call existed; it was that
organize_tool_files REPLACES a tool's constraint list, and it was called with only two SDC
files, dropping every I/O PDC including REF_CLK_50MHz -pin_name R18. So it is now called
with the COMPLETE list: 8 pdc + 2 sdc for PLACEROUTE, the 2 sdc for SYNTHESIZE and
VERIFYTIMING. The integration run confirms "constraints associated - 8 pdc + 2 sdc".

### build_100mhz.sh checks instead of trusting

Libero exits 0 after a failed step and only flushes its log at the end, so every step is
followed by a grep for ^Error. Then two assertions before anything is programmed:

    REF_CLK_50MHz must be placed on R18       (else the I/O constraints were dropped again)
    the timing report must CONTAIN VTA paths  (an empty list means VTA's clock went
                                               unconstrained and the build proves nothing)

### MPFS_DISCOVERY.base was poisoned

It still referenced a VTA_CTRL_BRIDGE component from the abandoned bridge attempt - its
.prjx and smartgen/VTA_CTRL_BRIDGE_work.ixf - and Libero reported "Unable to find
VTA_CTRL_BRIDGE.cxf" on every integration. Nothing instantiated it, but the base is now
regenerated from scratch by full_100mhz.sh rather than reused.

### The 100 MHz build closed

    REF_CLK_50MHz -> R18                      (I/O constraints preserved this time)
    worst VTA slack +2.586 ns  min period 7.279 ns  slow_lv_ht
      from VTA/store/inst_q/ram_ram_0_2/INST_RAM1K20_IP:A_CLK
      to   VTA/store/tensorStore/tensorStore/xrem[14]:D

Against +0.139 ns at 125 MHz that is 18x the margin, and the critical path has moved
entirely off the MAC array - the inp-scratchpad-to-multiplier path that caused this is no
longer among the worst paths at all. The new worst path is in the STORE module, which the
ALU test exercises constantly and which has never failed.

The prediction to test, and it is falsifiable: matrix.py should show GEMM correct at every
size, with no wedge. If GEMM is still wrong at 100 MHz with 2.6 ns of margin, then timing
was not the whole story and the remaining suspect list is short.

### The 100 MHz build did NOT fix it - and regressed the ALU

Prediction was: GEMM correct at every size, no wedge. Result, on a freshly programmed
board with +2.586 ns of VTA slack:

    alu             {'ok': 2, 'EXC': 3}    wedges on the 3rd execution, never recovers
    gemm-2x2        {'EXC': 5}
    gemm-4x4        {'EXC': 5}
    gemm-2x2-after  {'EXC': 5}

The prediction is falsified. Worse, the ALU - solid 5/5 through every previous test at
125 MHz - now wedges after two executions. Lowering a clock cannot make a previously
reliable path fail, so this is a regression from the new TOPOLOGY, not from the frequency.

So timing was not the whole story. The critical-path finding stands on its own evidence
(the timing report is unambiguous about what the worst path is, and it explains the
ALU/GEMM asymmetry), but it is evidently not sufficient.

The wedge dump is the same shape as before and points at which half of the topology broke:

    cycles 613 -> 613 unchanged, 0x00=0x1, register file answering

The control path reaches VTA - register writes land and reads come back, and that path
crosses S_CLK2. What is dead is instruction FETCH, which goes over the DMA path through
M_CLK1. "Works twice, then never again" is what a CDC FIFO does when its two halves come
out of reset inconsistently and it leaks an entry per program: VTA's ap_rst_n is released
by VTA_RESET on PLL lock, while the interconnect's crossing logic is reset by ARESETN, and
nothing orders those two events.

Note the 100mhz.v2 build this wiring was copied from NEVER BOOTED, so it was never
evidence that the wiring works - only evidence of what someone previously wrote.

## The fix: scratchpadReadLatency = 1 (2026-09-17, late)

The 100 MHz + CDC route was reverted (vta_integrate.tcl restored from the .125mhz.bak copy,
CDC parameters back to false, the VTA clock group removed from vta_clocks.sdc). VTA is back
on FIC_0_CLK with the direct topology that has always been reliable for the ALU.

The timing report said the failing path is the inp scratchpad LSRAM output reaching the MAC
array multiplier inputs, and that nearly 3 ns of it is pure ROUTING - one LUT between the
RAM and the MACC, the rest wire:

    RAM1K20_IP:A_DOUT[1]   arrival 2.284
    CFG_20:A  (net)        arrival 5.220     <- 2.94 ns of routing
    CFG_20:Y  (LUT)        +0.071
    MACC_IP:A[10]          +0.020

VTA already has a parameter for exactly this. TensorGemm extends TensorGemmPipelinedSplit,
which carries:

    val scratchpadReadLatency = 0      // "additional pipe latency of wgt/inp read if needed"
    val inpRdData0 = if (scratchpadReadLatency > 0) RegNext(io.inp.rd(0).data) else io.inp.rd(0).data
    ShiftRegister(inpRdData0(...), mvmInpRdLatency)   // "delay to deliver over distance"

Setting it to 1 registers the inp read data on its way to the MVMs, splitting that long
route across two cycles. It is fully plumbed - the wgt read INDEX is delayed by the same
amount so wgt data arrives with the delayed inp data, and reset_pipe, acc_idx_pipe and
wrpipe0 all add it to their latencies - so this is a supported configuration, not a hack.

### A trap that invalidated my first attempt at validating it

TSIM loads a PREBUILT build/libvta_hw.so, and nothing in the flow rebuilds it when the
Chisel changes. The first "TSIM passes all sizes with the fix" run was against a library
and generated Verilog from the previous day; the change had never been compiled. It was
caught only because sbt happened to be missing from PATH at the next step.

Two consequences recorded:

  - sbt is NOT on PATH by default. It lives at vta/tools/sbt/bin.
  - run_sim.sh now refuses to run when any .scala file is newer than libvta_hw.so, rather
    than reporting a pass that means nothing.
  - A clean build/verilator is needed when the RTL partitioning changes, or the link fails
    with duplicate symbols from stale object files.

Earlier TSIM results in these notes are still sound: no Chisel edit happened between the
library's build (Sep 16 00:57) and today's, and the bitstream's Verilog is from Sep 16
01:01, so library and bitstream matched.

### The pipeline fix closed the path and did NOT fix GEMM

Build with scratchpadReadLatency=1, on the reverted 125 MHz direct topology:

    REF_CLK_50MHz -> R18
    worst VTA slack +0.708 ns (was +0.139), min period 7.157 ns, slow_lv_ht
      from VTA/store/inst_q/ram_ram_0_1/INST_RAM1K20_IP:A_CLK
      to   VTA/store/tensorStore/tensorStore/xrem[13]:D

The inp-scratchpad-to-MAC path is GONE from the worst-path list, which also proves the new
RTL really is in the bitstream. Board result, GEMM as the FIRST programs on a fresh fabric:

    o=1  0/32     o=2  0/64     o=3  0/96     o=4  0/128     every tile zero

**So the timing explanation is falsified.** The indicted path was fixed - 5x the margin, no
longer critical - and GEMM is bit-for-bit as broken as before. The 0.139 ns inp->MAC path
was NOT the cause of the zeros. The root-cause claim recorded earlier today is withdrawn.

What the fix DID change is the shape of the wedge: there are now no driver timeouts at all.
Every program is dispatched and completes promptly; after about four or five programs VTA
starts reporting done instantly while writing nothing (NOTRUN with the sentinel intact, 20
executions in 0.2 s). Previously it hung with the cycle counter frozen. That is a different
failure and probably needs its own investigation rather than being folded into this one.

Two of my own errors to correct in the record:

  - o sets XSIZE, not ysize. The driver's dump shows the inp load is x_size=o, y_size=1.
    ysweep's premise and the "multi-line path" reasoning built on it were wrong. Its
    results stand (GEMM zero at every o); its labelling does not.
  - matrix.py runs ALU first, so on a device that stops after ~5 executions every GEMM
    result is NOTRUN - the program never ran. The first board test of this build was
    therefore not a test of GEMM at all. Test ordering matters when executions are scarce.

### The one thing that has ever made GEMM work

bias_probe, which DMA-loads the accumulator before the GEMM instead of relying on the
reset-GEMM. A pure GEMM emits TWO gemm instructions - a reset one issued BEFORE inp/wgt are
even loaded, then an accumulating one:

    [0] LOAD uop sram_base=0   [1] GEMM      <- reset
    [2] LOAD inp  [3] LOAD wgt  [4] LOAD uop sram_base=1  [5] GEMM   <- accumulate
    [6] STORE out

"Output is zero" therefore means instruction [5] contributed nothing, while the equivalent
accumulate in bias_probe demonstrably worked (bias + product, 64/64). That asymmetry - not
timing - is where the next investigation should start.

### ...and the reset-vs-accumulate lead is dead too

bias_probe, run as the FIRST program on a fresh fabric on the pipelined build:

    == bias only (GEMM contributed nothing) : True
    tile[0,0] got [100, 100, ... 100]      bias intact, product exactly 0

So the accumulator DMA load works, the ALU add works, the store works, and the GEMM
contributes zero - with the accumulator preloaded. There is no asymmetry between the
reset-GEMM and the accumulating GEMM; bias_probe is simply intermittent like everything
else. It returned bias+product at 14:02 and bias-only now, same program.

### Where this actually stands

Across THREE different bitstreams today - 125 MHz original, 100 MHz with CDC, 125 MHz with
the inp->MAC path pipelined - the MAC array's contribution to the accumulator has been
exactly zero, every time, except for one three-minute window at 14:02-14:05 when
bias_probe and gemm_probe both returned fully correct results. That window is the only
evidence that this hardware can do a GEMM at all, and nothing since has reproduced it.

Everything around the MAC is proven working, repeatedly and on every build:

    uop load, acc DMA load, ALU, acc write, out store   ALU test, always correct
    inp/wgt DMA                                         cycle counts scale with weight volume
    the MAC array exists in the netlist                 128 MATH blocks under tensorGemm/mvc_0
    the RTL is functionally correct                     TSIM correct at every size, on RTL
                                                        genuinely rebuilt from this source

What has never been directly observed, in any experiment all day: whether the inp and wgt
data actually ARRIVE in their scratchpads. Every test infers it from the GEMM result, which
is precisely the thing that is broken. The remaining candidates all sit in that blind spot:

  - inp scratchpad reads return zero (data never written, or read at the wrong address)
  - wgt scratchpad reads return zero
  - the MACC blocks are mis-configured by synthesis and multiply to zero

### The one experiment that would settle it

Add a read-only debug register to the VCR that latches what is being read out of the inp
and wgt scratchpads, and read it from Linux over the control interface - which is the one
path proven reliable on every build. After a GEMM:

    register holds the expected operands -> the scratchpads are fine, the fault is the MACs
    register holds zero                  -> the operands never arrive, the fault is upstream

This was proposed at the start of the session and set aside for SmartDebug, which turned out
not to be scriptable. It is now clearly the right move, and unlike then it is fully within
reach: rebuild_rtl.sh makes RTL regeneration reproducible, run_sim.sh refuses stale RTL, and
build_check.sh refuses a build with dropped constraints - so an instrumented bitstream can
be produced and trusted in about two hours.

## Board unreachable = check the Ethernet cable before anything else (2026-09-18)

Symptom: ssh/ping to 192.168.100.2 dead, host `ip -br addr` shows eno1 DOWN.

It is worth two minutes of diagnosis before assuming the board hung or the fabric broke,
because the board can be perfectly healthy. The serial consoles tell you which it is:

    /dev/ttyUSB-FlashPro5B  HSS monitor, prompt ">>"   - present even while Linux runs,
                                                         so ">>" alone means nothing
    /dev/ttyUSB-FlashPro5C  Linux console, login prompt

Use these udev symlinks, NOT /dev/ttyUSBn: the numbers are reassigned at every host boot
(on 09-18 the Linux console was ttyUSB2; after the 09-21 reboot it was ttyUSB0 and ttyUSB2
had become the HSS monitor).

Read one with:  stty -F /dev/ttyUSB-FlashPro5C 115200 raw -echo; cat /dev/ttyUSB-FlashPro5C
(send a newline first - both are silent when idle).

On 2026-09-18 the board was found fine: Linux up, systemd-networkd and vta-rpc both active,
/etc/systemd/network/10-end0-static.network intact with Address=192.168.100.2/24. What was
wrong was physical - `ethtool end0` reported "Link detected: no", and the host's eno1 showed
NO-CARRIER as well. Both ends seeing NO-CARRIER means the cable, not the software. Nothing
to fix on either machine; the static address reapplies by itself once carrier returns,
because networkd holds it back until then.

So: NO-CARRIER on both ends -> reconnect the cable. No reprogramming, no reboot, and in
particular do not reprogram the FPGA "to fix networking" - that costs four minutes and
resets the accelerator state you may have been about to measure.

### 2026-09-21: "cannot ping when the cable is connected" - still physical, not routing

Checked every layer on both machines; all correct:

    host   eno1 <- netplan-eno1, manual 192.168.100.1/24. The other wired profile
           ("Wired connection 1", DHCP) is bound to enp113s0 and cannot take eno1.
           ufw inactive, INPUT policy ACCEPT, icmp_echo_ignore_all=0, rp_filter=2 (loose).
    board  end0 <- 10-end0-static.network ([Match] Name=end0, 192.168.100.2/24);
           eth.network matches eth* and cannot take end0.

The 192.168.100.0/24 route is absent from the host table, but only because NetworkManager
installs a connected route when the device gets carrier - and neither host port (eno1, I219,
..:32; enp113s0, I210, ..:33) has had carrier since 09-18 15:38 per the NM journal, across
two host reboots and a board power cycle. The board's end0 is NO-CARRIER as well. A missing
route is the consequence of no link, not a routing misconfiguration.

Note the host has TWO RJ45 ports. The board must be on eno1 (the I219): the 192.168.100.1
profile is bound to that interface name, and enp113s0 runs DHCP, which on a point-to-point
cable to the board gets nothing - which would look exactly like a broken route table.

## Operand debug taps (2026-09-21)

The one thing no experiment had observed directly: do the GEMM operands actually reach the
MAC array? Twelve read-only registers added after everything the driver uses (VCR ucnt,
0x28..0x54; nothing existing moved), latched on finish like acc_wr_count at 0x24:

    0x28..0x30  DRAM -> LOAD, inp   (VME rd 2)  beats, OR of all words, first word
    0x34..0x3c  DRAM -> LOAD, wgt   (VME rd 3)  beats, OR, first word
    0x40..0x48  scratchpad -> GEMM, inp          reads, OR, LAST word
    0x4c..0x54  scratchpad -> GEMM, wgt          reads, OR, LAST word

Scratchpad taps latch the LAST read because a reduction GEMM issues a reset-GEMM before the
operands are loaded, and it reads the scratchpads too - random in Verilator, zero on the FPGA.
Taps are registered before folding, so nothing lands on the scratchpad-to-MAC path.

Tools: dbg_regs.py board (devmem2 over ssh) / dbg_regs.py tsim (TSIM driver prints the
same registers with VTA_DUMP_DBG=1).

Free datapoint before any rebuild: acc_wr_count (0x24) was always in the bitstream and never
read. After one 2x2 GEMM on the board: 8 = 2 GEMM instructions x 4 tiles. Both GEMMs execute
and write the accumulator; what they write is zero.

TSIM reference, 2x2 identity GEMM (x = 1..16, one-hot w), correct 64/64:

    acc_wr 8 | vme_inp 4 beats first 0x04030201 | vme_wgt 64 beats first 0x00000001
    spad_inp 8 reads last 0x04030201 | spad_wgt 8 reads last 0x00000001

Another stale-RTL trap, caught before it did damage: rebuild_rtl.sh removed the library and
Verilator objects but not build/chisel/Test.DefaultPynqConfig.sv, whose make rule has no
dependency on the sources - so the "rebuilt" library was the old RTL again. Found because
the library came out byte-identical in size. rebuild_rtl.sh now deletes the generated .sv,
and run_sim.sh's staleness guard compares sources against the .sv as well as the library.

## ROOT CAUSE FOUND AND FIXED: LOAD's instruction queue corrupted instructions (2026-09-25)

The live debug registers caught it on a hung GEMM - the same instruction measured at LOAD's
input port and at its instruction-queue output:

    Fetch -> LOAD:      LOAD inp xsize=2 ysize=1 dram=205520912    correct
    tensor load start:  LOAD inp xsize=2 ysize=0 dram=205520896    corrupted

Only LOAD's instruction queue sits between those taps. It was a plain Chisel Queue, which is
an ASYNCHRONOUS-read Mem; PolarFire synthesis maps that into LSRAM, which can only read
synchronously, so the instruction presented at the output does not match what was written.

    Load.scala:   val inst_q = Module(new Queue(...))      ->  new SyncQueue(...)

This accounts for the entire history of the problem:

  - xsize corrupted to 0 decodes as a SYNC (LoadDecode: isSync = LINP|LWGT with xsize==0),
    so the load is silently skipped and no data is fetched. That is why the inp scratchpad
    stayed zero, every GEMM returned exactly zero, and the wgt load right after it was fine.
  - ysize corrupted to 0 hangs the command generator, which finishes at line index
    ysize-1 = 65535: no command is ever issued (inp.cmd.cnt = 0) and the program never
    completes. That was the deterministic 3/3 hang on the trace build.
  - Different bits corrupt in different builds, which is why the symptom kept changing -
    all zeros, hangs, one three-minute window of correct results - and why the timing work
    moved the symptom around without ever fixing it.
  - The ALU always passed because it needs no LOAD instruction at all.
  - COMPUTE's uop/acc loads never failed because Compute.scala already used SyncQueue.

### Hardware results with the fix

    mem PASS | alu PASS | gemm red=1 PASS | gemm red=4 PASS | gemm red=16 PASS

gemm red=16 is 4x256 . 256x64 - the largest, and it has never passed before.

### Still open: the wedge

matrix.py, 4 programs x 5 reps on a fresh fabric:

    alu            {'ok': 5}
    gemm-2x2       {'ok': 3, 'NOTRUN': 2}
    everything after execution 9: NOTRUN, 20 executions in 0.2 s

So results are CORRECT until the device stops accepting work, at around the ninth program.
After that, calls return instantly having done nothing (the "reports done immediately"
variant rather than the older hang). This is a separate bug from the instruction
corruption and is now the top remaining issue.

STORE also uses a plain Queue (Store.scala) and has the same latent bug. It has not misbehaved
yet, but it is the same construct and is a candidate both for the wedge and for a robustness fix.

## BOTH BUGS FIXED - VTA is working (2026-09-25)

Switching STORE to SyncQueue as well removed the wedge. Same root cause as the GEMM
corruption: a plain Chisel Queue is an asynchronous-read Mem, and at 512 x 128 bits
PolarFire synthesis maps it into LSRAM, which can only read synchronously.

    Load.scala   new Queue(...) -> new SyncQueue(...)    fixed wrong/zero GEMM results
    Store.scala  new Queue(...) -> new SyncQueue(...)    fixed the wedge

Why a corrupted STORE instruction wedged the device: the VCR gives io.vcr.finish priority
over host writes to the control register, so once finish is stuck asserted the launch bit
can never be set again. The driver writes launch, immediately reads back "done", and every
call returns having run nothing. Measured before the fix: ctrl reads 0x2 and writing 1 does
not stick.

### Results after both fixes

    matrix.py, 4 programs x 15 reps, one process, fresh fabric:
      alu {'ok': 15}   gemm-2x2 {'ok': 15}   gemm-4x4 {'ok': 15}   gemm-2x2-again {'ok': 15}
      60 executions, every one correct, no wedge

    hw_test: mem PASS | alu PASS | gemm red=1 PASS | gemm red=4 PASS | gemm red=16 PASS

Previous builds stopped accepting work after 9 to 14 programs.

Worst VTA slack improved to +0.980 ns (min period 6.885 ns, slow_lv_ht) - SyncQueue maps
onto LSRAM cleanly instead of fighting the inference.

The debug counters now agree with TSIM on every field: inp.start xsize=2 ysize=1,
dram_offset x 16 == the AXI address subsequently requested (0xC4800C00), cmd len 3,
6 compute instructions, 1 store, 1 finish per program.

### What this retires

  - "GEMM contributes exactly zero" - it was a silently skipped load: xsize corrupted to 0
    decodes as a sync, so no inp data was ever fetched.
  - The timing work (re-timing, 100 MHz, scratchpadReadLatency) - real improvements to real
    critical paths, but never the cause. They changed which bits got corrupted, which is why
    the symptom kept moving.
  - The intermittency, the three-minute window when GEMM worked, and the sticky wedge: all
    one root cause.

The instruments that made this findable: matrix.py (classifies every execution
NOTRUN/WRONG/ok), the VCR debug taps plus mpfs/dbg_regs.py, run_sim.sh refusing stale RTL,
and build_check.sh refusing a build with dropped I/O constraints.

## End to end: all ten ResNet-18 conv2d layers run on the board (2026-09-25)

conv_probe.py is VTA's own test_benchmark_topi_conv2d with two board-specific changes:
export a shared library with the riscv64 cross compiler instead of saving a .o for the RPC
server to link, and skip program_fpga (the fabric is programmed over JTAG, and no prebuilt
bitstream exists for this config). CONV_ONLY=<name> runs a single layer.

Full TVM stack: TOPI conv2d, the tuned VTA schedule, padding, bias, right shift, clip and
cast, checked against a numpy reference.

    C2   56x56  64->64   3x3      8.43 GOPS
    C3   56x56  64->128  3x3 /2   7.21
    C4   56x56  64->128  1x1 /2   0.87
    C5   28x28 128->128  3x3      9.14
    C6   28x28 128->256  3x3 /2   8.58
    C7   28x28 128->256  1x1 /2   1.06
    C8   14x14 256->256  3x3     10.17
    C9   14x14 256->512  3x3 /2   9.75
    C10  14x14 256->512  1x1 /2   1.23
    C11   7x7  512->512  3x3     10.87

The 1x1 layers are an order of magnitude slower because they move nearly as much data for a
ninth of the arithmetic - DMA bound, not compute bound.

Known issue: running all ten in ONE process kills the RPC server part way (connection reset
by peer, nothing in the board's journal). Each layer passes on its own, so it is not
shape-specific; it accumulates across workloads in a process. The DMA pool is not obviously
too small - /proc/iomem confirms the full 64 MiB at 0xC4000000 is reserved, matching what
the driver assumes - so the suspect is pool exhaustion or fragmentation across many
allocations, or a leak in the driver's free list.

## The single-process multi-layer crash does not reproduce (2026-09-25)

All ten ResNet-18 conv layers now run in ONE process, three times in a row, 10/10 each:

    C2 8.49 | C3 7.23 | C4 0.88 | C5 9.09 | C6 8.61 | C7 1.07
    C8 10.14 | C9 9.81 | C10 1.24 | C11 10.92 GOPS

**This was not fixed, it stopped happening.** The pool diagnostics added for it never fired,
so DMA exhaustion - the leading hypothesis - was NOT the cause. What changed in between was
build_board_runtime.sh rebuilding and redeploying libvta.so, whose only source change was
those same diagnostics; they cannot fix a crash. So either the board had been running a
stale libvta from before some earlier driver fix (unprovable now - the old binary is gone,
and the board's clock is a day behind the host's, so timestamps prove nothing), or the
original failure was transient state left by the reprogram-and-wedge sequence that
immediately preceded it. Board and host libvta now agree (md5 c77f0de2f885).

Worth keeping from the attempt: a real blind spot is closed. LOG(FATAL) in TVM THROWS, and
VTAMemAlloc is extern "C", so the throw crosses a C ABI boundary and calls std::terminate -
the process dies with the message trapped in an exception nobody catches. That is exactly
why the original crash left NOTHING in the board's journal and the host saw only
"connection reset by peer". The allocator now writes its state to stderr, unbuffered,
BEFORE LOG(FATAL): pool size, bytes in use, block count, peak, free fragments, largest free
block and the failing request. VTA_MPFS_POOL_LOG=1 reports occupancy on every alloc and
free, which separates a leak (used climbing) from fragmentation (used flat, largest free
block shrinking).

If it returns, the failure will now explain itself instead of vanishing.

## Performance, measured (2026-09-25)

perf_report.py runs each layer on its own and pairs the test's wall time with VTA's own
cycle counter (reg 0x04 = the last launch's cycles), so accelerator time and system time can
be told apart.

    peak = 64 GOPS (16x16 MACs, 2 ops each, 125 MHz)

    layer     MOP  wall ms   GOPS  busy ms  busy GOPS  MAC util  in VTA
    C2      231.2    27.45   8.42     4.36      53.01       83%     16%
    C3      115.6    16.09   7.19     2.24      51.63       81%     14%
    C4       12.8    14.73   0.87     0.92      13.96       22%      6%
    C5      231.2    25.45   9.08     4.00      57.78       90%     16%
    C6      115.6    13.51   8.56     2.04      56.57       88%     15%
    C7       12.8    12.16   1.06     0.63      20.49       32%      5%
    C8      231.2    22.77  10.15     3.82      60.46       94%     17%
    C9      115.6    11.90   9.71     1.96      58.90       92%     16%
    C10      12.8    10.99   1.17     0.58      22.03       34%      5%
    C11     231.2    21.28  10.87     3.77      61.39       96%     18%

    total 1310 MOP, wall 176.3 ms -> 7.43 GOPS
    busy 24.3 ms (14% of wall) -> 53.85 GOPS while busy = 84% of peak

The accelerator is fine: 84% of peak while running, 96% on C11. It is busy only 14% of the
wall time. The rest is per-call host work on the U54 - the runtime rebuilds the instruction
and uop streams and copies them into the DMA pool on every invocation.

Utilisation RISES as spatial size shrinks and channels grow (C2 83% -> C11 96%): fewer,
larger tiles mean fewer instructions per unit of arithmetic. The 1x1 layers at 22-34% are
inherent, not a fault - they move nearly as much data for a ninth of the work.

These ten layers: 176 ms today (5.7 inferences/s); 24 ms (41/s) if the per-call cost were
amortised. That 7x needs no hardware change, and is now unblocked since multi-layer runs in
one process work.

Levers in order: graph-level execution (7x, and nearly all of what is available, since the
accelerator time is already near-optimal); autotuning (tophub has no mpfs entries, so these
are fallback schedules); clock (+0.98 ns slack in hand); a wider MAC array last (128 of the
part's 292 MATH blocks are used).
