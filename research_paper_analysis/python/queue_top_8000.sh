#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-../../arxiv_crawler/data/arxiv.sqlite}"

sqlite3 "$DB_PATH" <<'SQL'
WITH latest_versions AS (
    SELECT arxiv_id, MAX(version) AS max_version
    FROM papers
    GROUP BY arxiv_id
),
ranked AS (
    SELECT
        p.id AS paper_id,
        pr.fmr_score,
        MAX(a.h_index) AS max_h_index,
        pc.cited_by_count,
        ROW_NUMBER() OVER (
            ORDER BY pr.fmr_score DESC,
                     (MAX(a.h_index) IS NULL) ASC,
                     MAX(a.h_index) DESC,
                     pc.cited_by_count DESC
        ) AS rk
    FROM papers p
    JOIN latest_versions lv
      ON lv.arxiv_id = p.arxiv_id
     AND lv.max_version = p.version
    JOIN paper_relevance pr ON pr.paper_id = p.id
    LEFT JOIN paper_citations pc ON pc.paper_id = p.id
    LEFT JOIN paper_authors pa ON pa.paper_id = p.id
    LEFT JOIN authors a ON a.openalex_id = pa.author_openalex_id
    LEFT JOIN paper_queue pq ON pq.paper_id = p.id
    WHERE p.llm_relevant = 1
      AND p.disable_summary = 0
      AND (pq.summary_path IS NULL OR pq.summary_path = '')
    GROUP BY p.id
)
INSERT OR IGNORE INTO paper_queue (paper_id, status, priority, enqueued_at)
SELECT
    paper_id,
    'pending',
    (8001 - rk) AS priority,
    strftime('%Y-%m-%dT%H:%M:%SZ','now') AS enqueued_at
FROM ranked
ORDER BY rk
LIMIT 10;
SQL

printf "Queued top 8000 papers into %s\n" "$DB_PATH"
