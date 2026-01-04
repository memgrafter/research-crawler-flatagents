CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    query TEXT,
    categories TEXT,
    since TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY,
    arxiv_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL,
    authors TEXT NOT NULL,
    categories TEXT NOT NULL,
    primary_category TEXT,
    published_at TEXT,
    updated_at TEXT,
    pdf_url TEXT,
    entry_url TEXT,
    abstract_url TEXT,
    doi TEXT,
    journal_ref TEXT,
    comment TEXT,
    llm_relevant INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    crawl_run_id INTEGER,
    UNIQUE (arxiv_id, version),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id)
);

CREATE TABLE IF NOT EXISTS paper_queue (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    enqueued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    worker TEXT,
    error TEXT,
    UNIQUE (paper_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS crawler_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_updated_at ON papers(updated_at);
CREATE INDEX IF NOT EXISTS idx_papers_published_at ON papers(published_at);
