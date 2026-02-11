# arXiv Research Crawler

Note: this crawler is a pure workflow (no LLM calls). It uses the arXiv API and
SQLite only.
This subproject is a preparation tool; roadmap and scoring docs live in the
repo root (`README.md`, `DESIGN.md`, `TASKS.md`).

Lean FlatMachine crawler that discovers new LLM-related arXiv papers, stores
metadata in SQLite, and queues new entries for downstream processing.

## Quick Start

```bash
./run.sh -- --max-results 50
```

Install behavior: `run.sh` skips dependency installs if required packages are already present in `.venv`. Use `--upgrade`/`-u` to force reinstall/upgrade.

Dry run (skips writes to papers/queue):

```bash
./run.sh -- --max-results 25 --dry-run
```

Dry runs emit JSONL to stdout describing inserts/updates that would occur.

## Defaults
- Database: `data/arxiv.sqlite`
- Fetch categories: `cs.CL,cs.AI,cs.LG,stat.ML,cs.IR,cs.RO,cs.SE,cs.HC`
- Relevance filter categories: `cs.CL,cs.AI,cs.LG,stat.ML,cs.IR,cs.RO,cs.SE,cs.HC` (`llm_relevant` column)
- arXiv API URL: `https://export.arxiv.org/api/query` (`ARXIV_API_URL` override)
- User-Agent: `research-crawler/0.1` or `research-crawler/0.1 (mailto:...)` if `ARXIV_CONTACT_EMAIL` is set

Stored fields include abstract text plus `abstract_url` (arXiv abs page) and
`pdf_url` so downstream agents can fetch without web search.

## Backfill Example (December 2025)

```bash
./run.sh -- --max-results 5000 \
  --since 2025-12-01T00:00:00Z \
  --until 2025-12-31T23:59:59Z
```

Time-slice example (avoid deep pagination offsets):

```bash
python -m arxiv_crawler.backfill \
  --start-date 2025-12-01 \
  --end-date 2025-12-31 \
  --window-days 2 \
  --max-results 500
```

2024 helper script with auto-resume and conservative rate limits:

```bash
./backfill_2024.sh
```

If needed, force a specific restart day:

```bash
BACKFILL_START_DATE=2024-03-01 ./backfill_2024.sh
```

## Scraping Command History (Baseline)

Use this as the reference for daily scrapes and future backfills.

- December 2025 single-range runs (hit caps):
  - `./run.sh -- --max-results 5000 --since 2025-12-01T00:00:00Z --until 2025-12-31T23:59:59Z`
  - `./run.sh -- --max-results 10000 --since 2025-12-01T00:00:00Z --until 2025-12-31T23:59:59Z`
  - Outcome: both runs hit the `max-results` cap; the second run produced ~5k new
    and ~5k updates, so the month needed slicing.
- December 2025 sliced backfill (no offset paging):
  - `python -m arxiv_crawler.backfill --start-date 2025-12-01 --end-date 2025-12-31 --window-days 5 --max-results 5000 --progress-every 200`
  - Outcome: completed all slices; 6,436 entries fetched total, mostly updates.
- 2025-01-01 smoke test:
  - `python -m arxiv_crawler.backfill --start-date 2025-01-01 --end-date 2025-01-01 --window-days 1 --max-results 100`
  - Outcome: fetched 68 (all updates).
- 2025-04-09 → 2025-11-30 backfill (resumed after timeout at 2025-07-28):
  - `python -m arxiv_crawler.backfill --start-date 2025-04-09 --end-date 2025-11-30 --window-days 5 --max-results 5000 --progress-every 200`
  - Outcome: completed all slices; no caps hit.

Current state:
- `crawler_state.last_updated_at` is `2025-12-31T18:59:57+00:00`, so daily
  scrapes can run `./run.sh -- --max-results 200` and use the stored cursor.
- Remaining backfill gap: `2025-01-02` → `2025-04-08`.

## Key Files
- `config/machine.yml`: FlatMachine definition (actions + state flow).
- `schema.sql`: SQLite schema for papers, crawl runs, and queue.
- `src/arxiv_crawler/main.py`: CLI entry point.
- `src/arxiv_crawler/hooks.py`: crawler logic and SQLite handling.
