# Relevance Scoring (Parameterized, Isolated)

Isolated copy of the rolling relevance scorer for one-off year backfills.

## What it does

- Scores papers matching `arxiv_id LIKE '<year_prefix>%'`.
- Uses anchors loaded from configured cloud files.
- Writes scores into a caller-selected column (required):
  - e.g. `fmr_2023`, `fmr_2024`, or `fmr_score`.
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
./run.sh -- --target-score-column fmr_2024 --year-prefix 24 --limit 500 --dry-run
```

2023 backfill example:

```bash
cd relevance_scoring_2024
./run.sh -- \
  --target-score-column fmr_2023 \
  --year-prefix 23 \
  --config-path ../relevance_scoring_2023.yml \
  --limit 50000
```

Convenience wrapper (same args, with passthrough):

```bash
cd relevance_scoring_2024
./score_2023.sh --limit 50000
```

## Behavior details

- `--target-score-column` is required; the run fails fast if missing.
- By default, only papers with `<target_score_column> IS NULL` are selected.
- Use `--rescore-existing` to recompute already-populated target values.
- If a `paper_relevance` row does not exist yet, this pipeline inserts one and initializes:
  - `fmr_score` (required schema column), and
  - `<target_score_column>` (when different from `fmr_score`).
- Anchor exclusions are configurable via `anchor_exclude_terms` / `anchor_exclude_files` in the chosen config file.

## CLI flags

- `--db-path`: SQLite DB path (default `../arxiv_crawler/data/arxiv.sqlite`)
- `--config-path`: scorer config (default `../relevance_scoring_2024.yml`)
- `--year-prefix`: arXiv prefix (default `24`)
- `--target-score-column` (**required**): destination column in `paper_relevance`
- `--since`, `--until`: optional date filters
- `--limit`: max papers to score
- `--batch-size`: embedding batch size
- `--rescore-existing`: recompute existing target column values
- `--dry-run`: no DB writes
