puts "TCL_BEGIN: VTA integration"
# Attach the VTA accelerator to the Discovery Kit reference design, following the pattern
# Microchip uses for SmartHLS accelerators (script_support/additional_configurations/smarthls):
#   control regs : MSS -> FIC0 -> FIC0_INITIATOR slave port 2 @ 0x6002_0000 (32-bit AXI4Lite, already so configured in this design)
#   DMA          : VTA master -> DMA_INITIATOR master port 1 -> FIC0 -> MSS/DDR
# Both sit in the FIC_0_PERIPHERALS SmartDesign, clocked by FIC_0_CLK (125 MHz).
open_project -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/MPFS_DISCOVERY.prjx}

import_files -convert_EDN_to_HDL 0 -hdl_source {/home/dmd/polarfire_sandbox/refdesign-vta/vta_hw/VTA.DefaultPynqConfig.sv}
build_design_hierarchy

create_hdl_core -file {hdl/VTA.DefaultPynqConfig.sv} -module {XilinxShell} -library {work} -package {}
hdl_core_add_bif -hdl_core_name {XilinxShell} -bif_definition {AXI4:AMBA:AMBA4:master} -bif_name {m_axi_gmem} -signal_map {\
"AWVALID:m_axi_gmem_AWVALID" \
"AWREADY:m_axi_gmem_AWREADY" \
"AWADDR:m_axi_gmem_AWADDR" \
"AWID:m_axi_gmem_AWID" \
"AWUSER:m_axi_gmem_AWUSER" \
"AWLEN:m_axi_gmem_AWLEN" \
"AWSIZE:m_axi_gmem_AWSIZE" \
"AWBURST:m_axi_gmem_AWBURST" \
"AWCACHE:m_axi_gmem_AWCACHE" \
"AWPROT:m_axi_gmem_AWPROT" \
"AWQOS:m_axi_gmem_AWQOS" \
"AWREGION:m_axi_gmem_AWREGION" \
"WVALID:m_axi_gmem_WVALID" \
"WREADY:m_axi_gmem_WREADY" \
"WDATA:m_axi_gmem_WDATA" \
"WSTRB:m_axi_gmem_WSTRB" \
"WLAST:m_axi_gmem_WLAST" \
"WUSER:m_axi_gmem_WUSER" \
"BVALID:m_axi_gmem_BVALID" \
"BREADY:m_axi_gmem_BREADY" \
"BRESP:m_axi_gmem_BRESP" \
"BID:m_axi_gmem_BID" \
"BUSER:m_axi_gmem_BUSER" \
"ARVALID:m_axi_gmem_ARVALID" \
"ARREADY:m_axi_gmem_ARREADY" \
"ARADDR:m_axi_gmem_ARADDR" \
"ARID:m_axi_gmem_ARID" \
"ARUSER:m_axi_gmem_ARUSER" \
"ARLEN:m_axi_gmem_ARLEN" \
"ARSIZE:m_axi_gmem_ARSIZE" \
"ARBURST:m_axi_gmem_ARBURST" \
"ARCACHE:m_axi_gmem_ARCACHE" \
"ARPROT:m_axi_gmem_ARPROT" \
"ARQOS:m_axi_gmem_ARQOS" \
"ARREGION:m_axi_gmem_ARREGION" \
"RVALID:m_axi_gmem_RVALID" \
"RREADY:m_axi_gmem_RREADY" \
"RDATA:m_axi_gmem_RDATA" \
"RRESP:m_axi_gmem_RRESP" \
"RLAST:m_axi_gmem_RLAST" \
"RID:m_axi_gmem_RID" \
"RUSER:m_axi_gmem_RUSER" }

