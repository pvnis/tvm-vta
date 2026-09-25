# The FIC clock domains are mutually asynchronous. The reference design ships the same
# declaration in script_support/constraints/fic_clocks.sdc, but nothing sources that file,
# so it is repeated here for the domains this design actually uses.
#
# VTA is back on FIC_0_CLK (the dedicated-PLL + CDC topology regressed the ALU: it wedged
# on the third program every time, with the third one computing CORRECTLY but never
# reporting completion), so there is no VTA-specific crossing to declare.
set_clock_groups -name {FIC3_vs_FIC0} -asynchronous \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT3 } ] \
    -group [ get_clocks { CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0 } ]
