# TODOs

## SDK / FlatMachines follow-ups
- [ ] Add guard for action hooks returning partial context (detect key loss after `on_action`).
- [ ] Add optional merge semantics for action outputs (e.g., `action_merge: true`).
- [ ] Add strict mode to fail if required context keys disappear after action state.
- [ ] Improve docs: explicitly state that `on_action` must return full context (current behavior).

## research_paper_analysis_v2 follow-ups
- [ ] Add concise final worker completion log line (`queue_id`, `paper_id`, `summary_path`).
- [ ] Add a smoke test run path that executes one queued paper and asserts report persistence.
- [ ] Add a lightweight debug mode to suppress noisy OTEL metric dumps in worker logs.
- [ ] Confirm OpenRouter model IDs against account-visible slugs and document known-good values.
