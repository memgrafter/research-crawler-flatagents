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

-- DB-backed execution leases (replaces file lock state when enabled by runner).
CREATE TABLE IF NOT EXISTS execution_leases (
    execution_id   TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    phase          TEXT NOT NULL,
    lease_until    INTEGER NOT NULL,    -- unix epoch seconds
    fencing_token  INTEGER NOT NULL DEFAULT 1,
    acquired_at    TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_leases_until ON execution_leases(lease_until);

-- DB-backed checkpoints (replaces high-churn checkpoint temp files when enabled).
CREATE TABLE IF NOT EXISTS machine_checkpoints (
    checkpoint_key TEXT PRIMARY KEY,
    execution_id   TEXT NOT NULL,
    machine_name   TEXT,
    event          TEXT,
    current_state  TEXT,
    snapshot_json  BLOB NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mc_execution_created ON machine_checkpoints(execution_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mc_machine_name ON machine_checkpoints(machine_name);

CREATE TABLE IF NOT EXISTS machine_latest (
    execution_id TEXT PRIMARY KEY,
    latest_key   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ml_latest_key ON machine_latest(latest_key);
