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

-- Author cache to avoid duplicate OpenAlex API calls
CREATE TABLE IF NOT EXISTS authors (
    openalex_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    orcid TEXT,
    h_index INTEGER,
    i10_index INTEGER,
    cited_by_count INTEGER,
    works_count INTEGER,
    affiliation_name TEXT,
    affiliation_ror TEXT,
    retrieved_at TEXT NOT NULL,
    raw_json TEXT
);

-- Junction table linking papers to authors
CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id INTEGER NOT NULL,
    author_openalex_id TEXT NOT NULL,
    author_position INTEGER NOT NULL,  -- 0 = first author, 1 = second, etc.
    is_corresponding BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (paper_id, author_openalex_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id),
    FOREIGN KEY (author_openalex_id) REFERENCES authors(openalex_id)
);

CREATE INDEX IF NOT EXISTS idx_authors_h_index ON authors(h_index);
CREATE INDEX IF NOT EXISTS idx_authors_works_count ON authors(works_count);
CREATE INDEX IF NOT EXISTS idx_paper_authors_author ON paper_authors(author_openalex_id);
