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

/*!
 * \file mpfs_driver.cc
 * \brief VTA driver for Microchip PolarFire SoC (MPFS), e.g. the Discovery Kit.
 *
 * VTA sits in the FPGA fabric behind FIC0: its Chisel shell's control registers are
 * memory mapped, and its AXI master reads/writes DDR directly with physical addresses.
 *
 * Unlike the pynq/de10nano ports this needs no kernel module. PolarFire SoC exposes DDR
 * through several address aliases, and the reference design's device tree reserves a
 * "non-cached-low-buffer" region (no-map, so Linux never touches it) that is visible to
 * both the CPUs and fabric masters without cache coherency. We mmap that region through
 * /dev/mem and suballocate from it, which makes VTAFlushCache/VTAInvalidateCache no-ops:
 * there is no cached alias of these buffers to be stale.
 *
 * Defaults match the Discovery Kit reference design with VTA on FIC0; override with
 * VTA_MPFS_REG_BASE / VTA_MPFS_DMA_BASE / VTA_MPFS_DMA_SIZE (hex or decimal) if your
 * design or reserved-memory node differs.
 */

#include <vta/driver.h>
#include <dmlc/logging.h>

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ios>
#include <mutex>
#include <utility>
#include <vector>

namespace {

// VTA control registers (Chisel VCR), as driven by the TSIM/de10nano drivers.
static const uint32_t kRegCtrl      = 0x00;  // write 1 = launch; bit1 reads 1 = finished
static const uint32_t kRegCycles    = 0x04;  // cycle counter of the last run
static const uint32_t kRegInsnCount = 0x08;
static const uint32_t kRegInsnAddr  = 0x0c;
static const uint32_t kRegPtrFirst  = 0x10;  // 0x10..0x20: unused pointer registers
static const uint32_t kRegPtrLast   = 0x20;
static const uint32_t kCtrlLaunch   = 0x1;
static const uint32_t kCtrlFinish   = 0x2;

static const uint64_t kDefaultRegBase = 0x60020000;   // FIC0 interconnect slave 2
static const size_t   kDefaultRegSize = 0x10000;      // 64 KB window
static const uint64_t kDefaultDmaBase = 0xC4000000;   // non-cached-low-buffer (no-map)
static const size_t   kDefaultDmaSize = 64u << 20;    // 64 MiB
// Page alignment for every buffer. TVM asks for up to 256-byte alignment on VTA
// tensors, and the other VTA ports get page granularity for free because they
// allocate through CMA or a page-based allocator; matching that keeps us clear of
// any alignment requirement the runtime imposes on a buffer we hand back.
static const size_t   kAlign          = 4096;         // DMA buffer alignment

uint64_t EnvU64(const char* name, uint64_t dflt) {
  const char* s = getenv(name);
  if (s == nullptr || *s == '\0') return dflt;
  return strtoull(s, nullptr, 0);
}

/*! \brief Maps VTA's registers and the reserved DMA pool, and suballocates the pool. */
class MPFSDevice {
 public:
  static MPFSDevice* Global() {
    static MPFSDevice inst;
    return &inst;
  }

  void WriteReg(uint32_t offset, uint32_t value) {
    *reinterpret_cast<volatile uint32_t*>(reg_ + offset) = value;
  }
  uint32_t ReadReg(uint32_t offset) {
    return *reinterpret_cast<volatile uint32_t*>(reg_ + offset);
  }

  void* Alloc(size_t size) {
    std::lock_guard<std::mutex> lock(mutex_);
    size = (size + kAlign - 1) & ~(kAlign - 1);
    // first fit over the free list
    for (auto it = free_.begin(); it != free_.end(); ++it) {
      if (it->second >= size) {
        size_t off = it->first, rest = it->second - size;
        free_.erase(it);
        if (rest) free_.emplace_back(off + size, rest);
        used_.emplace_back(off, size);
        return dma_ + off;
      }
    }
    LOG(FATAL) << "VTA: out of DMA memory (pool " << dma_size_ << " bytes at 0x"
               << std::hex << dma_phy_ << "); requested " << std::dec << size
               << ". Enlarge the reserved region or set VTA_MPFS_DMA_SIZE.";
    return nullptr;
  }

  void Free(void* buf) {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t off = static_cast<size_t>(reinterpret_cast<uint8_t*>(buf) - dma_);
    for (auto it = used_.begin(); it != used_.end(); ++it) {
      if (it->first == off) {
        free_.emplace_back(off, it->second);
        used_.erase(it);
        Coalesce();
        return;
      }
    }
    LOG(FATAL) << "VTA: VTAMemFree on a pointer that was not allocated here";
  }

  vta_phy_addr_t PhyAddr(void* buf) {
    return static_cast<vta_phy_addr_t>(
        dma_phy_ + static_cast<uint64_t>(reinterpret_cast<uint8_t*>(buf) - dma_));
  }

