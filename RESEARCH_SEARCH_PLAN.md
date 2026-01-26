# Research Search Plan

## Goal
Add a new shell script and supporting Python entry point that lets a user run a quick text search (FTS5) against the existing SQLite database, review the top N matches in the same style as `run_summarizer_repl.sh`, and then feed the chosen papers into the human-loop summarizer flow (with the user’s focus/query carried through).

## Proposed Flow (User)
1. Run a new script (e.g., `research_paper_analysis/python/run_search_repl.sh`).
2. Provide a search query and a result limit (e.g., top 20).
3. See the ranked candidates in the same format as the current summarizer REPL.
4. Give feedback/instructions as usual (summarize, pass, disable).
5. Selected papers are summarized via the existing analyzer pipeline.

## Plan
1. **Discovery + design**
   - Confirm available schema and fields in `arxiv_crawler/data/arxiv.sqlite` (papers, abstracts, relevance scores).
   - Decide FTS5 approach for title+abstract search (no embeddings for now).
   - Keep a note for a future optional semantic re-rank step (on-demand embeddings) if needed later.

2. **Data + query layer**
   - Add an FTS5 index over `papers.title` + `papers.abstract`.
   - Implement an FTS query that returns top N matching papers (pre-filter to `llm_relevant = 1` and exclude `disable_summary = 1`), ordered by BM25.
   - **FTS5 schema options (pick one):**
     - Option A: external content table + triggers (keeps data in `papers`).
     - Option B: contentless FTS table + triggers (data lives only in the FTS index).
   - **Tradeoffs:**
     - Option A pros: no duplicate text storage, easy `rebuild`, supports `snippet()`/`highlight()`.
     - Option A cons: requires triggers to keep FTS in sync.
     - Option B pros: simpler table definition, still no duplicate text storage.
     - Option B cons: rebuild is awkward without external content, no `snippet()`/`highlight()`.
   - **Decision:** Use Option A (external content) for easier rebuilds and future-friendly snippet/highlight support.
   - **Option A (external content) example:**
     ```sql
     CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts
     USING fts5(
       title,
       abstract,
       content='papers',
       content_rowid='id'
     );

     CREATE TRIGGER IF NOT EXISTS papers_fts_ai AFTER INSERT ON papers BEGIN
       INSERT INTO papers_fts(rowid, title, abstract)
       VALUES (new.id, new.title, new.abstract);
     END;
     CREATE TRIGGER IF NOT EXISTS papers_fts_ad AFTER DELETE ON papers BEGIN
       INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
       VALUES ('delete', old.id, old.title, old.abstract);
     END;
     CREATE TRIGGER IF NOT EXISTS papers_fts_au AFTER UPDATE ON papers BEGIN
       INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
       VALUES ('delete', old.id, old.title, old.abstract);
       INSERT INTO papers_fts(rowid, title, abstract)
       VALUES (new.id, new.title, new.abstract);
     END;
     ```
   - **Option B (contentless) example:**
     ```sql
     CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts
     USING fts5(title, abstract);

     CREATE TRIGGER IF NOT EXISTS papers_fts_ai AFTER INSERT ON papers BEGIN
       INSERT INTO papers_fts(rowid, title, abstract)
       VALUES (new.id, new.title, new.abstract);
     END;
     CREATE TRIGGER IF NOT EXISTS papers_fts_ad AFTER DELETE ON papers BEGIN
       INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
       VALUES ('delete', old.id, old.title, old.abstract);
     END;
     CREATE TRIGGER IF NOT EXISTS papers_fts_au AFTER UPDATE ON papers BEGIN
       INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
       VALUES ('delete', old.id, old.title, old.abstract);
       INSERT INTO papers_fts(rowid, title, abstract)
       VALUES (new.id, new.title, new.abstract);
     END;
     ```
   - **One-time rebuild (either option):**
     ```sql
     INSERT INTO papers_fts(papers_fts) VALUES('rebuild');
     ```
   - **Search query example (BM25):**
     ```sql
     SELECT
       p.id,
       p.arxiv_id,
       p.title,
       p.abstract
     FROM papers p
     JOIN papers_fts fts ON fts.rowid = p.id
     WHERE p.llm_relevant = 1
       AND p.disable_summary = 0
       AND fts MATCH ?
     ORDER BY bm25(fts)
     LIMIT ?;
     ```

3. **Search REPL entry point**
   - Add `research_paper_analysis/python/src/research_paper_analysis/search_repl.py` that:
     - Accepts `--query`, `--limit`, and `--db`.
     - Performs FTS5 search to build a candidate list.
     - Reuses the summarizer candidate formatting and the human-loop action flow (recommendations + LLM JSON).
     - Passes the user’s query/focus into downstream summarization context (via config inputs or a new `focus` argument).

4. **Shell script + wiring**
   - Add `research_paper_analysis/python/run_search_repl.sh` mirroring venv setup and deps from `run_summarizer_repl.sh`.
   - Wire the script to call the new Python module with passthrough args.

5. **Docs + examples**
   - Update `research_paper_analysis/python/README.md` with usage examples and expected output.
   - Note any one-time FTS5 setup step (if needed).

6. **Manual validation**
   - Run with a sample query (e.g., “retrieval augmented generation”) and confirm:
     - Results are ranked by FTS5/BM25 relevance.
     - The human-loop flow behaves exactly like the summarizer REPL.
     - Selected items are summarized and stored in the queue as expected.

## Open Questions
- Do we want FTS5 as a contentless index with triggers, or a direct content= table that mirrors `papers`?
- Where should “focus” be threaded into the analysis prompts/configs to steer summaries? (Needs a small config/plumbing decision.)
