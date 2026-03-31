# Plan: Re-establish Discovery Pipeline as Default

**Target:** 1-2 weeks from now (mid-April 2026)

---

## Goal

Get `discovery_pipeline/` running on a weekly cron as the single entry point for new paper discovery. It should crawl → score → enrich → report, automatically, with no manual intervention.

---

## Steps

### 1. Validate current state
- Confirm `discovery_pipeline/run.sh` still runs end-to-end (crawl, score, enrich, report)
- Check it uses the correct DB (`../arxiv_crawler/data/arxiv.sqlite`)
- Verify it picks up where the last crawl left off (auto-backfill)

### 2. Update scoring to use 2026 config
- Discovery pipeline's scoring phase needs to use `relevance_scoring_2026.yml` (all prior year clouds as anchors) instead of the default `relevance_scoring.yml`
- Add MPS watermark caps (already done in the scoring hooks — verify it's picked up)

### 3. Wire in 2026 seeding
- After scoring, the pipeline should feed new papers into v2 executions via `watch_2026.sh --once` (or equivalent logic)
- Decide: should the discovery pipeline call watch_2026 directly, or keep it as a separate cron job?

### 4. Set up cron
- Weekly Monday 6am (as README suggests):
  ```
  0 6 * * 1 cd /path/to/discovery_pipeline && ./run.sh >> /tmp/discovery.log 2>&1
  ```
- Add a second cron for `watch_2026.sh` if seeding is kept separate
- Consider: daily crawl + weekly report, or weekly for both?

### 5. Monitoring
- Set up log rotation for discovery.log
- Add a health check: alert if no new papers scored in 7 days
- Review reports weekly to validate quality

### 6. Retire manual workflows
- Document that `backfill_*.sh` scripts are for one-off historical backfills only
- `queue_top_per_word_cloud.sh` is superseded by `watch_2026.sh` for 2026
- The v2 analysis pipeline (`run.py --workers`) is separate — it consumes what the discovery pipeline feeds

---

## Open Questions

- Should the discovery pipeline also trigger v2 analysis workers, or keep that manual?
- Daily vs weekly cadence — daily crawl avoids large backlogs but costs more API calls
- Do we want year rollover logic (auto-detect 2026 vs 2027 when January comes)?
