# Relevance Scoring 2024 (One-off, Isolated)

Isolated copy of the rolling relevance scorer for one-off 2024 backfill.

## What it does

- Scores only papers matching `arxiv_id LIKE '24%'`.
- Uses anchors loaded from 2024 cloud files by default.
- Writes scores into `paper_relevance.fmr_2024`.
- Leaves the rolling scorer (`relevance_scoring/`) untouched.

## Default anchor source

Configured in `../relevance_scoring_2024.yml`:

- `research_paper_analysis_v2/queries/word_clouds_2024/*.txt`

## LLM-only exclusion layer

This one-off scorer is now explicitly an **LLM pass**.

After loading cloud anchors, it applies an explicit exclusion list to remove broad/generic/domain terms that caused bleed:

- `research_paper_analysis_v2/queries/word_clouds_2024/llm_pass_exclusions_2024.list`

Examples removed: `AI`, `ML`, `RL`, `3d`, `high-quality`, `decision-making`, `EEG`, `MRI`, `q-learning`, `f1 score`.

## Quick start

```bash
cd relevance_scoring_2024
./run.sh -- --limit 500 --dry-run
```

Backfill run:

```bash
cd relevance_scoring_2024
./run.sh -- --limit 50000 --year-prefix 24
```

## Behavior details

- By default, only papers with `fmr_2024 IS NULL` are selected.
- Use `--rescore-existing` to recompute already-populated `fmr_2024`.
- If a `paper_relevance` row does not exist yet, this pipeline inserts one and initializes both `fmr_score` and `fmr_2024` to the computed value for that row.
- Anchor exclusions are configurable via `anchor_exclude_terms` / `anchor_exclude_files` in `relevance_scoring_2024.yml`.

## CLI flags

- `--db-path`: SQLite DB path (default `../arxiv_crawler/data/arxiv.sqlite`)
- `--config-path`: scorer config (default `../relevance_scoring_2024.yml`)
- `--year-prefix`: arXiv prefix (default `24`)
- `--since`, `--until`: optional date filters
- `--limit`: max papers to score
- `--batch-size`: embedding batch size
- `--rescore-existing`: recompute existing `fmr_2024`
- `--dry-run`: no DB writes
