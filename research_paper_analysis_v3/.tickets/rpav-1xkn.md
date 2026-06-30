---
id: rpav-1xkn
status: open
deps: [rpav-b3m1, rpav-e50g]
links: []
created: 2026-06-13T06:48:02Z
type: feature
priority: 2
assignee: memgrafter
---
# Write main runner (scheduler)

run.py that wires prep → analyzer: continuous priority scheduling like v2 but with only 2 phases (prep, analyzer). Needs DB schema for v3 executions table. Handles --workers, --daemon, --prep-only flags.
