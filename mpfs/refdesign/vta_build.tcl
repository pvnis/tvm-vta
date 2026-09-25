open_project -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/MPFS_DISCOVERY.prjx}
puts "VTABUILD: synthesize"
run_tool -name {SYNTHESIZE}
puts "VTABUILD: place and route"
run_tool -name {PLACEROUTE}
puts "VTABUILD: verify timing"
run_tool -name {VERIFYTIMING}
save_project
puts "VTABUILD: DONE"
