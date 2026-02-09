# TODO (v2)

## Resolved by persistence/parallelism migration

- [x] Add periodic worker heartbeat updates during long-running jobs
  → Replaced by machine checkpoints. Every state transition is checkpointed.

- [x] Add stale-processing reaper path in v2 scheduler
  → Runner auto-releases stale `prepping` (>60min) and `analyzing` (>120min) executions.

- [x] Prevent permanent slot loss from orphaned workers
  → No workers. Machine execution IS the job. Semaphore controls concurrency.

- [x] Decouple from shared arxiv DB for write operations
  → V2 executions DB is separate. Arxiv DB is read-only (seed + corpus signals).

## Budget optimization

- [ ] Track actual request counts per machine run (not estimates)
  - Currently using fixed estimates per phase.
  - Hook-level accounting would make budget remaining accurate.

- [ ] End-of-day prep burst
  - When nearing day end with remaining budget, switch to `--prep-only`.

- [ ] Expensive model availability tracking
  - Track expensive model failures in v2 DB.
  - Auto-shift to prep-only during sustained expensive-model outage.

## Rate limiting

- [ ] Add daemon-level 429 density detector
  - Pause analysis when circuit opens; resume after cooldown.

- [ ] Backoff tuning
  - Re-evaluate current retry backoffs for OpenRouter behavior.

## Quality

- [ ] Validate report quality at scale
  - Run 20+ papers and compare quality to v1.

- [x] Fix FTS query construction for `collect_corpus_signals`
  - Hyphenated terms are now quoted to avoid `no such column` parsing failures.

- [ ] Improve terminology tagging
  - Consider section weighting and cross-paper normalization.

## Infrastructure

- [x] Move machine checkpoints from file backend to v2 SQLite DB
- [x] Move machine lock state from file locks to v2 SQLite lease table

- [ ] Add lightweight debug mode to suppress noisy OTEL metric dumps in logs

- [ ] Add `--resume-only` flag to runner
  - Only resume incomplete executions, don't start new ones.

- [ ] Checkpoint retention/cleanup policy
  - Prune terminal checkpoint snapshots regularly (`done`/`failed`).
  - Periodic DB compaction (`VACUUM INTO`) during downtime.

- [ ] Config version bump
  - Machine configs still use `spec_version: "1.0.0"`; SDK is 1.1.0.

## SDK / FlatMachines follow-ups

- [ ] `LocalFileBackend` missing `list(prefix)` method from spec
  - Runner had to work around with direct scanning.

- [ ] Peer machines should inherit parent persistence backend by default
  - Important for consistency across nested machine invocation.

- [ ] Surface `AgentResult.rate_limit` to hooks/context
  - Needed for better adaptive scheduling and budget control.

- [ ] Guard for action hooks returning partial context
  - Detect unexpected key loss after `on_action`.

## V1 unwinding

- [ ] Remove `config/machine.yml` (superseded by prep + expensive + wrap)
- [ ] Remove `src/research_paper_analysis_v2/main.py` if no longer needed
- [ ] Confirm v1 `research_paper_analysis/` can be removed independently

## Reference (rate-limit sample)

- Example historical signal from logs:
  - `RateLimitError ... free-models-per-min ... X-RateLimit-Limit/Remaining/Reset`
