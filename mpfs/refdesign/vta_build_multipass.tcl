open_project -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/MPFS_DISCOVERY.prjx}
puts "VTABUILD: synthesize"
run_tool -name {SYNTHESIZE}

# Multi-pass place and route, keeping the BEST pass by worst slack rather than stopping at
# the first that meets the constraint. The design closed at only +0.056 ns on a single
# default pass, and the seed-to-seed spread measured on this design during the timing work
# was around 0.2 ns - larger than the entire margin - so the best of several seeds should
# buy back real headroom without changing the design at all.
#
# Rank the passes by the slack of VTA's own clock domain. VIOLATIONS cannot discriminate
# here - every pass has zero - and TIMING is not a legal value (the legal set is
# SLOWEST_CLOCK, SPECIFIC_CLOCK, VIOLATIONS, TOTAL_POWER), so name the FIC0 clock explicitly
# rather than relying on SLOWEST_CLOCK, which ranks by frequency and would pick the 50 MHz
# FIC3 domain that VTA has nothing to do with.
#
# Note the target period stays 8 ns. Over-constraining backfires here - a 7.0 ns run during
# the timing work gave a WORSE result than the 8.0 ns one, because the placer spreads effort
# across hundreds of failing paths instead of the few that matter.
puts "VTABUILD: place and route (multi-pass, best of 5 seeds)"
configure_tool -name {PLACEROUTE} \
    -params {MULTI_PASS_LAYOUT:true} \
    -params {NUM_MULTI_PASSES:5} \
    -params {STOP_ON_FIRST_PASS:false} \
    -params {MULTI_PASS_CRITERIA:SPECIFIC_CLOCK} \
    -params {SPECIFIC_CLOCK:CLOCKS_AND_RESETS_0/CCC_FIC_x_CLK/PF_CCC_C0_0/pll_inst_0/OUT0} \
    -params {SLACK_CRITERIA:WORST_SLACK} \
    -params {START_SEED_INDEX:1} \
    -params {EFFORT_LEVEL:true} \
    -params {REPAIR_MIN_DELAY:true} \
    -params {TDPR:true} \
    -params {GB_DEMOTION:true}
run_tool -name {PLACEROUTE}

puts "VTABUILD: verify timing"
run_tool -name {VERIFYTIMING}
save_project
puts "VTABUILD: DONE"
