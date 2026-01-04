# Task List: Relevance Scoring + Prioritization

1. Finalize scoring taxonomy
   - Confirm the umbrella name (FMR) and feature list (categories, keywords, embeddings).
   - Define comparative relevance rules and the "supersedes" relation.
   - Keep the heuristic set small and measurable; avoid feature creep.
2. Add schema extensions
   - Create `paper_relevance`, `paper_citations`, and `paper_relations`.
   - Add indexes for `paper_id`, `priority_score`, and `last_fetched_at`.
3. Implement citation enrichment
   - Use OpenAlex with polite pool (`OPENALEX_MAILTO`) and document required access.
   - Add a batch fetcher that updates `paper_citations` with caching, 10 req/sec cap, and exponential backoff.
   - Implement OR batching (default 100 IDs) with `per-page=100`.
   - Document OpenAlex filter fields (no arXiv ID filter listed) and plan the title-search fallback; DOI is optional.
   - Add a dry-run/limit flag to test a small batch without writes.
   - Store OpenAlex `cited_by_count` per target paper for global popularity.
4. Implement clustering
   - Use the defaults in `relevance_scoring.yml` (MiniLM + KMeans) and allow CLI overrides.
   - Embed titles/abstracts and assign cluster IDs.
   - Persist results in `paper_clusters` or `paper_relevance.details_json`.
5. Build the relevance scoring job
   - Compute FMR and comparative signals.
   - Compute citation-weighted popularity.
   - Write `paper_relevance` rows with feature breakdowns.
6. Prioritize the queue
   - Define priority weights and normalize to `paper_queue.priority`.
   - Route papers to `read` vs `reproduce` via `paper_queue.worker`.
7. Add CLI entry points
   - `python -m arxiv_crawler.score_relevance --since ... --top-k ...`
   - `python -m arxiv_crawler.fetch_citations --since ...`
8. Documentation pass
   - Update `README.md` with usage examples once the jobs exist.
   - Record any new outputs in `data/` and how to interpret them.
9. Validation and tuning
   - Spot-check top-ranked papers for relevance.
   - Adjust weights and heuristics based on outcomes.
