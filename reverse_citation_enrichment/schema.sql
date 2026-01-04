CREATE TABLE IF NOT EXISTS citations (
    id INTEGER PRIMARY KEY,
    source_paper_id INTEGER NOT NULL,
    citing_paper_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (source_paper_id, citing_paper_id, source),
    FOREIGN KEY (source_paper_id) REFERENCES papers(id),
    FOREIGN KEY (citing_paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS citation_runs (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    since TEXT,
    until TEXT,
    target_count INTEGER NOT NULL DEFAULT 0,
    resolved_count INTEGER NOT NULL DEFAULT 0,
    edge_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS paper_citations (
    paper_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    cited_by_count INTEGER NOT NULL DEFAULT 0,
    retrieved_at TEXT NOT NULL,
    raw_json TEXT,
    PRIMARY KEY (paper_id, source),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS citation_work_cache (
    openalex_id TEXT PRIMARY KEY,
    arxiv_id TEXT,
    doi TEXT,
    title TEXT,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_citations_source_paper_id ON citations(source_paper_id);
CREATE INDEX IF NOT EXISTS idx_citations_citing_paper_id ON citations(citing_paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_citations_count ON paper_citations(cited_by_count);
CREATE INDEX IF NOT EXISTS idx_citation_cache_arxiv_id ON citation_work_cache(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_citation_cache_doi ON citation_work_cache(doi);
