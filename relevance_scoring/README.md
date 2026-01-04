# Relevance Scoring (FMR)

Local embedding-based scoring for "Foundation Model Relevance" (FMR). This job
computes a first-pass relevance score for papers in the shared SQLite DB using
anchor phrase similarity.

## Quick Start

```bash
./run.sh -- --limit 200 --dry-run
```

Score a batch:

```bash
./run.sh -- --limit 500 --since 2025-01-01
```

## Defaults
- Database: `../arxiv_crawler/data/arxiv.sqlite`
- Config: `../relevance_scoring.yml`
- Embeddings: `dunzhang/stella_en_400M_v5` (with `trust_remote_code: true`)
- Score: max cosine similarity to anchors (`scoring.method=embedding_max`)

## Output Table
- `paper_relevance`: stores `fmr_score` and `details_json` (best anchor, method)

## Files
- `config/machine.yml`: FlatMachine definition
- `schema.sql`: `paper_relevance` table
- `src/relevance_scoring/main.py`: CLI entry point
- `src/relevance_scoring/hooks.py`: scoring logic
