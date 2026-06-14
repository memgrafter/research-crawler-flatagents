---
id: rpav-hggh
status: open
deps: []
links: []
created: 2026-06-13T07:32:25Z
type: bug
priority: 2
assignee: memgrafter
---
# FlatMachine _launch_and_write should propagate hooks_registry to launched machines by default

When a machine launches peer machines via _launch_and_write (single or parallel), it passes agent_registry, persistence, lock, result_backend but NOT hooks_registry. This means launched machines cannot resolve custom hook actions because their hooks_registry is empty. The base FlatMachine._launch_and_write at line ~994 passes agent_registry=self.agent_registry but not hooks_registry. Fix: add hooks_registry=self.hooks_registry to the peer constructor call in _launch_and_write.
