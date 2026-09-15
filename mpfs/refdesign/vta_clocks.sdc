# The FIC clock domains are mutually asynchronous. The reference design ships the same
# declaration in script_support/constraints/fic_clocks.sdc, but nothing sources that file,
# so it is repeated here for the domains this design actually uses.
#
# VTA currently runs on FIC_0_CLK, so there is no VTA-specific crossing to declare. When VTA
# is eventually moved to its own clock, add its domain to a group here.
set_clock_groups -name {FIC3_vs_FIC0} -asynchronous \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT3 } ] \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0 } ]