hdl_core_add_bif -hdl_core_name {XilinxShell} -bif_definition {AXI4:AMBA:AMBA4:slave} -bif_name {s_axi_control} -signal_map {\
"AWVALID:s_axi_control_AWVALID" \
"AWREADY:s_axi_control_AWREADY" \
"AWADDR:s_axi_control_AWADDR" \
"WVALID:s_axi_control_WVALID" \
"WREADY:s_axi_control_WREADY" \
"WDATA:s_axi_control_WDATA" \
"WSTRB:s_axi_control_WSTRB" \
"BVALID:s_axi_control_BVALID" \
"BREADY:s_axi_control_BREADY" \
"BRESP:s_axi_control_BRESP" \
"ARVALID:s_axi_control_ARVALID" \
"ARREADY:s_axi_control_ARREADY" \
"ARADDR:s_axi_control_ARADDR" \
"RVALID:s_axi_control_RVALID" \
"RREADY:s_axi_control_RREADY" \
"RDATA:s_axi_control_RDATA" \
"RRESP:s_axi_control_RRESP" }


# A dedicated 100 MHz PLL for VTA. The 125 MHz build meets timing by 0.139 ns on the path
# from the inp scratchpad LSRAM to the MAC array multiplier inputs (the design's own
# max_timing report, slow_lv_ht), which is why GEMM is intermittently wrong on hardware
# while the ALU - which never uses that path - is always correct. At 100 MHz that path is
# not even critical: the previous 100 MHz build measured +1.853 ns worst VTA slack.
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/VTA_CCC.tcl

set sd FIC_0_PERIPHERALS
open_smartdesign -sd_name $sd
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/FIC0_INITIATOR.vta.tcl
sd_update_instance -sd_name $sd -instance_name {FIC0_INITIATOR}
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/DMA_INITIATOR.vta.tcl
sd_update_instance -sd_name $sd -instance_name {DMA_INITIATOR}

# VTA runs on its own 100 MHz clock and crosses into the 125 MHz FIC0 domain inside the
# two CoreAXI4Interconnects, which is what SLAVE2_CLOCK_DOMAIN_CROSSING (the control port)
# and MASTER1_CLOCK_DOMAIN_CROSSING (the DMA port) enable in the .vta.tcl parameter files
# sourced above. Enabling CDC exposes one clock pin per crossed port - S_CLK2 and M_CLK1 -
# and no per-port reset.
#
# The PLL's reference is ACLK (FIC_0_CLK), NOT the board's REF_CLK_50MHz pad: taking the pad
# adds a second load to it, Libero then inserts a CLKINT buffer, and the MAIN CCC loses its
# dedicated CCC_SW_CLKIN route - which changes the reference path of every FIC clock in the
# design and stops the board booting.
sd_instantiate_component -sd_name $sd -component_name {VTA_CCC} -instance_name {VTA_CCC_0}
sd_connect_pins -sd_name $sd -pin_names {"ACLK" "VTA_CCC_0:REF_CLK_0"}