  bool Contains(void* buf) {
    uint8_t* p = reinterpret_cast<uint8_t*>(buf);
    return p >= dma_ && p < dma_ + dma_size_;
  }

 private:
  MPFSDevice() {
    uint64_t reg_base = EnvU64("VTA_MPFS_REG_BASE", kDefaultRegBase);
    size_t   reg_size = static_cast<size_t>(EnvU64("VTA_MPFS_REG_SIZE", kDefaultRegSize));
    dma_phy_          = EnvU64("VTA_MPFS_DMA_BASE", kDefaultDmaBase);
    dma_size_         = static_cast<size_t>(EnvU64("VTA_MPFS_DMA_SIZE", kDefaultDmaSize));

    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    CHECK_GE(fd, 0) << "VTA: cannot open /dev/mem (run as root)";
    reg_ = reinterpret_cast<uint8_t*>(
        mmap(nullptr, reg_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, reg_base));
    CHECK(reg_ != MAP_FAILED) << "VTA: cannot map registers at 0x" << std::hex << reg_base;
    dma_ = reinterpret_cast<uint8_t*>(
        mmap(nullptr, dma_size_, PROT_READ | PROT_WRITE, MAP_SHARED, fd, dma_phy_));
    CHECK(dma_ != MAP_FAILED) << "VTA: cannot map the DMA pool at 0x" << std::hex << dma_phy_
                              << " (is it reserved no-map in the device tree?)";
    close(fd);
    free_.emplace_back(0, dma_size_);
  }

  void Coalesce() {
    bool merged = true;
    while (merged) {
      merged = false;
      for (size_t i = 0; i < free_.size() && !merged; ++i) {
        for (size_t j = 0; j < free_.size(); ++j) {
          if (i == j) continue;
          if (free_[i].first + free_[i].second == free_[j].first) {
            free_[i].second += free_[j].second;
            free_.erase(free_.begin() + j);
            merged = true;
            break;
          }
        }
      }
    }
  }

  uint8_t* reg_{nullptr};
  uint8_t* dma_{nullptr};
  uint64_t dma_phy_{0};
  size_t dma_size_{0};
  std::vector<std::pair<size_t, size_t> > free_, used_;
  std::mutex mutex_;
};

}  // namespace

void* VTAMemAlloc(size_t size, int cached) {
  // 'cached' is ignored: this pool is a non-cached alias, which is what keeps the CPU and
  // VTA coherent without any cache maintenance.
  (void)cached;
  return MPFSDevice::Global()->Alloc(size);
}

void VTAMemFree(void* buf) { MPFSDevice::Global()->Free(buf); }

vta_phy_addr_t VTAMemGetPhyAddr(void* buf) { return MPFSDevice::Global()->PhyAddr(buf); }

void VTAMemCopyFromHost(void* dst, const void* src, size_t size) {
  memcpy(dst, src, size);
  __sync_synchronize();
}

void VTAMemCopyToHost(void* dst, const void* src, size_t size) {
  __sync_synchronize();
  memcpy(dst, src, size);
}

// No cached alias of these buffers exists, so there is nothing to flush or invalidate.
void VTAFlushCache(void* vir_addr, vta_phy_addr_t phy_addr, int size) { __sync_synchronize(); }
void VTAInvalidateCache(void* vir_addr, vta_phy_addr_t phy_addr, int size) { __sync_synchronize(); }

VTADeviceHandle VTADeviceAlloc() { return MPFSDevice::Global(); }

void VTADeviceFree(VTADeviceHandle handle) { /* singleton, mapped for the process lifetime */ }

int VTADeviceRun(VTADeviceHandle handle, vta_phy_addr_t insn_phy_addr, uint32_t insn_count,
                 uint32_t wait_cycles) {
  MPFSDevice* dev = static_cast<MPFSDevice*>(handle);
  dev->WriteReg(kRegInsnCount, insn_count);
  dev->WriteReg(kRegInsnAddr, static_cast<uint32_t>(insn_phy_addr));
  for (uint32_t off = kRegPtrFirst; off <= kRegPtrLast; off += 4) dev->WriteReg(off, 0);
  __sync_synchronize();          // instruction/data writes must land before launch
  dev->WriteReg(kRegCtrl, kCtrlLaunch);

  for (uint32_t i = 0; i < wait_cycles; ++i) {
    if (dev->ReadReg(kRegCtrl) & kCtrlFinish) {
      __sync_synchronize();      // results are visible before we return
      return 0;
    }
  }
  LOG(FATAL) << "VTA: timed out after " << wait_cycles << " polls waiting for completion "
             << "(insn_count=" << insn_count << ")";
  return 1;
}
