# Reverse Citation Enrichment

Batch job that enriches the shared SQLite database with "cited by" edges using
OpenAlex. This stays separate from the crawler so ingestion remains fast and
deterministic.

## Requirements
- `OPENALEX_MAILTO` must be set for the polite pool.
- `OPENALEX_API_KEY` must be set for OpenAlex API access.
- The SQLite database must already contain the `papers` table from `arxiv_crawler/`.

## Quick Start

```bash
export OPENALEX_MAILTO="you@example.com"
export OPENALEX_API_KEY="your-key"
./run.sh -- --limit 100 --dry-run
```

Install behavior: `run.sh` skips dependency installs if required packages are already present in `.venv`. Use `--upgrade`/`-u` to force reinstall/upgrade.

Run a small backfill:

```bash
./run.sh -- --limit 500 --since 2025-01-01 --cooldown-days 30
```

## Defaults
- Database: `../arxiv_crawler/data/arxiv.sqlite`
- Batch size: 100 IDs per request (OpenAlex OR batching)
- Rate limit: 10 req/sec with exponential backoff
- Query strategy: `search` + `filter=indexed_in:arxiv` title matching
- Fallback: search by arXiv ID and match against OpenAlex locations
- DOI lookups: optional (`--use-doi`)

## Output Tables
- `citations`: cited-by edges between known papers
- `paper_citations`: global cited-by counts per target paper (from OpenAlex `meta.count`)
- `citation_runs`: run metadata and counts
- `citation_work_cache`: cached OpenAlex work metadata (escape hatch)

## Files
- `config/machine.yml`: FlatMachine definition
- `schema.sql`: citation tables
- `src/reverse_citation_enrichment/main.py`: CLI entry point
- `src/reverse_citation_enrichment/hooks.py`: OpenAlex logic + SQLite handling
