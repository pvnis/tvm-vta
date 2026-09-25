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

/** Load.
 *
 * Load inputs and weights from memory (DRAM) into scratchpads (SRAMs).
 * This module instantiate the TensorLoad unit which is in charge of
 * loading 1D and 2D tensors to scratchpads, so it can be used by
 * other modules such as Compute.
 */
class Load(debug: Boolean = false)(implicit p: Parameters) extends Module {
  val mp = p(ShellKey).memParams
  val io = IO(new Bundle {
    val i_post = Input(Bool())
    val o_post = Output(Bool())
    val inst = Flipped(Decoupled(UInt(INST_BITS.W)))
    val inp_baddr = Input(UInt(mp.addrBits.W))
    val wgt_baddr = Input(UInt(mp.addrBits.W))
    val vme_rd = Vec(2, new VMEReadMaster)
    val inp = new TensorClient(tensorType = "inp")
    val wgt = new TensorClient(tensorType = "wgt")
    // Debug: the instruction each tensor load latches on its start cycle (see EventCounters).
    val dbg_start = Vec(2, ValidIO(UInt(INST_BITS.W)))
    // Debug: every instruction LOAD dequeues from its own instruction queue.
    val dbg_deq = ValidIO(UInt(INST_BITS.W))
  })
  val sIdle :: sSync :: sExe :: Nil = Enum(3)
  val state = RegInit(sIdle)

  val s = Module(new Semaphore(counterBits = 8, counterInitValue = 0))
  val inst_q = Module(new Queue(UInt(INST_BITS.W), p(CoreKey).instQueueEntries))
  // PolarFire timing fix: register the head of the instruction queue so the RAM read
  // is not in series with instruction decode + DMA address arithmetic (was the only
  // failing path cone at 125 MHz on MPFS095T). Adds 1 cycle per instruction.
  val inst_head = Queue(inst_q.io.deq, 1, pipe = true)

  val dec = Module(new LoadDecode)
  dec.io.inst := inst_head.bits

  val tensorType = Seq("inp", "wgt")
  val tensorDec = Seq(dec.io.isInput, dec.io.isWeight)
  val tensorLoad =
    Seq.tabulate(2)(i => Module(new TensorLoad(tensorType = tensorType(i))))

  val start = inst_head.valid & Mux(dec.io.pop_next, s.io.sready, true.B)
  val done = Mux(dec.io.isInput, tensorLoad(0).io.done, tensorLoad(1).io.done)

  // control
  switch(state) {
    is(sIdle) {
      when(start) {
        when(dec.io.isSync) {
          state := sSync
        }.elsewhen(dec.io.isInput || dec.io.isWeight) {
          state := sExe
        }
      }
    }
    is(sSync) {
      state := sIdle
    }
    is(sExe) {
      when(done) {
        state := sIdle
      }
    }
  }

  // instructions
  inst_q.io.enq <> io.inst
  inst_head.ready := (state === sExe & done) | (state === sSync)
  io.dbg_deq.valid := inst_head.fire
  io.dbg_deq.bits := inst_head.bits

  // load tensor
  // [0] input (inp)
  // [1] weight (wgt)
  val ptr = Seq(io.inp_baddr, io.wgt_baddr)
  val tsor = Seq(io.inp, io.wgt)
  for (i <- 0 until 2) {
    tensorLoad(i).io.start := state === sIdle & start & tensorDec(i)
    io.dbg_start(i).valid := tensorLoad(i).io.start
    io.dbg_start(i).bits := inst_head.bits
    tensorLoad(i).io.inst := inst_head.bits
    tensorLoad(i).io.baddr := ptr(i)
    tensorLoad(i).io.tensor <> tsor(i)
    io.vme_rd(i) <> tensorLoad(i).io.vme_rd
  }

  // semaphore
  s.io.spost := io.i_post
  s.io.swait := dec.io.pop_next & (state === sIdle & start)
  io.o_post := dec.io.push_next & ((state === sExe & done) | (state === sSync))

  // debug
  if (debug) {
    // start
    when(state === sIdle && start) {
      when(dec.io.isSync) {
        printf("[Load] start sync\n")
      }.elsewhen(dec.io.isInput) {
        printf("[Load] start input\n")
      }.elsewhen(dec.io.isWeight) {
        printf("[Load] start weight\n")
      }
    }
    // done
    when(state === sSync) {
      printf("[Load] done sync\n")
    }
    when(state === sExe) {
      when(done) {
        when(dec.io.isInput) {
          printf("[Load] done input\n")
        }.elsewhen(dec.io.isWeight) {
          printf("[Load] done weight\n")
        }
      }
    }
  }
}
