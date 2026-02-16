# 2023 arXiv Filter Spec (Draft)

Drafted from:
- `2025_ARXIV_FILTER_METHOD.md` (method template)
- `2024_ARXIV_FILTER_SPEC.md` (year-adapted policy structure)

---

## Objective
Select high-signal 2023 ML/LLM papers for v2 analysis using the same proven tranche policy, but with **2023-native** scoring + terms.

---

## Hard rule: score source (no fallback)

For 2023 selection, use:
- `paper_relevance.fmr_2023` **only**

Do **not** use:
- `fmr_score`
- `COALESCE(fmr_2023, fmr_score)`

Rows with `fmr_2023 IS NULL` are ineligible until scoring completes.

---

## Data sources

- `../arxiv_crawler/data/arxiv.sqlite`
  - `papers`: `id`, `arxiv_id`, `title`, `abstract`, `primary_category`, `llm_relevant`
  - `paper_relevance`: `paper_id`, `fmr_2023`, `details_json`
- `data/v2_executions.sqlite`
  - `executions`: dedupe out already queued/running/done by `arxiv_id`

Term inputs (2023-native):
- Word-cloud terms: `queries/word_clouds_2023_organic_semantic_cleaned/*.txt`
- Anchor terms: `paper_relevance.details_json["best_anchor"]` from `arxiv_id LIKE '23%'`

---

## Core category whitelist

```
cs.LG  cs.CL  cs.AI  cs.CV  cs.IR  cs.NE
stat.ML
cs.MA  cs.SD
eess.AS  eess.IV
```

---

## Matching logic

Text searched: `title + " " + abstract` (case-insensitive).

- `wc_hits`: unique matched terms from cleaned 2023 word clouds
- `fmr_hits`: unique matched terms from 2023 best-anchor set
- `combined_hits = |wc_matches ∪ fmr_matches|`

---

## Band policy (draft, same as 2025/2024)

Using `fmr_2023`:

| Band | Category | Term threshold | Action |
|---|---|---:|---|
| `>= 0.60` | required | none | queue |
| `0.55–0.60` | required | none | queue |
| `0.50–0.55` | required | `combined_hits >= 1` | queue |
| `0.40–0.50` | required | `combined_hits >= 2` | queue |
| `< 0.40` | — | — | skip |

---

## Priority formula (draft)

- `fmr_2023 >= 0.55`: `priority = fmr_2023`
- `fmr_2023 < 0.55`: `priority = fmr_2023 + 0.001 * min(combined_hits, 20)`

This preserves score-first ranking and uses term density as a small tie-breaker.

---

## Manifest schema (draft)

CSV columns:

```
arxiv_id,
fmr_2023,
primary_category,
wc_hits,
fmr_hits,
combined_hits,
selected,
rejection_reason,
priority,
policy_id
```

Recommended policy id: `arxiv_filter_2023_v1`.

---

## QA gate before queueing

- 20 random from `selected=true` in `0.40–0.50`
- 10 random from `selected=false`
- Check category mix + obvious false positives

If precision is poor in lower bands, tighten thresholds before insertion.

---

## Minimal validation SQL

```sql
-- scoring completeness
SELECT
  COUNT(*) AS total_2023,
  SUM(CASE WHEN pr.fmr_2023 IS NOT NULL THEN 1 ELSE 0 END) AS scored_2023,
  SUM(CASE WHEN pr.fmr_2023 IS NULL THEN 1 ELSE 0 END) AS unscored_2023
FROM papers p
LEFT JOIN paper_relevance pr ON pr.paper_id = p.id
WHERE p.arxiv_id LIKE '23%';

-- tranche shape (scored subset)
SELECT
  CASE
    WHEN pr.fmr_2023 >= 0.60 THEN '>=0.60'
    WHEN pr.fmr_2023 >= 0.55 THEN '0.55-0.60'
    WHEN pr.fmr_2023 >= 0.50 THEN '0.50-0.55'
    WHEN pr.fmr_2023 >= 0.40 THEN '0.40-0.50'
    ELSE '<0.40'
  END AS band,
  COUNT(*) AS n
FROM papers p
JOIN paper_relevance pr ON pr.paper_id = p.id
WHERE p.arxiv_id LIKE '23%'
  AND pr.fmr_2023 IS NOT NULL
GROUP BY band
ORDER BY 1;
```

---

## Notes

- This draft intentionally mirrors the proven 2025/2024 policy structure.
- Main adaptation is strict score source (`fmr_2023`) plus 2023-native term sets.
- No fallback score column is part of this draft.
