---
id: rpav-ohm5
status: open
deps: []
links: []
created: 2026-06-13T07:01:29Z
type: task
priority: 2
assignee: memgrafter
---
# End-to-end test of analyzer machine with FlatMachine runner

Write dry_run_full.py that uses FlatMachine.run() with the analyzer_machine config, passing pre-cleaned paper text (data/1706.03762-clean.txt) as input. Validates full flow: warmup → fan-out → assemble → judge → save. Must work before prep machine/DB exist.
