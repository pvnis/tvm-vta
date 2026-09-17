# The FIC clock domains are mutually asynchronous. The reference design ships the same
# declaration in script_support/constraints/fic_clocks.sdc, but nothing sources that file,
# so it is repeated here for the domains this design actually uses.
#
# VTA now runs on its own 100 MHz PLL and crosses into FIC_0_CLK inside the two
# CoreAXI4Interconnects, so that crossing is declared asynchronous too. Without this the
# timing engine tries to close paths between the two domains through the CDC FIFOs, which
# are explicitly there to make that unnecessary - and the resulting false violations would
# either fail the build or push the tool into wrecking the real paths trying to fix them.
set_clock_groups -name {FIC3_vs_FIC0} -asynchronous \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT3 } ] \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0 } ]

set_clock_groups -name {VTA_vs_FIC0} -asynchronous \
    -group [ get_clocks { FIC_0_PERIPHERALS_0/VTA_CCC_0/VTA_CCC_0/pll_inst_0/OUT0 } ] \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0 } ]
