# Plan: Re-establish Discovery Pipeline as Default

**Target:** 1-2 weeks from now (mid-April 2026)
**Updated:** 2026-03-30

---

## Goal

Get `discovery_pipeline/` running on a weekly cron as the single entry point for new paper discovery. It should crawl → score → enrich → report, automatically, with no manual intervention.

---

## Done (2026-03-30 session)

- ✅ Backfill complete: 28,249 papers crawled through 2026-03-30
- ✅ All 2026 papers scored with `relevance_scoring_2026.yml` (all prior year clouds as anchors, ~2856 terms)
- ✅ MPS watermark caps (0.6 high / 0.5 low) hardcoded in all embedding entry points
- ✅ 2026 word clouds generated (Stella + anchors), cleaned, exclusion list applied
- ✅ Flatmachine configs updated for SDK 1.1.1 (`hooks` string → object format)
- ✅ `watch_2026.sh` created and tested — seeds 2026 papers into v2 executions
- ✅ v2 pipeline confirmed working with 5 workers + trinity-large-preview:free
- ✅ ~23K papers seeded and daemon + watcher running

## Remaining Steps

### 1. Validate analysis output quality
- Monitor the current run (~23K papers, ~4 days at 16 RPM)
- Spot-check reports in `~/code/analysis/ml_research_analysis_2025/` for 2026 papers
- Consider testing alternative models if trinity quality is insufficient

### 2. Wire discovery pipeline to 2026 config
- `discovery_pipeline/run.sh` scoring phase needs to point at `relevance_scoring_2026.yml`
- Verify it uses the same DB and picks up where backfill left off
- Check if discovery pipeline configs also need the hooks format update

### 3. Set up cron
- Weekly Monday 6am:
  ```
  0 6 * * 1 cd /path/to/discovery_pipeline && ./run.sh >> /tmp/discovery.log 2>&1
  ```
- Separate cron for `watch_2026.sh` to keep v2 queue topped up
- `run.py --workers 16 --daemon` as a long-running service alongside

### 4. Monitoring
- Log rotation for discovery.log and run logs
- Health check: alert if no new papers scored in 7 days
- Review reports weekly to validate quality

### 5. Retire manual workflows
- `backfill_*.sh` scripts are for one-off historical backfills only
- `queue_top_per_word_cloud.sh` is superseded by `watch_2026.sh` for 2026
- The v2 analysis pipeline (`run.py --workers`) consumes what the discovery pipeline feeds

---

## Open Questions

- Should the discovery pipeline also trigger v2 analysis workers, or keep that manual?
- Daily vs weekly cadence — daily crawl avoids large backlogs but costs more API calls
- Do we want year rollover logic (auto-detect 2026 vs 2027 when January comes)?
- Alternative model to trinity-large-preview:free — validating options
