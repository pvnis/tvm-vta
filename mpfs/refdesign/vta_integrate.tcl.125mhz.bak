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


set sd FIC_0_PERIPHERALS
open_smartdesign -sd_name $sd
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/FIC0_INITIATOR.vta.tcl
sd_update_instance -sd_name $sd -instance_name {FIC0_INITIATOR}
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/DMA_INITIATOR.vta.tcl
sd_update_instance -sd_name $sd -instance_name {DMA_INITIATOR}

# VTA is connected directly to the two system interconnects and runs on ACLK (FIC_0_CLK,
# 125 MHz) - the topology of the known-good build.
#
# Giving VTA a slower clock is still the right fix for the timing marginality, but every
# route tried so far is blocked: enabling CLOCK_DOMAIN_CROSSING on ports of the SHARED
# interconnects makes the board unbootable (three attempts, three different clock sources),
# and moving the crossing into dedicated 1x1 bridges - Microchip's own VectorBlox topology -
# fails at the control path, where SmartDesign rejects the connection between
# FIC0_INITIATOR:AXI4mslave2 and the bridge's slave interface as "not compatible" even with
# the signal sets, widths (ARADDR 38, ARID 8, ARLEN 8, ARUSER 1) and AXI4 types all matched.
# The DMA-side bridge configures and connects fine; it is the control path that blocks.
sd_instantiate_hdl_core -sd_name $sd -hdl_core_name {XilinxShell} -instance_name {VTA_0}
sd_connect_pins -sd_name $sd -pin_names {"FIC0_INITIATOR:AXI4mslave2" "VTA_0:s_axi_control"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_0:m_axi_gmem" "DMA_INITIATOR:AXI4mmaster1"}
sd_connect_pins -sd_name $sd -pin_names {"ACLK" "VTA_0:ap_clk"}
sd_connect_pins -sd_name $sd -pin_names {"ARESETN" "VTA_0:ap_rst_n"}
save_smartdesign -sd_name $sd
build_design_hierarchy
generate_component -component_name {FIC_0_PERIPHERALS} -recursive 1

# Nothing outside this SmartDesign changes: VTA runs on the clock FIC_0_PERIPHERALS
# already receives.
build_design_hierarchy
generate_component -component_name {MPFS_DISCOVERY_KIT} -recursive 1
puts "VTA: integrated into $sd on ACLK (125 MHz), direct connections"

# NO constraint manipulation here. An earlier version called derive_constraints_sdc and
# then organize_tool_files with only two SDC files, which REPLACES each tool's constraint
# list - silently dropping all eight I/O PDCs and the floorplan PDC. The board's 50 MHz
# oscillator constraint (set_io REF_CLK_50MHz -pin_name R18 -fixed true) was among them, so
# the reference clock got auto-placed on another pin and the fabric came up without a usable
# clock. That is what made every rebuilt design hang at "Initializing Mi-V IHC V2" - HSS's
# first access to a fabric peripheral - and it was misread for weeks as CDC, PLL and timing
# problems. The base design already derives and associates its constraints correctly; adding
# VTA introduces no new clock, so there is nothing to add here.
save_project
puts "TCL_END: VTA integration"
