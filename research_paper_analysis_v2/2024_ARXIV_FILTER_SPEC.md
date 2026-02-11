# 2024 arXiv Filter Spec

Applies the same *methodology* as [2025_ARXIV_FILTER_METHOD.md](./2025_ARXIV_FILTER_METHOD.md) to the 2024 arXiv corpus, with year-native term generation.

---

## Key Principle: Year-Native Terms

The 2025 word-cloud terms and FMR anchors were derived from 2025's top papers. They encode 2025's research vocabulary — model names, benchmark names, and trend-specific phrases that may not exist in 2024 literature.

**Term lists must be re-derived per year from that year's own top papers.** The *method* is a template; the terms are not portable across years.

### Term generation process (per year)
1. Crawl the target year's papers into the arXiv DB
2. Run FMR scoring on all crawled papers
3. Take the top ~5,000 papers by FMR score
4. Extract digests (title + abstract) from those top papers
5. Build word-cloud term lists from the digests (same clustering/extraction method used for 2025)
6. Extract FMR anchor terms from `paper_relevance.details_json["best_anchor"]` for the target year's papers
7. Use the resulting year-native term sets for the union-hit filter

---

## Prerequisites

### 1. Crawl 2024 papers
The arXiv DB currently has only **915 papers** with `arxiv_id LIKE '24%'`. The crawler needs to backfill 2024 before this filter can run at scale.

```bash
cd ../arxiv_crawler
# Run with date range targeting 2024-01-01 to 2024-12-31
# (exact flags depend on crawler CLI — check its README)
./run.sh -- --start-date 2024-01-01 --end-date 2024-12-31 --max-results 200000
```

### 2. Score 2024 papers
Ensure `paper_relevance.fmr_score` is computed for all newly crawled 2024 papers. This happens as part of the crawler pipeline (FMR scoring at ingest time).

### 3. Build 2024-native term lists
From the scored 2024 papers:
```
a) Select top ~5,000 by fmr_score
b) Build word-cloud term lists → queries/word_clouds_2024/*.txt
c) Collect FMR anchors from 2024 papers only:
     SELECT details_json->>'best_anchor'
     FROM paper_relevance pr
     JOIN papers p ON p.id = pr.paper_id
     WHERE p.arxiv_id LIKE '24%'
```

### 4. Verify volume
Before filtering, confirm expected scale:
```sql
SELECT COUNT(*) FROM papers WHERE arxiv_id LIKE '24%';
SELECT COUNT(*) FROM paper_relevance pr
  JOIN papers p ON p.id = pr.paper_id
  WHERE p.arxiv_id LIKE '24%' AND pr.fmr_score IS NOT NULL;
```
arXiv publishes ~20K–30K ML-adjacent papers/year in core categories. Expect similar or slightly less than 2025.

---

## Filter Policy

**Policy ID:** `arxiv_filter_v1`

Same structure as 2025. Only the term sources differ.

### Core category whitelist
```
cs.LG  cs.CL  cs.AI  cs.CV  cs.IR  cs.NE
stat.ML
cs.MA  cs.SD
eess.AS  eess.IV
```

### Term sources (2024-native)
- **Word-cloud terms:** `queries/word_clouds_2024/*.txt` (generated from 2024 top papers)
- **FMR anchor terms:** `paper_relevance.details_json["best_anchor"]` (from 2024 papers only)
- **Match rule:** case-insensitive `\b...\b` regex against `title + " " + abstract`
- **Union hits:** `|(wc_matches ∪ fmr_matches)|`

### Band thresholds

| FMR band | Category filter | Term filter | Action |
|----------|----------------|-------------|--------|
| `>= 0.60` | required | none | queue |
| `0.55–0.60` | required | none | queue |
| `0.50–0.55` | required | union >= 1 | queue |
| `0.40–0.50` | required | union >= 2 | queue |
| `< 0.40` | — | — | skip |

### Priority formula
- `>= 0.55`: `priority = fmr_score`
- `< 0.55`: `priority = fmr_score + 0.001 * min(combined_hits, 20)`

---

## Execution Steps

### Step 1: Crawl + score (prerequisite)
Backfill 2024 papers into arXiv DB with FMR scores.

### Step 2: Generate 2024-native term lists
Build word clouds and collect FMR anchors from the 2024 corpus (see Prerequisites §3).

### Step 3: Generate manifest
Run filter script with:
- `year_prefix = '24'`
- `policy_id = 'arxiv_filter_v1'`
- `term_source = 'queries/word_clouds_2024/'` + 2024 FMR anchors
- Output: `data/2024_selection_manifest.csv`

Manifest columns:
```
arxiv_id, fmr_score, primary_category, wc_hits, fmr_hits, combined_hits,
selected, rejection_reason, priority, policy_id
```

### Step 4: QA sample
Before queueing, spot-check:
- 20 random from `selected=true, fmr 0.40–0.50` (weakest band)
- 10 random from `selected=false` (confirm they're noise)
- Verify category distribution looks reasonable

### Step 5: Queue
Insert `selected=true` rows into `executions` with:
- `status = 'pending'`
- `priority` from manifest
- `created_at` = insertion time (sorts after any existing pending work)

### Step 6: Record
Log to this file or a run-log:
- Date of queue insertion
- Counts per band
- Total queued / skipped
- Any policy adjustments made after QA

---

## Differences from 2025

| Aspect | 2025 | 2024 |
|--------|------|------|
| Corpus in DB | ~103K papers | ~915 (needs crawl) |
| Word cloud terms | derived from 2025 top papers | **derived from 2024 top papers** |
| FMR anchors | from 2025 papers | **from 2024 papers** |
| FMR scoring model | same | same (embeddings are era-independent) |
| Band thresholds | validated empirically | assume same — **verify after crawl** |
| Priority ordering | runs first (priority > 0) | runs after 2025 queue |

### Why terms must be year-native
The 2025 word-cloud terms were built by:
1. Taking the top ~5,000 2025 papers by FMR score
2. Extracting digests (title + abstract)
3. Building 12 thematic term lists from those digests

This captures 2025's vocabulary — model names (DeepSeek, Qwen, o1), benchmarks, and trend phrases that didn't exist in 2024. Running 2025 terms against 2024 abstracts would:
- **Undercount hits** for 2024 papers (terms hadn't been coined yet)
- **Push good 2024 papers below union thresholds** in the 0.40–0.55 bands
- **Miss 2024-specific vocabulary** (terms that were prominent in 2024 but replaced/evolved by 2025)

The FMR score itself is embedding-based and era-independent — it remains the primary signal. Year-native terms are the secondary filter for the mid-bands only.

### Priority interleaving with 2025
Two options:
1. **Sequential**: Queue 2024 papers only after 2025 pending count drops below a threshold. Simpler.
2. **Interleaved**: Assign 2024 papers a priority offset (e.g., `priority - 1.0`) so they always sort below same-FMR 2025 papers. More complex but keeps the queue always full.

Decision: TBD at queue time.

---

## Open Questions
1. **Crawler date-range support**: Does the arXiv crawler support `--start-date`/`--end-date`? If not, what query mechanism targets 2024?
2. **Volume estimate**: How many 2024 papers will the crawler find across all categories? Need this to estimate pipeline runtime.
3. **FMR threshold validation**: After crawling 2024, do the band boundaries (0.40/0.50/0.55/0.60) still produce the same quality tiers? Spot-check before committing.
4. **Term list overlap**: After generating 2024 term lists, compare overlap with 2025 lists. High overlap = stable field vocabulary. Low overlap = significant terminology shift worth investigating.
