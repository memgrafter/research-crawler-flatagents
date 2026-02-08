# SDK / FlatMachines follow-ups

- [ ] `LocalFileBackend` is missing `list(prefix)` method from the spec.
  Runner works around this with `Path.glob("*/latest")`.
  Should be added to the SDK.

- [ ] Peer machines don't inherit parent's persistence backend.
  Wrapper machines (why_hypothesis, reproduction) use memory persistence by default.
  Not a problem for single-agent wrappers, but would matter for complex child machines.

- [ ] Rate limit info from `AgentResult.rate_limit` is not surfaced to hooks or context.
  `_accumulate_agent_metrics` tracks cost/usage but drops rate limit state.
  Would enable budget tracking at the machine level instead of the runner.

- [ ] Guard for action hooks returning partial context (detect key loss after `on_action`).

- [ ] Config `spec_version: "1.0.0"` triggers validation warning with SDK 1.1.0.
  Non-blocking but noisy.

- [ ] (Project) Escape/quote hyphenated FTS terms in `collect_corpus_signals` query builder.
  Current MATCH strings can raise `no such column: ...` and force LIKE fallback.

## Project runtime priorities

- [ ] PRIORITY NEXT: Move checkpoint persistence from files to v2 SQLite DB.
  Target: remove high-volume `.step_*.tmp` / `.latest.tmp` checkpoint writes and reduce FD pressure.

- [ ] Follow-up: Move execution lock state from file locks to v2 SQLite lease rows.
  Keep separate from checkpoint migration; do only if needed after checkpoint DB migration.

## Rate limit headers

2026-02-07 16:59:30 - flatagents.flatagent - WARNING - LLM call failed: RateLimitError - litellm.RateLimitError: RateLimitError: OpenrouterException - {"error"
:{"message":"Rate limit exceed (status=429) | headers={}
2026-02-07 16:59:30 - flatagents.monitor.report-assembler - INFO - Agent report-assembler completed in 483.78ms - success                                      2026-02-07 16:59:30 - flatmachines.execution - WARNING - Attempt 1/4 failed: RateLimitError: litellm.RateLimitError: RateLimitError: OpenrouterException - {"er
ror":{"message":"Rate limit exceeded: free-models-per-min. ","code":429,"metadata":{"headers":{"X-RateLimit-Limit":"20","X-RateLimit-Remaining":"0","X-RateLimit-Reset":"1770512400000"},"provider_name":null}},"user_id":"user_2eilynMFgVA7gHumgjhRl9EEl0E"}
2026-02-07 16:59:30 - flatmachines.execution - INFO - Retrying in 1.8s...
