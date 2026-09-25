open_project -file {/home/dmd/polarfire_sandbox/refdesign-vta/MPFS_DISCOVERY/MPFS_DISCOVERY.prjx}
puts "PROGDATA: generate programming data"
run_tool -name {GENERATEPROGRAMMINGDATA}
puts "PROGDATA: generate programming file"
run_tool -name {GENERATEPROGRAMMINGFILE}
save_project
puts "PROGDATA: DONE"
