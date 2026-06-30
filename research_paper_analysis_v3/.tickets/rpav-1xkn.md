---
id: rpav-1xkn
status: open
deps: [rpav-e50g]
links: []
created: 2026-06-13T06:48:02Z
type: feature
priority: 2
assignee: memgrafter
---
# Write launch contract

Minimal entry point: given paper_ids + phase, launch paper machines.

The listener_os pattern handles all orchestration — paper machines park on wait_for, signal via send_and_notify, OS activates dispatcher. The launcher just needs to:

1. Read papers by status from v3_executions (get_papers_ready_for_phase)
2. Launch a paper machine per paper with the right input
3. Exit — dispatcher handles resumption

No daemon mode, no priority queues, no worker pools. Whatever invokes us (Python loop, systemd oneshot, cron, container) decides the scheduling strategy.
