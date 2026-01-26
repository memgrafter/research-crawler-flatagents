# Research Analyzer Agent — Human Loop Plan

## Goal
Run **batch paper summaries on demand** (not in weekly summary) using the existing
`research_paper_analysis` flatmachine that accepts an arXiv ID or URL. The workflow
uses a **human-in-the-loop prioritization REPL** to pick which papers to summarize,
optionally disabling summary for specific papers.

This avoids expensive LLM calls on thousands of papers while still leveraging the
quality prioritization scores in the database.

## Constraints & Assumptions
- **Cost control** is the primary goal: no automatic bulk summarization in the weekly
  pipeline.
- **Human loop** is required for final selection and disable-summary flags.
- `research_paper_analysis` already supports arXiv IDs/URLs as input.
- We will **not** modify ingestion or scoring flows; we only add a summarization
  worker and a human loop controller.
- `disable_summary` does **not** exist yet in the DB. We will add it (see DB plan).

## High-Level Flow
1. **Batch request**: user runs a summarization batch script (not part of weekly report).
2. **Candidate fetch**: script selects top K eligible papers by ranking.
3. **Human REPL**: user sees candidate list (title, abstract, score, authors).
4. **User decisions**: user provides natural language preferences and/or disables.
5. **LLM reformats**: the agent turns the user's text into JSON.
6. **User approves JSON**: apply changes only after explicit approval.
7. **Apply decisions**:
   - Set disable-summary flag in DB.
   - Run summaries for prioritized papers (in rank order).
   - Do nothing for "pass" items (idempotent behavior).
8. **Repeat**: fetch the next K candidates and continue.

## Data Model Plan
### 1) Add `disable_summary` flag
Add to `papers` table because it's a paper-level property and should persist
across runs (preferred over `paper_queue`).

Proposed schema change:
```sql
ALTER TABLE papers ADD COLUMN disable_summary INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_papers_disable_summary ON papers(disable_summary);
```

### 2) Track summary completion in DB
Reuse `paper_queue` to track summarizer runs (no new table):
- `worker = 'summarizer'`
- `status = 'pending' | 'processing' | 'done' | 'error'`
- `priority` used for ranking
- add `summary_path` to store the output filename (timestamped)

Proposed schema change:
```sql
ALTER TABLE paper_queue ADD COLUMN summary_path TEXT;
```
`finished_at` already provides the completion timestamp.

## Candidate Selection (Ranking)
Use the existing ranking query pattern:
- Primary: `paper_relevance.fmr_score`
- Secondary: `max(author.h_index)`
- Optional tertiary: `paper_citations.cited_by_count`

Eligibility filters:
- `p.llm_relevant = 1`
- `p.disable_summary = 0`
- `paper_relevance.fmr_score IS NOT NULL`
- Exclude already summarized papers (if a summary output exists)

Example selection query:
```sql
SELECT
  p.id, p.arxiv_id, p.title, p.abstract,
  pr.fmr_score,
  pc.cited_by_count,
  GROUP_CONCAT(a.display_name, ', ') AS authors,
  MAX(a.h_index) AS max_h_index
FROM papers p
JOIN paper_relevance pr ON pr.paper_id = p.id
LEFT JOIN paper_citations pc ON pc.paper_id = p.id
LEFT JOIN paper_authors pa ON pa.paper_id = p.id
LEFT JOIN authors a ON a.openalex_id = pa.author_openalex_id
WHERE p.llm_relevant = 1
  AND p.disable_summary = 0
GROUP BY p.id
ORDER BY pr.fmr_score DESC, max_h_index DESC NULLS LAST, pc.cited_by_count DESC
LIMIT ?;
```

## Human REPL Interface (User-Facing)
Show each candidate with:
- index
- title
- arxiv_id
- score (fmr_score)
- authors
- abstract (truncated)

User input (free-form, NL) examples:
- "Summarize 1, 3, and 5. Do not summarize paper 2. Leave the rest."
- "Prioritize the transformer paper, disable summary for the dataset-only papers."
- "Do them all."
- "Do all except the diffusion benchmark paper."

The agent converts to a strict JSON schema:
```json
{
  "batch_id": "2026-01-26T07:00Z",
  "actions": [
    { "arxiv_id": "2401.12345", "title": "Paper Title", "action": "summarize", "priority": 1 },
    { "arxiv_id": "2401.54321", "title": "Another Title", "action": "disable_summary" },
    { "arxiv_id": "2401.99999", "title": "Some Paper", "action": "pass" }
  ]
}
```

**Approval gate**: show JSON and require explicit user approval before applying.

### Decision Rules (Critical)
- **Default action is `pass`** for any paper the user does not mention.
- **Default priority** is the paper's ranking score (`fmr_score` order) unless the user overrides.
- "Do them all" → mark all as `summarize` in existing priority order.
- "Do all except X" → `summarize` all, **pass** X (unless the user explicitly says "do not summarize").
- "Do not summarize / disable summary" → set `disable_summary = 1` in DB (persistent).

## Summarizer Worker (research_paper_analysis)
### Inputs
- `arxiv_id` OR URL (as updated in flatmachine).

### Execution loop
1. Take approved JSON actions.
2. Apply disable-summary flags in DB.
3. For `summarize` actions (in priority order):
   - Call `research_paper_analysis` flatmachine.
   - Save outputs to `research_paper_analysis/data/` (existing behavior).
4. For `pass` actions: do nothing (idempotent).
5. Repeat: fetch next K eligible candidates.

### Failure handling
- If summarization fails, mark `paper_queue.status='error'` and store error text.
- Keep results idempotent: re-running should not duplicate reports.

## CLI/Script Plan
Create a new Python script inside `research_paper_analysis/python/`:
- `summarizer_repl.py` (or similar)
- Responsibilities:
  - Query DB for top-K candidates.
  - Present REPL view.
  - Call LLM to normalize user input -> JSON.
  - Show JSON for approval.
  - Apply DB changes and run summaries.

Potential CLI usage:
```bash
python research_paper_analysis/python/summarizer_repl.py \
  --db /path/to/your.db \
  --limit 10
```

Default batch size: **10** (override with `--limit`).

### Single-Paper Analyzer Remains Unchanged
Direct one-off analysis still uses the existing entrypoint and run script:
```bash
cd research_paper_analysis/python
./run.sh --arxiv 1706.03762
```
The summarizer REPL is a **separate script** and does not modify `main.py` or
`python/run.sh`, so single-paper runs remain unaffected.

### New Run Script (Summarizer REPL)
Add a dedicated run helper (e.g., `research_paper_analysis/python/run_summarizer_repl.sh`)
that mirrors the existing `run.sh` venv setup but calls `summarizer_repl.py`.

## Open Questions
1. Should “pass” keep papers immediately eligible (current) or defer them for N days?
2. Should we also store a short summary of the user’s rationale alongside the action?

## Next Steps (Implementation Order)
1. Add `disable_summary` column + index.
2. Build `summarizer_repl.py`.
3. Wire to updated flatmachine in `research_paper_analysis`.
4. Test on a small batch (K=3) to validate end-to-end flow.
