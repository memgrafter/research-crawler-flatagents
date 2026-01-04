# Relevance Scoring and Prioritization Design

## Goals
- Rank arXiv papers by "Foundation Model Relevance (FMR)" for AI/LLM/transformer/deep-learning topics.
- Use citation backreferences as a popularity signal, weighted by relevance of citing papers.
- Prioritize the top-K papers for reading or reproduction based on research type and estimated cost.

## Non-Goals
- Replace the crawler or introduce LLM calls into the ingestion path.
- Provide a perfect citation graph; start with coarse citation popularity and iterate.

## Principles
- Start simple and measurable; only add complexity when ranking quality improves.
- Preserve reuse with lightweight escape hatches (configs, raw JSON, feature breakdowns).
- Keep ingestion deterministic; all enrichment happens in separate batch jobs.

## Definitions
- FMR (Foundation Model Relevance): a topical relevance score combining categories, keywords, and optional embeddings.
- Comparative relevance: a signal indicating a paper is superseded or improved upon (e.g., "A is strictly better than B").
- Cluster: a topical grouping used to compare papers against similar neighbors for comparative relevance.
- Priority score: the final score used to rank candidates in `paper_queue`.

## Signals and Scoring
1. **FMR score (0-1)**
   - Category match: existing `llm_relevant` plus weight for primary categories.
   - Lexical match: curated keywords/phrases in title + abstract (e.g., "transformer", "diffusion", "instruction tuning").
   - Optional embedding similarity: compare against a topic vector set.
   - Recency decay: reduce weight for older papers unless cited heavily.

2. **Comparative relevance (cluster-aware)**
   - Cluster papers by embeddings to compare within topical neighborhoods.
   - Default clustering uses MiniLM embeddings + KMeans from `relevance_scoring.yml`.
   - Detect "supersedes" relations from:
     - Explicit phrasing ("improves on", "outperforms", "replaces").
     - Title patterns ("v2", "improved", "revisited").
     - Citation dominance (newer paper heavily citing older paper).
   - Output a penalty for superseded papers and a boost for successors.

3. **Backreference popularity**
   - Fetch citation counts + optional "influential citation" counts.
   - Compute a weighted popularity score:
     - Base = log(1 + citation_count)
     - Weight citing papers by their FMR or priority score when available.

4. **Priority score**
   - Priority = w1 * FMR + w2 * backreference + w3 * comparative - w4 * reproduction_cost
   - Use a bounded scale for queue priority (e.g., 0-100).

## Data Model (Proposed)
- `paper_relevance` (new table)
  - `paper_id` (PK), `fmr_score`, `comparative_score`, `citation_score`, `priority_score`
  - `research_type` (enum-like TEXT), `reproduction_cost` (INTEGER)
  - `scored_at`, `details_json` (feature breakdown + reuse escape hatch)
- `paper_citations` (new table)
  - `paper_id` (PK), `cited_by_count`, `retrieved_at`, `raw_json` (reuse escape hatch)
  - Populate `cited_by_count` from OpenAlex `meta.count` even when edges are sparse.
- `paper_relations` (new table)
  - `from_paper_id`, `to_paper_id`, `relation` (e.g., `supersedes`), `confidence`
- `paper_clusters` (optional new table)
  - `paper_id`, `cluster_id`, `method`, `assigned_at`

The existing `paper_queue.priority` field remains the execution target; the scoring job updates it for the top-K papers.

## Pipeline
1. **Ingest**: crawler writes new papers and queues them (unchanged).
2. **Citation fetch**: batch job enriches `paper_citations` using external sources (OpenAlex/Semantic Scholar).
3. **Clustering**: embed abstracts/titles and assign cluster IDs for comparative relevance (defaults in `relevance_scoring.yml`).
4. **Relevance scoring**: compute FMR + comparative + citation scores into `paper_relevance`.
5. **Routing**:
   - Determine `research_type` and `reproduction_cost`.
   - Set `paper_queue.priority` for top-K and mark `worker` as `read` or `reproduce`.

## Routing Heuristics
- `research_type`: classify via rules (e.g., "benchmark", "dataset", "system", "theory") with optional model fallback.
- `reproduction_cost` (0-3): heuristic on code availability, hardware needs, dataset size.
- Route:
  - Low cost + high priority -> `reproduce`
  - High cost + high priority -> `read`
  - Low priority -> backlog

## Operational Notes
- Cache citation results and respect API rate limits.
- Incremental updates: only rescore changed or newly ingested papers.
- Keep scoring jobs separate from the crawler to preserve no-LLM ingestion.
- Prefer CLI overrides instead of growing the config; keep the defaults small.
- Citation fetch defaults: OpenAlex OR batching (target 100 IDs per request), 10 req/sec cap, exponential backoff, a dry-run option, and polite-pool `OPENALEX_MAILTO`.
- OpenAlex query strategy: use `search` with `filter=indexed_in:arxiv` and match by title, then fall back to arXiv ID search; DOI batching is optional and low priority.

## Open Questions
- Confirm the final keyword set and topic embeddings for FMR.
- Confirm OpenAlex filter fields for arXiv IDs and whether `filter=doi:` is supported (optional).
- When to switch from KMeans to HDBSCAN (or no clustering) based on data scale.
- Decide whether to store paper-level tasks in a new table vs reusing `paper_queue.worker`.
