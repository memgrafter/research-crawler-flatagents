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
    JOIN paper_queue pq ON pq.paper_id = p.id
    WHERE p.llm_relevant = 1
      AND p.disable_summary = 0
      AND (pq.summary_path IS NULL OR pq.summary_path = '')
    GROUP BY p.id
),
top_8000 AS (
    SELECT paper_id, rk
    FROM ranked
    ORDER BY rk
    LIMIT 8000
),
bottom_6000 AS (
    SELECT paper_id
    FROM top_8000
    WHERE rk > 2000
)
UPDATE paper_queue
SET status = 'backlog',
    worker = NULL,
    started_at = NULL,
    finished_at = NULL,
    claimed_by = NULL,
    claimed_at = NULL,
    error = NULL
WHERE paper_id IN (SELECT paper_id FROM bottom_6000);
SQL

printf "Moved bottom 6000 of top-8000 queue to backlog in %s\n" "$DB_PATH"
