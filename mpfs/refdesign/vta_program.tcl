open_project -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/MPFS_DISCOVERY.prjx}
puts "PROGRAM: programming device"
run_tool -name {PROGRAMDEVICE}
save_project
puts "PROGRAM: DONE"
