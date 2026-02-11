# 2024 Word Clouds (Shotgun, High-Recall)

This folder contains 2024 ML/LLM term clouds generated from arXiv `title + abstract` text using **deterministic heuristics** (no runtime LLM calls, no embedding inference in the term extraction stage).

## Goal

Create a broad, reusable vocabulary set for selecting 2024 ML/LLM papers with high recall (targeting the same spirit as the prior high-accuracy FMR-first pipeline), while keeping compute and cost low.

## How it was built

Pipeline command:

```bash
cd research_paper_analysis_v2
./build_word_clouds_2024.sh
```

This runs two steps:

1. **`generate_word_list_2024.py`**
   - Pulls candidate 2024 papers from SQLite:
     - `arxiv_id LIKE '24%'`
     - score floor on `COALESCE(fmr_2024, fmr_score)`
       - **bootstrap behavior:** if `fmr_2024` is `NULL`, it falls back to `fmr_score`
     - core categories and/or shotgun lexical matches in title+abstract
   - Extracts candidate terms (unigrams, bigrams/trigrams, acronyms)
   - Applies noise controls (stopwords, generic-term filtering, technical regex gate)
   - Ranks terms with TF/DF/IDF-style scoring
   - Outputs:
     - `shotgun_ml_llm_2024.txt`
     - `data/word_list_2024_candidates.csv`

2. **`split_word_clouds_2024.py`**
   - Splits ranked terms into themed files via deterministic regex rules
   - Backfills themed files with existing seed terms from `queries/word_clouds/*.txt`
   - Builds one broad catch-all cloud:
     - `general_ml_llm_high_recall_95.txt`

## Initial-state note (important)

At the time of the first 2024 word-cloud run, `paper_relevance.fmr_2024` was intentionally **not populated yet** (mostly/all `NULL`).

So the candidate-paper score used by `COALESCE(fmr_2024, fmr_score)` effectively came from **`fmr_score`**, i.e. the existing prior scoring column (historically used for the current/2025 pipeline).

In short:
- this run did **not** magically infer `fmr_2024` values,
- it used the existing `fmr_score` signal as a bootstrap until 2024-specific scoring is populated.

## Intentions

The intended production use is:

- Populate `paper_relevance.fmr_2024` for **2024 papers** (`arxiv_id LIKE '24%'`).
- Use these 2024 clouds **together** (general + themed + 2024 anchors) as the term layer for 2024 filtering.
- Keep `fmr_2024` as the primary ranking signal and use term hits as secondary gating/tie-break support.
- After `fmr_2024` is populated, prefer strict 2024 scoring (no fallback) for 2024 selection runs.

In short: this vocabulary pack is meant to operate as a single 2024 term system on top of `fmr_2024` for 2024 papers.

## Why this method is robust

1. **Multi-signal candidate selection**
   - Not just one keyword list: combines year prefix, relevance score, category whitelist, and lexical shotgun patterns.

2. **Score-column resilience**
   - Works during migration: uses `fmr_2024` when available and falls back to `fmr_score`.
   - In the initial bootstrap run (empty `fmr_2024`), this means it used the pre-existing score signal from `fmr_score`.

3. **Noise suppression before ranking**
   - Filters stopwords/generic tokens and phrase patterns known to be non-discriminative.

4. **Frequency-based guards**
   - Terms must clear minimum document frequency and are dropped if too common (`max_doc_ratio`), reducing trivial terms.

5. **Deterministic + reproducible**
   - Same DB + same flags => same output.
   - No model temperature, no prompt drift.

6. **Recall backstop**
   - The general cloud is built from:
     - fixed high-recall anchors,
     - mined 2024 terms,
     - prior cloud seeds.
   - This intentionally biases toward recall over precision for initial filtering.

## Why this method is generic

- **Year-agnostic**: change `--year-prefix`.
- **Schema-tolerant**: configurable `--score-column` / `--fallback-score-column`.
- **No provider dependency**: no LLM API or embedding model required for generation.
- **Portable taxonomy**: themed filenames follow the existing naming scheme used in 2025 clouds.
- **Tunable knobs**: `--min-score`, `--top-papers`, DF thresholds, caps, etc.

## About “LLM flavor” in this output

There is **no runtime LLM inference** in this generation pipeline.

However, some static design choices were authored by the coding assistant/human during implementation:

- shotgun lexical seed patterns,
- high-recall anchor list,
- themed regex routing rules,
- file naming and taxonomy mapping.

So the **data output is statistics-driven**, but the **heuristic scaffolding** reflects human/assistant priors about ML/LLM terminology.

If you want to minimize this flavor further:
- reduce/remove fixed anchors,
- derive theme rules from purely mined co-occurrence clusters,
- rely more on scored terms and less on manual seed backfill.

## Output files in this folder

- `general_ml_llm_high_recall_95.txt` (broad catch-all cloud)
- `shotgun_ml_llm_2024.txt` (raw ranked shortlist from the generator)
- themed clouds:
  - `core_architecture_components.txt`
  - `model_families_specific_implementations.txt`
  - `training_optimization.txt`
  - `reasoning_cognition.txt`
  - `information_retrieval_knowledge.txt`
  - `robustness_safety.txt`
  - `evaluation_benchmarking.txt`
  - `efficiency_scaling.txt`
  - `multimodal_cross_domain.txt`
  - `deployment_production.txt`
  - `continual_adaptive_learning.txt`
  - `interpretability_transparency.txt`
  - `human_ai_interaction.txt`
  - `data_training_paradigms.txt`

## Practical note on the “95” in the general file name

`general_ml_llm_high_recall_95.txt` means **high-recall target/intention**, not a mathematically guaranteed 95% recall on its own. Actual coverage should be validated with a held-out QA sample against your selection outcomes.
