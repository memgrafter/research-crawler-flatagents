---
id: rpav-op1z
status: open
deps: []
links: [rpav-8sq6]
created: 2026-06-30T03:25:50Z
type: feature
priority: 2
assignee: memgrafter
tags: [invoker, concurrency, config]
---
# Config-driven invoker with concurrency slot management

Read a config file with per-phase allocation limits. Check active machines against limits. Launch into unused slots.

Based on scrapped controller machine from epic §7 but simplified: no state machine, just a config reader + slot checker + launcher.

Config:
  prep_slots: 5
  analyzer_slots: 25

Invoker reads v3_executions to count active papers per phase, compares against config, launches new paper machines into free slots.
