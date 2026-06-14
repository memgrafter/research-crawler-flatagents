---
id: rpav-ow08
status: open
deps: []
links: []
created: 2026-06-13T08:11:51Z
type: epic
priority: 2
assignee: memgrafter
---
# FlatMachine machine-to-machine I/O: design flaws discovered in v3 fan-out pipeline

Building the v3 analyzer pipeline exposed multiple machine-machine I/O issues that make parallel execution fragile and hard to debug. These are FlatMachine SDK issues, not v3-specific.

## Issues Found

### 1. hooks_registry not propagated by default (FIXED in recent commit but V3FlatMachine still overrides)
The base FlatMachine._launch_and_write now passes hooks_registry, but our custom V3FlatMachine override may be stale. More importantly: the hooks_registry propagation was missing for a long time and no test caught it. Need regression test.

### 2. output_to_context routing is opaque and hard to debug
Parallel execution returns {machine_name: output} where each output is the child's final state output. The routing expressions (e.g., output.narrative_lead.narrative_lead) are bare path references resolved via _resolve_path — no error if a key is missing, just silently returns None. No validation that the expected keys exist in the child output.

### 3. Child machine config_dir breaks relative paths in shared agent configs
When a sub-machine loads an agent from ../agents/analyzer.flatagent.yml, the agent's prompt path (./prompts/...) resolves relative to the sub-machine's config_dir (config/machines/), not the agent file's own directory. This forces either:
- Duplicate agent configs per machine directory with different relative paths
- Absolute paths in agent configs (fragile)
- A shared agent registry that avoids re-loading configs

### 4. profiles_file discovery breaks with relative paths in child machines
discover_profiles_file resolves relative explicit_path against config_dir. Child machines have different config_dir, so a relative profiles path from the parent becomes invalid in the child. Only absolute paths work — but users naturally write relative paths.

### 5. Silent failures in parallel execution
When a sub-machine errors, the error is stored as {"_error": ...} in results but the main machine continues. There's no on_error handler for individual parallel branches — only for the whole state. Empty/None section outputs are indistinguishable from missing data.

### 6. No schema validation for machine I/O
Nothing enforces that a child machine outputs the keys the parent expects. A typo in output_to_context (e.g., wrong key name) silently produces None in context, cascading into empty sections downstream with no trace of where it went wrong.

## Proposed Improvements

1. **Machine I/O schema**: Allow machines to declare expected input/output schemas. Validate at launch time that the child's output matches what the parent expects.

2. **Error propagation in parallel branches**: Add per-machine on_error handlers in parallel states. Propagate errors up if a critical branch fails.

3. **Agent config path resolution**: Agent prompt/system paths should resolve relative to the agent file's own directory, not the loading machine's config_dir. Or: provide a shared agent registry that caches loaded agents by name.

4. **Profiles path resolution**: profiles_file should be passed as an absolute path or resolved once at the root and propagated as-is (not re-resolved per child).

5. **Better debug output for output_to_context**: Log what was routed and what was None/missing. Warn when a routing expression resolves to None.

6. **Message validation**: Add a lightweight message passing layer between machines with typed payloads instead of raw dicts.