# VTA needs a reset synchronised to its own clock and released once its PLL locks.
# EXT_RST_N is ARESETN directly - deliberately NOT the MSS_DLL_LOCKS-gated reset that the
# fabric CORERESETs in CLOCKS_AND_RESETS use.
sd_instantiate_component -sd_name $sd -component_name {CORERESET} -instance_name {VTA_RESET}
sd_connect_pins -sd_name $sd -pin_names {"VTA_CCC_0:OUT0_FABCLK_0" "VTA_RESET:CLK"}
sd_connect_pins -sd_name $sd -pin_names {"ARESETN" "VTA_RESET:EXT_RST_N"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_CCC_0:PLL_LOCK_0" "VTA_RESET:PLL_LOCK"}
sd_connect_pins_to_constant -sd_name $sd -pin_names {VTA_RESET:BANK_x_VDDI_STATUS} -value {VCC}
sd_connect_pins_to_constant -sd_name $sd -pin_names {VTA_RESET:BANK_y_VDDI_STATUS} -value {VCC}
sd_connect_pins_to_constant -sd_name $sd -pin_names {VTA_RESET:SS_BUSY} -value {GND}
sd_connect_pins_to_constant -sd_name $sd -pin_names {VTA_RESET:INIT_DONE} -value {VCC}
sd_connect_pins_to_constant -sd_name $sd -pin_names {VTA_RESET:FF_US_RESTORE} -value {GND}
sd_connect_pins_to_constant -sd_name $sd -pin_names {VTA_RESET:FPGA_POR_N} -value {VCC}

sd_instantiate_hdl_core -sd_name $sd -hdl_core_name {XilinxShell} -instance_name {VTA_0}
sd_connect_pins -sd_name $sd -pin_names {"FIC0_INITIATOR:AXI4mslave2" "VTA_0:s_axi_control"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_0:m_axi_gmem" "DMA_INITIATOR:AXI4mmaster1"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_CCC_0:OUT0_FABCLK_0" "VTA_0:ap_clk"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_RESET:FABRIC_RESET_N" "VTA_0:ap_rst_n"}

# The interconnect side of each crossing runs on VTA's clock.
sd_connect_pins -sd_name $sd -pin_names {"VTA_CCC_0:OUT0_FABCLK_0" "FIC0_INITIATOR:S_CLK2"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_CCC_0:OUT0_FABCLK_0" "DMA_INITIATOR:M_CLK1"}
save_smartdesign -sd_name $sd
build_design_hierarchy
generate_component -component_name {FIC_0_PERIPHERALS} -recursive 1

# Nothing outside this SmartDesign changes: VTA runs on the clock FIC_0_PERIPHERALS
# already receives.
build_design_hierarchy
generate_component -component_name {MPFS_DISCOVERY_KIT} -recursive 1
puts "VTA: integrated into $sd on its own 100 MHz clock, CDC in the interconnects"

# Constraints. The new PLL introduces a clock that does not exist in the base design, so
# derive_constraints_sdc MUST run or VTA's paths are analysed against nothing and the timing
# report is meaningless.
#
# The trap here, which cost this project weeks: organize_tool_files REPLACES a tool's
# constraint list rather than appending to it. An earlier version called it for PLACEROUTE
# with only the two SDC files, silently dropping all the I/O PDCs - including the board's
# 50 MHz oscillator assignment (set_io REF_CLK_50MHz -pin_name R18) - so the reference clock
# was auto-placed on E12 and the fabric came up with no usable clock. Every rebuilt design
# then hung at "Initializing Mi-V IHC V2", which was misread for weeks as CDC, PLL and
# timing problems. The fix is not to skip the call, it is to pass the COMPLETE list.
import_files -sdc {/home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/vta_clocks.sdc}
build_design_hierarchy
derive_constraints_sdc

set pd /home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY
set io_pdcs [list \
    "$pd/constraint/io/MPFS_DISCOVERY_KIT_BANK_SETTINGS.pdc" \
    "$pd/constraint/io/MPFS_DISCOVERY_KIT_BOARD_MISC.pdc" \
    "$pd/constraint/io/MPFS_DISCOVERY_MAC.pdc" \
    "$pd/constraint/io/MPFS_DISCOVERY_mikroBUS.pdc" \
    "$pd/constraint/io/MPFS_DISCOVERY_RPi.pdc" \
    "$pd/constraint/io/MPFS_DISCOVERY_UARTS.pdc" \
    "$pd/constraint/io/MPFS_DISCOVERY_7_SEG.pdc" \
    "$pd/constraint/fp/SW_PLL.pdc"]
set sdcs [list \
    "$pd/constraint/MPFS_DISCOVERY_KIT_derived_constraints.sdc" \
    "$pd/constraint/vta_clocks.sdc"]

# PLACEROUTE owns both kinds; the timing tools own only the SDCs.
set args {}
foreach f [concat $io_pdcs $sdcs] { lappend args -file $f }
eval organize_tool_files -tool {PLACEROUTE} $args -module {MPFS_DISCOVERY_KIT::work} -input_type {constraint}
set args {}
foreach f $sdcs { lappend args -file $f }
eval organize_tool_files -tool {SYNTHESIZE} $args -module {MPFS_DISCOVERY_KIT::work} -input_type {constraint}
eval organize_tool_files -tool {VERIFYTIMING} $args -module {MPFS_DISCOVERY_KIT::work} -input_type {constraint}
puts "VTA: constraints associated - [llength $io_pdcs] pdc + [llength $sdcs] sdc"
save_project
puts "TCL_END: VTA integration"
