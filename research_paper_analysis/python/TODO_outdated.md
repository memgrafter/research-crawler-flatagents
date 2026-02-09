# TODO

## Not Ready (must fix before next run)
- [ ] **Fireworks account suspended**: $5 credit burned. 131 reset jobs will re-poison if workers start. Either fix billing or switch profile back to cerebras (`profiles.yml` override).
- [ ] **1 paper stuck in `processing`**: From a dead worker. Need to reset to pending.
- [ ] **No circuit breaker for provider failures**: Workers poison jobs one-by-one instead of detecting systemic provider outage and halting. 131 papers poisoned overnight from a single billing issue.
- [ ] **Status code lost at machine boundary**: `llm_error_status_code` set in inner machine context doesn't propagate to outer machine. `_is_retryable_failure` can't see it, so provider errors get poisoned instead of retried.
- [ ] **`_is_retryable_failure` missing patterns**: "suspended", "billing", "spending limit", "precondition" not in retryable phrases.
- [ ] **Expression engine `int()` not done**: Only a hooks workaround for `quality_score`. Any future agent returning number-as-string in a YAML condition will hit the same `TypeError`. Need `ast.Call` handler in `flatmachines/expressions/simple.py`.
- [ ] **`done` state outputs `None` for title, key_findings, summary, quality_score**: Context propagation bug in `machine.yml` — values not flowing through `output_to_context` between submachines. `formatted_report` works.

---

## Provider Error Handling (from 2026-02-06 fireworks suspension incident)
- [ ] **Status code lost at machine boundary**: `llm_error_status_code` is set in inner machine context (analyzer/refiner) but doesn't propagate to outer machine (paper_analysis_worker). Only the error text string survives via `context["last_error"]`. Fix: either propagate status code through machine output, or parse it from the error text.
- [ ] **`_is_retryable_failure` missing provider suspension patterns**: Fireworks `PRECONDITION_FAILED: Account suspended` not caught. Add "suspended", "billing", "spending limit", "precondition" to retryable phrases.
- [ ] **Provider-down should suspend all jobs, not poison one-by-one**: When a provider is unreachable/suspended, workers should detect the systemic failure, suspend themselves, and zero the queue — not burn through retries on 131 individual papers. Need a circuit-breaker: after N consecutive provider errors across workers, halt all processing.
- [ ] **Rate limit logic is cerebras-only**: The rate limit detection/gating was built for cerebras 429s. Needs to be provider-agnostic — handle fireworks, openai, anthropic, etc. error shapes.

## Report Processing
- [ ] **Deduplicate reports in `data/`**: Multiple MD files exist for the same arxiv ID from re-runs (2503 MDs vs 2258 done papers). Need to keep only the latest version per arxiv ID. Could be a one-time cleanup script or an ongoing check in `_complete_paper`.

## Frontmatter Bug
- [ ] **`model_profiles_used` in frontmatter is wrong**: Shows only the first 2 profiles (e.g., `default` and `fast`) instead of the actual profiles used during the run. Needs investigation — likely hardcoded or pulled from config rather than tracked during execution.

## Context Propagation
- [ ] **`done` state output shows `None` for title, key_findings, summary, quality_score**: These context values aren't propagating through to `machine.yml`'s final output despite successful pipeline. `formatted_report` works fine. Data flow issue in `output_to_context` mapping between submachines.
- [ ] **`reaped_count: None` in reaper output**: Bare path returns None when key missing — lost `| default(0)` Jinja filter during bare-path migration. Need defaults in hook code or YAML literal defaults.

## Blocked / Immediate
- [ ] **Fireworks account suspended**: $5 credit burned. 131 reset jobs will re-poison if workers start. Either fix billing or switch profile back to cerebras (`profiles.yml` override).
- [ ] **1 paper stuck in `processing`**: From a dead worker. Need to reset to pending.

## Expression Engine
- [ ] **Add `int()`/`str()`/`float()` builtins to `flatmachines/expressions/simple.py`**: Need `ast.Call` handler for safe type-casting. Current fix is a hooks workaround specific to `quality_score` — any future agent returning number-as-string in a condition will hit the same `TypeError`. This was the originally requested approach.

## Testing / Verification
- [ ] **System log dir untested**: Checker/reaper changed to write to `logs/system/` but haven't run since the change.
- [ ] **Suspend-on-fail + reaper behavior untested**: Never verified that killed workers get suspended status and reaper picks them up correctly.
- [ ] **7 done papers missing MD files**: Papers marked `done` in DB but no corresponding report in `data/`. Need investigation.

## Minor
- [ ] **`flatagents.profiles` log line appears twice per agent init**: Internal flatagents issue, not handler duplication.
- [ ] **OTEL console export is noise**: Consider disabling console metric export since there's no central collector. Metrics reset per-worker subprocess anyway.
- [ ] **No token counts with fireworks**: Agent completion log lines don't include input/output token counts. Either fireworks isn't returning usage data or the monitor isn't logging it.
