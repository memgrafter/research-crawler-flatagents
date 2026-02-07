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
