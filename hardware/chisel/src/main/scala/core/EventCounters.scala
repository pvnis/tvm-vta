/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

package vta.core

import chisel3._
import chisel3.util._
import vta.util.config._
import vta.shell._
import ISA._

/** EventCounters.
 *
 * This unit contains all the event counting logic. One common event tracked in
 * hardware is the number of clock cycles taken to achieve certain task. We
 * can count the total number of clock cycles spent in a VTA run by checking
 * launch and finish signals.
 *
 * The event counter value is passed to the VCR module via the ecnt port, so
 * they can be accessed by the host. The number of event counters (nECnt) is
 * defined in the Shell VCR module as a parameter, see VCRParams.
 *
 * If one would like to add an event counter, then the value of nECnt must be
 * changed in VCRParams together with the corresponding counting logic here.
 */
class EventCounters(debug: Boolean = false)(implicit p: Parameters) extends Module {
  val vp = p(ShellKey).vcrParams
  val io = IO(new Bundle {
    val launch = Input(Bool())
    val finish = Input(Bool())
    val ecnt = Vec(vp.nECnt, ValidIO(UInt(vp.regBits.W)))
    val ucnt = Vec(vp.nUCnt, ValidIO(UInt(vp.regBits.W)))
    val acc_wr_event = Input(Bool())
    // Operand debug taps (see the block at the end of this module).
    val dbg_vme_inp = Flipped(ValidIO(UInt(p(ShellKey).memParams.dataBits.W)))
    val dbg_vme_wgt = Flipped(ValidIO(UInt(p(ShellKey).memParams.dataBits.W)))
    val dbg_spad_inp = Flipped(ValidIO(UInt(
      (p(CoreKey).batch * p(CoreKey).blockIn * p(CoreKey).inpBits).W)))
    val dbg_spad_wgt = Flipped(ValidIO(UInt(
      (p(CoreKey).blockOut * p(CoreKey).blockIn * p(CoreKey).wgtBits).W)))
    // Second set: what each LOAD tensor load was told to do, and what it asked VME for.
    val dbg_start = Vec(2, Flipped(ValidIO(UInt(INST_BITS.W))))   // inp, wgt
    val dbg_cmd = Vec(2, Flipped(ValidIO(new VMECmd)))             // inp, wgt
  })
  val cycle_cnt = RegInit(0.U(vp.regBits.W))
  when(io.launch && !io.finish) {
    cycle_cnt := cycle_cnt + 1.U
  }.otherwise {
    cycle_cnt := 0.U
  }
  io.ecnt(0).valid := io.finish
  io.ecnt(0).bits := cycle_cnt

  val acc_wr_count = Reg(UInt(vp.regBits.W))
  when (!io.launch || io.finish) {
    acc_wr_count := 0.U
  }.elsewhen (io.acc_wr_event) {
    acc_wr_count := acc_wr_count + 1.U
  }
  io.ucnt(0).valid := io.finish
  io.ucnt(0).bits := acc_wr_count

  // Operand debug taps. On MPFS095T a GEMM runs to completion and writes the accumulator the
  // expected number of times (acc_wr_count), yet the result is all zeros - while the same RTL
  // in TSIM is correct. These say where along each operand's path the data turns into zeros:
  //
  //   vme_inp / vme_wgt    read beats arriving from DRAM for the LOAD module (VME rd 2/3)
  //   spad_inp / spad_wgt  scratchpad read data handed to the GEMM
  //
  // Each stream gets three registers, reset at launch and latched on finish like the others:
  //   count  beats (VME) or reads (scratchpad) with valid data
  //   or     OR of every 32-bit word of every beat: zero means no nonzero bit was ever seen
  //   sample the lowest 32-bit word of the FIRST beat (VME) or of the LAST read (scratchpad)
  //
  // Why last for the scratchpads: a GEMM with a reduction issues a reset-GEMM before the
  // operands are loaded, and that GEMM reads the scratchpads too (uninitialised - zero on the
  // FPGA, random in Verilator). The read that matters is the accumulate-GEMM's, which is the
  // last. For the VME streams the first beat is the start of the operand, so first is right.
  //
  // The taps are registered before any folding, so none of this logic lands on the
  // scratchpad-to-MAC path the timing work was about.
  def tap(in: ValidIO[UInt], keepLast: Boolean): Seq[UInt] = {
    val w = in.bits.getWidth
    val v = RegNext(in.valid, false.B)
    val d = RegNext(in.bits)
    val words = (0 until (w + 31) / 32).map(i => d(math.min(w, 32 * i + 32) - 1, 32 * i))
    val folded = words.reduce(_ | _)
    val cnt = Reg(UInt(vp.regBits.W))
    val orr = Reg(UInt(vp.regBits.W))
    val first = Reg(UInt(vp.regBits.W))
    val seen = Reg(Bool())
    when(!io.launch || io.finish) {
      cnt := 0.U; orr := 0.U; first := 0.U; seen := false.B
    }.elsewhen(v) {
      cnt := cnt + 1.U
      orr := orr | folded
      when(!seen || keepLast.B) { first := words(0); seen := true.B }
    }
    Seq(cnt, orr, first)
  }
  // Second set, for inp then wgt (wgt loads correctly on the board, so it is the control):
  //   start: count of load starts; FIRST start's xsize (low 16) | ysize (high 16); its dram_offset
  //   cmd:   count of VME read commands; FIRST command's address; its len
  // Together they separate "the load was told to read nothing" (a stale instruction, xsize 0,
  // no data needed to finish) from "it asked for data and the data went elsewhere".
  def sample(v: Bool, fields: Seq[UInt]): Seq[UInt] = {
    val rv = RegNext(v, false.B)
    val rf = fields.map(f => RegNext(f))
    val cnt = Reg(UInt(vp.regBits.W))
    val held = Seq.fill(fields.length)(Reg(UInt(vp.regBits.W)))
    when(!io.launch || io.finish) {
      cnt := 0.U; held.foreach(_ := 0.U)
    }.elsewhen(rv) {
      cnt := cnt + 1.U
      when(cnt === 0.U) { held.zip(rf).foreach { case (h, f) => h := f } }
    }
    cnt +: held
  }
  val set2 = (0 until 2).flatMap { i =>
    val d = io.dbg_start(i).bits.asTypeOf(new MemDecode)
    sample(io.dbg_start(i).valid, Seq(Cat(d.ysize, d.xsize), d.dram_offset)) ++
      sample(io.dbg_cmd(i).valid, Seq(io.dbg_cmd(i).bits.addr, io.dbg_cmd(i).bits.len))
  }
  val dbg = Seq(tap(io.dbg_vme_inp, false), tap(io.dbg_vme_wgt, false),
                tap(io.dbg_spad_inp, true), tap(io.dbg_spad_wgt, true)).flatten ++ set2
  require(dbg.length == vp.nUCnt - 1, "-F- nUCnt must be 1 + 24 debug registers")
  for ((r, i) <- dbg.zipWithIndex) {
    io.ucnt(i + 1).valid := io.finish
    io.ucnt(i + 1).bits := r
  }
}
