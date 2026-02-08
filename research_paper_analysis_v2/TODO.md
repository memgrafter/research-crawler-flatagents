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
  - Currently using fixed estimates (1 req/prep, 7 req/analysis).
  - Hook could increment `daily_usage` after each agent call via `on_state_exit`.
  - Would enable more accurate budget remaining calculations.

- [ ] End-of-day prep burst
  - When approaching end of day and budget remains, switch to `--prep-only` to
    fill buffer for next day's analysis.
  - Could be a cron job or a time-aware check in the daemon loop.

- [ ] Expensive model availability tracking
  - Track success/failure of expensive model calls in v2 DB.
  - Use rolling window to detect sustained unavailability.
  - When unavailable, automatically switch to prep-only mode.
  - When available again, resume analysis.

## Rate limiting

- [ ] Add daemon-level 429 density detector
  - Monitor analysis machine failures for rate-limit patterns.
  - When circuit opens, pause analysis and run prep instead.
  - Resume analysis after cooldown period.

- [ ] Backoff tuning
  - Current retry backoffs: `[2, 8, 16]` for prep, `[2, 8, 16, 35]` for expensive wrappers.
  - Monitor if these are appropriate for OpenRouter free tier.
  - Consider longer backoffs for the expensive model.

## Quality

- [ ] Validate report quality at scale
  - Run 20+ papers and compare output quality to v1 reports.
  - Check that parallel execution doesn't degrade why_hypothesis or reproduction quality.

- [ ] Fix FTS query construction for `collect_corpus_signals`
  - Root cause: hyphenated terms in MATCH queries (`in-context`, `model-free`, `trade-offs`, etc.)
    are emitted unquoted and interpreted as column expressions by SQLite FTS5.
  - Symptoms: warnings like `FTS neighbor query failed (no such column: context/free/offs/...)`
    and frequent fallback to LIKE search.
  - Fix approach: quote/escape hyphenated terms (or all terms) in `_build_fts_query`.
  - Validation: zero `no such column:` FTS warnings in prep logs for a sample batch.

- [ ] Improve terminology tagging
  - Current scoring uses paper text + corpus neighbors + taxonomy.
  - Consider adding section header weighting.
  - Consider cross-paper tag normalization.

## Infrastructure

- [ ] Add lightweight debug mode to suppress noisy OTEL metric dumps in logs

- [ ] Add `--resume-only` flag to runner
  - Only resume incomplete executions, don't start new ones.
  - Useful for recovering after crashes without consuming budget.

- [ ] Checkpoint cleanup
  - Completed executions leave checkpoints on disk.
  - Add `--cleanup-checkpoints` flag to remove checkpoints for done/failed executions.

- [ ] Config version bump
  - Machine configs use `spec_version: "1.0.0"` but SDK is 1.1.0.
  - Produces a validation warning. Bump when ready.

## V1 unwinding

- [ ] Remove `config/machine.yml` (superseded by prep + expensive + wrap machines)
- [ ] Remove `src/research_paper_analysis_v2/main.py` if no longer needed
- [ ] Confirm v1 `research_paper_analysis/` can be removed independently
