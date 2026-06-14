---
id: rpav-fg5j
status: open
deps: []
links: []
created: 2026-06-13T07:16:50Z
type: bug
priority: 2
assignee: memgrafter
---
# FlatMachine lifecycle_hooks should also serve as state-level hooks fallback

When lifecycle_hooks is set in machine config (e.g. lifecycle_hooks: v3-hooks), it is only used for on_machine_start/on_machine_end. State actions fall back to _legacy_global_hooks which is None unless passed as a constructor arg. This means every action state must declare hooks: explicitly, or actions are unhandled. Fix: _get_state_hooks should also check _lifecycle_hooks as fallback before returning noop.
