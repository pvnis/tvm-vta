# VTA runs on its own 100 MHz PLL output while the interconnects it talks to run on
# FIC_0_CLK at 125 MHz. The only paths between the two domains go through the clock-domain
# crossing logic inside CoreAXI4Interconnect (DMA_INITIATOR master port 1 and
# FIC0_INITIATOR slave port 2), so the domains must be declared unrelated - otherwise the
# tools try to time through the synchronizers and spend placement effort on paths that are
# false by construction.
set_clock_groups -name {VTA_vs_FIC0} -asynchronous \
    -group [ get_clocks { FIC_0_PERIPHERALS_0/VTA_CCC_0/pll_inst_0/OUT0 } ] \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0 } ]
