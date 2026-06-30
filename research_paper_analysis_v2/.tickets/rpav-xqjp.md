---
id: rpav-xqjp
status: open
deps: [rpav-h362]
links: [rpav-uj2i]
created: 2026-06-13T03:02:21Z
type: task
priority: 2
assignee: memgrafter
tags: [research, runner]
---
# Wire unified machine into run.py scheduler

Update run.py to use unified_machine instead of three-phase pipeline. Remove expensive/wrap phase distinction. Update DB schema for new status values if needed. Test with a few papers and compare output quality + token usage.
