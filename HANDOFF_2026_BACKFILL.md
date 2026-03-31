# Handoff: 2026 Backfill, Year Split & Word Clouds

**Date:** 2026-03-30  
**Status:** Backfill running, year split done, word cloud generation pending

---

## 1. Backfill (2026-02-05 → 2026-03-30)

### What was done
- Rewrote `arxiv_crawler/backfill_2025_2026.sh` with **auto-resume** (matches 2023/2024 scripts)
  - Reads the latest successful `submittedDate` slice from `crawl_runs`
  - End date defaults to `$(date +%Y-%m-%d)` (today) — no more hardcoded dates
  - Supports `BACKFILL_START_DATE`, `END_BOUND`, `THROTTLE_SECONDS`, `SLEEP_SECONDS`, `SLICE_RETRIES`, `SLICE_RETRY_SLEEP` env overrides

### Current state
- **Running in background** (PID 39581) — processing ~1 day-slice/min
- As of writing: reached **2026-02-17**, ~13 days remaining out of ~54 total
- Expect completion in ~40 minutes

### Monitoring
```bash
# Watch live
tail -f ~/code/research_crawler/arxiv_crawler/backfill_2025_2026.log

# Check latest slice
sqlite3 ~/code/research_crawler/arxiv_crawler/data/arxiv.sqlite \
  "SELECT MAX(substr(query, instr(query, 'submittedDate:[') + 15, 8)) 
   FROM crawl_runs WHERE status='ok' 
   AND (query LIKE '%submittedDate:[2026%');"

# Paper count by month
sqlite3 ~/code/research_crawler/arxiv_crawler/data/arxiv.sqlite \
  "SELECT strftime('%Y-%m', published_at) as m, COUNT(*) 
   FROM papers WHERE published_at >= '2026-01-01' GROUP BY m ORDER BY m;"
```

### If it fails / gets 429'd
arXiv rate-limits `submittedDate` queries aggressively. The script retries automatically. If it dies:
```bash
cd ~/code/research_crawler/arxiv_crawler
# Just re-run — auto-resume picks up where it left off
THROTTLE_SECONDS=10 SLEEP_SECONDS=10 SLICE_RETRIES=50 SLICE_RETRY_SLEEP=300 \
  nohup ./backfill_2025_2026.sh > backfill_2025_2026.log 2>&1 &
```

### Gotcha: database locks
If you see `database is locked` errors, check for stale processes:
```bash
lsof ~/code/research_crawler/arxiv_crawler/data/arxiv.sqlite
# Kill any old python processes, then clean up:
sqlite3 ~/code/research_crawler/arxiv_crawler/data/arxiv.sqlite \
  "UPDATE crawl_runs SET status='error', error='stale', finished_at=datetime('now') WHERE status='running';"
```

---

## 2. Year Split: 2025 vs 2026

### What was done
| File | Purpose |
|------|---------|
| `queries/word_clouds_2025/` | Copy of `word_clouds/` — 14 themed .txt files (the canonical 2025 set) |
| `queries/word_clouds_2026/` | **Symlink → `word_clouds_2025`** (temporary, use 2025 clouds until 2026 clouds are generated) |
| `queries/shotgun_seed_2025.sql` | Seed query filtering `arxiv_id LIKE '25%'`, uses `fmr_score` |
| `queries/shotgun_seed_2026.sql` | Seed query filtering `arxiv_id LIKE '26%'`, uses `fmr_score` |

### Note on `word_clouds/` (the original)
The original `queries/word_clouds/` directory is **still used by the pipeline** (hardcoded in `hooks.py:1589`). It's the taxonomy used for domain tagging during analysis. `word_clouds_2025` is a copy for year-specific archival. The pipeline itself doesn't need to change — it reads `word_clouds/` for domain tags regardless of paper year.

### To use year-specific clouds in `queue_top_per_word_cloud.sh`
Currently that script reads from `queries/word_clouds/`. To queue per-year:
```bash
# For 2025 papers
./queue_top_per_word_cloud.sh  # uses word_clouds/ as-is

# For 2026 papers (once real clouds exist)
# Either: update the WORD_CLOUD_DIR in the script
# Or: replace the symlink with the real generated dir
```

---

## 3. 2026 Word Cloud Generation (TODO)

### What needs to happen
After the backfill completes and relevance scoring runs on the new 2026 papers:

#### Step A: Score new 2026 papers
```bash
cd ~/code/research_crawler/relevance_scoring
./run.sh -- --since 2026-02-04 --limit 50000
```

#### Step B: Generate 2026 word clouds
```bash
cd ~/code/research_crawler/research_paper_analysis_v2
./build_word_clouds_organic_semantic.sh \
  --year-prefix 26 \
  --output-dir queries/word_clouds_2026_organic_semantic \
  --output-file shotgun_ml_llm_26_organic_semantic.txt \
  --csv-output data/word_list_26_organic_semantic_candidates.csv
```

This runs the full pipeline: phrase mining from 2026 abstracts → semantic reranking against anchors → themed cloud splitting. Takes ~10-20 min (loads embedding model + processes ~15K+ papers).

#### Step C: Replace the symlink
```bash
cd ~/code/research_crawler/research_paper_analysis_v2/queries
rm word_clouds_2026
# Option 1: use the organic semantic output directly
mv word_clouds_2026_organic_semantic word_clouds_2026
# Option 2: curate/clean first (like was done for 2023)
cp -r word_clouds_2026_organic_semantic word_clouds_2026
# then hand-edit as needed
```

#### Step D: Queue and seed 2026 papers
```bash
cd ~/code/research_crawler/research_paper_analysis_v2

# Queue top papers per word cloud into paper_queue
# (would need to point at word_clouds_2026 — see note above)

# Seed into v2 analysis pipeline
python run.py --seed-only --seed-limit 20000
```

---

## Data snapshot (as of 2026-03-30 ~6:10pm)

| Year | Papers | Has FMR score |
|------|--------|--------------|
| 2025 | 103,659 | 80,872 |
| 2026 | ~15,284 (growing) | 7,461 |

---

## Files changed

```
arxiv_crawler/backfill_2025_2026.sh              # Rewritten with auto-resume + dynamic end date
research_paper_analysis_v2/queries/
  shotgun_seed_2025.sql                           # NEW
  shotgun_seed_2026.sql                           # NEW
  word_clouds_2025/                               # NEW (copy of word_clouds/)
  word_clouds_2026 -> word_clouds_2025            # NEW (symlink, temporary)
```
