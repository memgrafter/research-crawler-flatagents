# Repository TODO

## Runner UX

- [ ] Add wrapper help output (`-h` / `--help`) to remaining `run.sh` scripts:
  - `arxiv_crawler/run.sh`
  - `multi_paper_synthesizer/run.sh`
  - `relevance_scoring/run.sh`
  - `reverse_citation_enrichment/run.sh`
  - `discovery_pipeline/run.sh`
  - `research_paper_analysis/js/run.sh`

## Rate-Limit Control

- [ ] Validate new rate-limit gate end-to-end
  - Simulate 429 responses
  - Confirm suspend/unsuspend behavior
  - Confirm `effective_max` changes as expected

- [ ] Decide state-key scope for rate-limiting
  - Current behavior uses global `crawler_state` keys
  - Evaluate whether keys should be per-provider/per-model

## Documentation

- [ ] Document suspended-worker behavior and rate-limit state keys
  - Include timezone handling details
  - Place in `README` and/or `EXAMPLE_INFO.md`

## Output De-duplication / Requeue Behavior

- [ ] Investigate duplicate analysis outputs
  - Causes observed: multiple paper versions, empty `summary_path` causing requeue
  - Decide cleanup strategy
  - Add filename dedupe policy

## Profiles / Model Selection Design

- [ ] Design profile alias + override model for cleaner model switching
  - Problem: current profile switching is awkward for common "fast/default/free" workflows
  - Goal: support global aliases, local overrides, and fallbacks without config sprawl
  - Evaluate how much should live in profiles vs machine/job-level overrides
