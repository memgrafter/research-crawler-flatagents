# 2025 arXiv Filter Method

## Objective
Select high-signal ML/AI research papers from arXiv for the v2 pipeline without downloading/analyzing the full corpus.

This method combines:
1. **FMR score** (`paper_relevance.fmr_score`),
2. **Primary category filtering** (`papers.primary_category`),
3. **Term-match filtering** from both:
   - curated word-cloud term lists (`queries/word_clouds/*.txt`), and
   - FMR anchor phrases (`paper_relevance.details_json.best_anchor`).

---

## Data Sources
- `arxiv_crawler/data/arxiv.sqlite`
  - `paper_relevance`: `fmr_score`, `details_json`
  - `papers`: `arxiv_id`, `title`, `abstract`, `primary_category`
- `research_paper_analysis_v2/data/v2_executions.sqlite`
  - `executions`: existing queued/running/done papers

Already-seen papers are excluded by `arxiv_id` if present in `executions`.

---

## Core Category Filter
Only keep papers with `primary_category` in:

- `cs.LG`, `cs.CL`, `cs.AI`, `cs.CV`, `cs.IR`, `cs.NE`
- `stat.ML`
- `cs.MA`, `cs.SD`
- `eess.AS`, `eess.IV`

This removes a large amount of cross-domain/applications noise (e.g., unrelated robotics, software engineering, quant-ph, etc.).

---

## Term-Match Sets
### A) Word-cloud terms
All non-comment, non-empty lines from `queries/word_clouds/*.txt`.

### B) FMR anchor terms
From `paper_relevance.details_json["best_anchor"]` (when present).

### Matching rule
Case-insensitive whole-term regex with word boundaries (`\b...\b`) against:

`title + " " + abstract`

### Union hit count
`combined_hits = |(word_cloud_matches ∪ fmr_anchor_matches)|`

---

## Band Policy (Final)
- **`fmr >= 0.60`**: handled separately earlier (already queued/running)
- **`0.55 <= fmr < 0.60`**: **Category only**
- **`0.50 <= fmr < 0.55`**: **Category + combined_hits >= 1**
- **`0.40 <= fmr < 0.50`**: **Category + combined_hits >= 2**
- **`fmr < 0.40`**: skip

This keeps precision high while still recovering conceptually relevant papers that word-cloud-only matching would miss.

---

## Priority Strategy in v2
To support ordered processing after current queue:

1. Added column:
   - `executions.priority REAL NOT NULL DEFAULT 0`
2. Updated claim ordering in scheduler (`run.py`):
   - `ORDER BY priority DESC, created_at ASC`
3. Existing papers remain `priority = 0` (so they finish first, as requested).
4. New papers use positive priority so they run **after existing queue** and in ranked order.

### Priority formula
- For `0.55–0.60`: `priority = fmr_score`
- For lower bands: `priority = fmr_score + 0.001 * min(combined_hits, 20)`

This keeps FMR as primary ranking and uses term density as a small tie-breaker.

---

## Run Snapshot (this execution)
Inserted into `executions` as `pending` with priority:

- `0.55–0.60` (category only): **11,531**
- `0.50–0.55` (category + union >=1): **18,446**
- `0.40–0.50` (category + union >=2): **7,845**

**Total inserted:** `37,822`

Skipped during this pass:
- not in core category: `23,831`
- below hit threshold in band: `9,824`
- already in v2: `15,559`

---

## Artifacts produced during analysis
- `data/unqueued_above_0.60.txt`
- `data/unqueued_0.55_to_0.60.txt`
- `data/unqueued_0.55_to_0.60_boosted.txt`
- `data/unqueued_0.55_to_0.60_category.txt`
- `data/unqueued_0.55_to_0.60_category_boosted.txt`
- `data/unqueued_0.55_to_0.60_category_zero_hits.txt`
- `data/unqueued_0.50_to_0.55_category.txt`
- `data/unqueued_0.55_0.60_cat_wc_fmr.txt`
- `data/unqueued_0.50_0.55_cat_wc_fmr.txt`
- `data/unqueued_0.40_0.50_cat_wc_fmr.txt`
- `data/unqueued_0.00_0.40_cat_wc_fmr.txt`
- `data/fmr_score_histogram.png`
- `data/fmr_completion_histogram.png`

---

## Notes
- Empirically, FMR is highly discriminative: quality falls sharply below ~0.50 and is mostly poor below 0.40.
- FMR-anchor matching adds meaningful recall over word-cloud-only matching (captures phrases like hallucination/alignment/model-editing/etc.).
- Category filter + union matching gave the best precision/recall tradeoff for this corpus.
