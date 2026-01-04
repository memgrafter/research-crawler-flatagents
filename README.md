# Research Crawler (LLM arXiv)

Super-lean project overview: build a FlatMachine-based crawler that regularly discovers new arXiv LLM research, normalizes metadata, and persists it to SQLite so it can be integrated into a broader LLM research knowledge base. The crawler will run on cron. Additional FlatMachines will be added later for downstream processing.

## Scope (Current)
- **Crawler FlatMachine**: fetch new papers from relevant arXiv categories and update a local SQLite store.
- **SQLite persistence**: store paper metadata, fetch history, and processing status.
- **Cron execution**: the crawler runs on a schedule and is idempotent.

Implementation lives in:
- `arxiv_crawler/` (crawler, schema, and runnable entry point)
- `reverse_citation_enrichment/` (OpenAlex cited-by enrichment job)
- `relevance_scoring/` (local embedding-based FMR scoring)

## Design Principles
- Keep it minimal: batch jobs and small tables, no heavy services.
- Reuse escape hatches: `details_json`/`raw_json` columns and a small config file for swapping components.
- Default to stable, cheap heuristics; only add complexity when it measurably helps.

Quick run:

```bash
cd arxiv_crawler
./run.sh -- --max-results 50
```

## Project Plan

### Relevance + Prioritization Roadmap (Planned)
1. Define and score "Foundation Model Relevance (FMR)" to capture AI/LLM/transformer/deep-learning topicality, with cluster-aware comparative relevance (e.g., superseded work).
2. Pull backreferences/citations and compute a popularity signal weighted by the relevance of citing papers.
3. Combine signals to prioritize the top-K papers in `paper_queue`, routing them to read vs reproduce based on research type and estimated cost.

### Phase 1 — Discovery & Data Model
Tasks:
- Identify target arXiv categories and query strategy (e.g., cs.CL, cs.LG, cs.AI, stat.ML).
- Define SQLite schema: papers, sources, crawl_runs, and processing status.
- Decide uniqueness keying (arXiv ID + version) and dedupe rules.

### Phase 2 — Crawler FlatMachine
Tasks:
- Implement a FlatMachine that fetches new entries since last run.
- Parse and normalize metadata (title, authors, abstract, categories, URL, published date).
- Persist updates in SQLite with upsert semantics and run logging.

### Phase 3 — Integration Hooks
Tasks:
- Define interfaces for downstream FlatMachines (analysis, summarization, tagging).
- Emit a clean “new papers” queue/table for later processing.
- Add basic failure handling and retry policy.

### Phase 4 — Ops & Automation
Tasks:
- Add cron-compatible runner (`run.sh` or `python -m ...`) with exit codes.
- Document configuration (API limits, categories, DB path).
- Add lightweight monitoring (counts, last-run timestamp).

## Notes
- This repository already contains example FlatMachine projects used for reference and experimentation.
- Future FlatMachines will be added as the knowledge-base workflow expands.
- Future opportunity: OpenAlex `locations`/`best_oa_location` can help recover arXiv links and PDFs when DOI is missing.

## Docs and Config
- `DESIGN.md`: relevance scoring + prioritization design.
- `TASKS.md`: task list for implementation.
- `REVERSE_CITATION_ENRICHMENT_PLAN.md`: citation enrichment plan.
- `relevance_scoring.yml`: starter defaults for embeddings + clustering.
