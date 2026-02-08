-- V2 execution tracking schema.
-- Decoupled from arxiv_crawler DB. Only v2 reads/writes here.

CREATE TABLE IF NOT EXISTS executions (
    execution_id  TEXT PRIMARY KEY,
    arxiv_id      TEXT NOT NULL,
    paper_id      INTEGER NOT NULL,     -- references arxiv DB papers.id (not FK-enforced)
    title         TEXT NOT NULL,
    authors       TEXT NOT NULL DEFAULT '',
    abstract      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
        -- pending | prepping | prepped | analyzing | analyzed | wrapping | done | failed
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    prep_output   TEXT,                 -- JSON blob: key_outcome, corpus_signals, section_text, etc.
    expensive_output TEXT,              -- JSON blob: why_hypotheses, reproduction_notes, open_questions
    result_path   TEXT,                 -- path to output report .md
    error         TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_arxiv ON executions(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);

CREATE TABLE IF NOT EXISTS daily_usage (
    date           TEXT PRIMARY KEY,    -- YYYY-MM-DD
    total_calls    INTEGER NOT NULL DEFAULT 0,
    cheap_calls    INTEGER NOT NULL DEFAULT 0,
    expensive_calls INTEGER NOT NULL DEFAULT 0
);
