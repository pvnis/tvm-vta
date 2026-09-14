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


# VTA does not run on FIC_0_CLK. At 125 MHz the post-layout worst slack was +0.056 ns -
# 0.7% of the period - and on hardware the accelerator intermittently stopped completing
# programs, so it is given margin instead. Its two AXI ports cross back into the 125 MHz
# FIC0 domain inside CoreAXI4Interconnect, which does the CDC itself when a port has
# CLOCK_DOMAIN_CROSSING enabled (the arrangement Microchip uses for VectorBlox here).

set sd FIC_0_PERIPHERALS
open_smartdesign -sd_name $sd
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/FIC0_INITIATOR.vta.tcl
sd_update_instance -sd_name $sd -instance_name {FIC0_INITIATOR}
source /home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/DMA_INITIATOR.vta.tcl
sd_update_instance -sd_name $sd -instance_name {DMA_INITIATOR}

# VTA runs on FIC_3_CLK (50 MHz), borrowed from the existing clock tree - NO new PLL.
#
# Two attempts at 100 MHz with a dedicated PLL both left the board unbootable, HSS hanging on
# its first fabric-peripheral access (Mi-V IHC, which is on FIC3). The second attempt was
# connectivity-identical to the working design at the top level and still failed, so the
# cause is inside this SmartDesign, and a fabric PLL referenced from an existing critical
# clock is the common factor. The design has no 100 MHz clock to borrow, so keeping 100 MHz
# would mean keeping the suspect; 50 MHz still gives VTA a 20 ns period against the 8 ns it
# was failing at.
#
# FIC_3_CLK is the safest clock in the design to borrow: its MSS DLL is DISABLED
# (FIC_3_EMBEDDED_DLL_USED false), so adding fabric loads to it cannot break the
# MSS_DLL_LOCKS chain that gates the reset of every fabric peripheral.
sd_create_scalar_port -sd_name $sd -port_name {VTA_CLK} -port_direction {IN}
sd_create_scalar_port -sd_name $sd -port_name {VTA_ARESETN} -port_direction {IN}

sd_instantiate_hdl_core -sd_name $sd -hdl_core_name {XilinxShell} -instance_name {VTA_0}
sd_connect_pins -sd_name $sd -pin_names {"FIC0_INITIATOR:AXI4mslave2" "VTA_0:s_axi_control"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_0:m_axi_gmem" "DMA_INITIATOR:AXI4mmaster1"}
# VTA and the fabric-facing side of both crossings run on the borrowed clock; the
# interconnects' own ACLK stays on FIC_0_CLK at 125 MHz.
sd_connect_pins -sd_name $sd -pin_names {"VTA_CLK" "VTA_0:ap_clk" \
    "DMA_INITIATOR:M_CLK1" "FIC0_INITIATOR:S_CLK2"}
sd_connect_pins -sd_name $sd -pin_names {"VTA_ARESETN" "VTA_0:ap_rst_n"}
save_smartdesign -sd_name $sd
# Regenerate before touching the top level: until the hierarchy is rebuilt, the
# FIC_0_PERIPHERALS_0 instance does not expose the ports added above.
build_design_hierarchy
generate_component -component_name {FIC_0_PERIPHERALS} -recursive 1

# Join the existing FIC_3 clock and reset nets by naming a pin already on each: those nets
# are already driven, and connecting a driver a second time is rejected.
set top MPFS_DISCOVERY_KIT
open_smartdesign -sd_name $top
sd_update_instance -sd_name $top -instance_name {FIC_0_PERIPHERALS_0}
sd_connect_pins -sd_name $top -pin_names {"FIC_3_PERIPHERALS_0:PCLK" "FIC_0_PERIPHERALS_0:VTA_CLK"}
sd_connect_pins -sd_name $top -pin_names {"FIC_3_PERIPHERALS_0:PRESETN" "FIC_0_PERIPHERALS_0:VTA_ARESETN"}
save_smartdesign -sd_name $top
build_design_hierarchy
generate_component -component_name {MPFS_DISCOVERY_KIT} -recursive 1
puts "VTA: integrated into $sd on FIC_3_CLK (50 MHz), CDC in the interconnects"

# Re-derive timing constraints, then tell the tools
# that the VTA and FIC0 domains are unrelated - the only paths between them go through the
# interconnects' CDC synchronizers.
# The hierarchy has to be rebuilt after regenerating the top component, otherwise deriving
# constraints cannot find the top level.
build_design_hierarchy
set_root -module {MPFS_DISCOVERY_KIT::work}
derive_constraints_sdc
# import_files copies the file into the project's constraint directory, which is the path
# organize_tool_files expects; create_links would leave it outside the project.
import_files -convert_EDN_to_HDL 0 -library {work} \
    -sdc {/home/dmd/polarfire_sandbox/refdesign-vta/script_support/additional_configurations/vta/vta_clocks.sdc}
foreach tool {SYNTHESIZE PLACEROUTE VERIFYTIMING} {
    organize_tool_files -tool $tool \
        -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/constraint/MPFS_DISCOVERY_KIT_derived_constraints.sdc} \
        -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/constraint/vta_clocks.sdc} \
        -module {MPFS_DISCOVERY_KIT::work} \
        -input_type {constraint}
}
save_project
puts "TCL_END: VTA integration"
