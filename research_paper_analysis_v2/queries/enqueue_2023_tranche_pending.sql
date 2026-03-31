-- Enqueue a 2023 FMR tranche into v2 executions as pending.
--
-- Run from research_paper_analysis_v2 root:
--   sqlite3 data/v2_executions.sqlite < queries/enqueue_2023_tranche_pending.sql
--
-- Edit min_score / max_score / row_limit in the params CTE as needed.
-- Examples:
--   0.55-0.60 tranche: min_score=0.55, max_score=0.60
--   >=0.60 tranche:    min_score=0.60, max_score=9.99
--
-- Safety default:
--   low tranches (<0.55) are blocked unless allow_low_tranches=1.

ATTACH DATABASE '../arxiv_crawler/data/arxiv.sqlite' AS arxiv;

BEGIN;

WITH
params AS (
  SELECT
    0.55 AS min_score,
    0.60 AS max_score,
    0    AS row_limit, -- 0 means "no limit"
    0    AS allow_low_tranches -- set to 1 only if you explicitly want <0.55
),
core_categories(cat) AS (
  VALUES
    ('cs.LG'),('cs.CL'),('cs.AI'),('cs.CV'),('cs.IR'),('cs.NE'),
    ('stat.ML'),('cs.MA'),('cs.SD'),('eess.AS'),('eess.IV')
),
candidates AS (
  SELECT
    p.arxiv_id,
    p.id AS paper_id,
    COALESCE(p.title, '') AS title,
    COALESCE(p.abstract, '') AS abstract,
    pr.fmr_2023 AS score
  FROM arxiv.papers p
  JOIN arxiv.paper_relevance pr ON pr.paper_id = p.id
  CROSS JOIN params
  WHERE p.arxiv_id LIKE '23%'
    AND p.llm_relevant = 1
    AND p.primary_category IN (SELECT cat FROM core_categories)
    AND pr.fmr_2023 IS NOT NULL
    AND pr.fmr_2023 >= params.min_score
    AND pr.fmr_2023 <  params.max_score
    AND (
      params.allow_low_tranches = 1
      OR params.min_score >= 0.55
    )
    AND NOT EXISTS (
      SELECT 1
      FROM executions e
      WHERE e.arxiv_id = p.arxiv_id
    )
  ORDER BY pr.fmr_2023 DESC
  LIMIT CASE
    WHEN (SELECT row_limit FROM params) > 0
      THEN (SELECT row_limit FROM params)
    ELSE -1
  END
)
INSERT OR IGNORE INTO executions (
  execution_id,
  arxiv_id,
  paper_id,
  title,
  authors,
  abstract,
  status,
  created_at,
  updated_at,
  prep_output,
  result_path,
  error,
  priority
)
SELECT
  LOWER(HEX(RANDOMBLOB(16))) AS execution_id,
  c.arxiv_id,
  c.paper_id,
  c.title,
  '' AS authors,
  c.abstract,
  'pending' AS status,
  STRFTIME('%Y-%m-%dT%H:%M:%f+00:00','now') AS created_at,
  STRFTIME('%Y-%m-%dT%H:%M:%f+00:00','now') AS updated_at,
  NULL AS prep_output,
  NULL AS result_path,
  NULL AS error,
  c.score AS priority
FROM candidates c;

SELECT changes() AS inserted_rows;

COMMIT;
DETACH DATABASE arxiv;
