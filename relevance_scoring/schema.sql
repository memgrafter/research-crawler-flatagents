CREATE TABLE IF NOT EXISTS paper_relevance (
    paper_id INTEGER PRIMARY KEY,
    fmr_score REAL NOT NULL,
    comparative_score REAL,
    citation_score REAL,
    priority_score REAL,
    research_type TEXT,
    reproduction_cost INTEGER,
    scored_at TEXT NOT NULL,
    details_json TEXT,
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE INDEX IF NOT EXISTS idx_paper_relevance_fmr_score ON paper_relevance(fmr_score);
