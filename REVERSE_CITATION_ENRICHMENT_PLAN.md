# Reverse Citation Enrichment Plan

## Goal
Enrich the SQLite knowledge base with “cited by” edges for arXiv LLM/agentic papers using an external citation provider (e.g., Semantic Scholar or OpenAlex).

## Scope
- Separate job from the crawler to keep ingest fast and deterministic.
- Backfill for a fixed historical window (e.g., year 2025), then run incremental updates.
- Keep enrichment minimal; weighting and clustering happen in the scoring job.
- Implementation lives in `reverse_citation_enrichment/`.

## Data Model (Additions)
- `citations`: directed edges with source metadata.
  - `source_paper_id`, `citing_paper_id`, `source`, `retrieved_at`
- `paper_citations`: global counts for each target paper.
  - `paper_id`, `source`, `cited_by_count`, `retrieved_at`, `raw_json`
- `citation_runs`: run metadata, pagination info, errors.
- Optional `citation_work_cache`: minimal metadata for citing works without arXiv IDs.
- Store raw provider payloads when available as a reuse escape hatch.

## Workflow Phases
1) **Target selection**
   - Select papers to enrich by date range and `llm_relevant=1`.
   - Skip papers already enriched recently (cooldown window).

2) **Fetch cited-by**
   - Query provider API by arXiv ID if supported; otherwise use title search.
   - Fallback: search by arXiv ID and match against OpenAlex locations.
   - DOI lookup is optional and low priority (few papers have DOIs); skip malformed values.
   - Paginate with rate limits and retries.
   - Use async workers capped at 10 req/sec; exponential backoff on errors.
   - Build OpenAlex filter queries from `papers.arxiv_id` if supported; otherwise use DOI or title search.
   - OpenAlex filters do not list arXiv IDs or `ids.doi`; plan a fallback:
     - Use `search=` with `filter=indexed_in:arxiv` and map by title.
     - If `filter=doi:` is supported, it can be used opportunistically.

3) **Normalize and map**
   - Map citing works to `papers` by arXiv ID (or insert into cache).
   - Store provider-specific IDs for traceability.

4) **Upsert edges**
   - Insert citation edges with unique constraint on `(source_paper_id, citing_paper_id, source)`.

5) **Record run**
   - Persist run status, counts, and timestamps.

6) **Store global counts**
   - Save OpenAlex `meta.count` as `paper_citations.cited_by_count` for each target paper.

## Backfill Strategy
- One-time backfill for 2025 (or a configurable range).
- Split into batches (by month or by paper count).
- Store `retrieved_at` for refresh logic.

## Incremental Strategy
- Daily or weekly runs for new papers.
- Re-enrich older papers on a slower cadence (e.g., quarterly).

## Integration Notes
- The scoring job weights citation counts by FMR and (optionally) cluster proximity.
- Avoid extra joins here; keep enrichment focused on reliable edges.

## Provider Considerations (Selected: OpenAlex)
- Use OpenAlex for coverage and straightforward API.
- Rate limits: 100,000 calls/day and 10 requests/second.
- Use OR batching to squash requests:
  - Up to 100 values per filter using `|` (pipe).
  - Use `per-page=100` to return all results for the batched IDs.
  - Example: `filter=doi:https://doi.org/AAA|https://doi.org/BBB` (if supported).
  - OR batching only works within a single filter field, not across filters.
- Use polite pool with `OPENALEX_MAILTO`:
  - Query param: `mailto=$OPENALEX_MAILTO`
  - Or set `mailto:$OPENALEX_MAILTO` in the User-Agent header.

## Rate Limit + Budget Notes
- Default batch size target: 100 IDs per request.
- Example: 8,000 papers -> ~80 requests; at 10 req/sec this is ~8s plus overhead/backoff (~10-15s).
- Daily budget should not be a constraint with OR batching (100,000 calls/day).
- Title-search fallbacks are per-paper and will cost more; keep them limited.

## Dry Run Mode
- Support a `--dry-run` or `--limit` flag to fetch a small sample (one 100-ID batch) and log counts without writing to the DB.

## Success Criteria
- High coverage for `llm_relevant` papers.
- Stable run times with predictable API usage.
- Idempotent writes with clean dedupe semantics.
- Raw citation data is retained to enable future re-scoring without refetching.
