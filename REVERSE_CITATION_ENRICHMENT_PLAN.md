# Reverse Citation Enrichment Plan

## Goal
Enrich the SQLite knowledge base with “cited by” edges for arXiv LLM/agentic papers using an external citation provider (e.g., Semantic Scholar or OpenAlex).

## Scope
- Separate job from the crawler to keep ingest fast and deterministic.
- Backfill for a fixed historical window (e.g., year 2025), then run incremental updates.

## Data Model (Additions)
- `citations`: directed edges with source metadata.
  - `source_paper_id`, `citing_paper_id`, `source`, `retrieved_at`
- `citation_runs`: run metadata, pagination info, errors.
- Optional `citation_work_cache`: minimal metadata for citing works without arXiv IDs.

## Workflow Phases
1) **Target selection**
   - Select papers to enrich by date range and `llm_relevant=1`.
   - Skip papers already enriched recently (cooldown window).

2) **Fetch cited-by**
   - Query provider API by arXiv ID (fallback to DOI).
   - Paginate with rate limits and retries.

3) **Normalize and map**
   - Map citing works to `papers` by arXiv ID (or insert into cache).
   - Store provider-specific IDs for traceability.

4) **Upsert edges**
   - Insert citation edges with unique constraint on `(source_paper_id, citing_paper_id, source)`.

5) **Record run**
   - Persist run status, counts, and timestamps.

## Backfill Strategy
- One-time backfill for 2025 (or a configurable range).
- Split into batches (by month or by paper count).
- Store `retrieved_at` for refresh logic.

## Incremental Strategy
- Daily or weekly runs for new papers.
- Re-enrich older papers on a slower cadence (e.g., quarterly).

## Provider Considerations
- Semantic Scholar: strong arXiv coverage, strict rate limits.
- OpenAlex: broader coverage, simple API, good for scale.

## Success Criteria
- High coverage for `llm_relevant` papers.
- Stable run times with predictable API usage.
- Idempotent writes with clean dedupe semantics.
