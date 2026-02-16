-- Shotgun seed query for 2023 ML/LLM papers.
--
-- Notes:
-- - Uses strict 2023 score column: fmr_2023 (no fallback).
-- - Filters to 2023 IDs and broad ML/LLM signals in title+abstract.
-- - Adjust MIN_SCORE and LIMIT for recall/precision tradeoff.

WITH scored AS (
  SELECT
    p.id,
    p.arxiv_id,
    p.primary_category,
    p.title,
    p.abstract,
    pr.fmr_2023 AS score
  FROM papers p
  JOIN paper_relevance pr ON pr.paper_id = p.id
  WHERE p.arxiv_id LIKE '23%'
    AND pr.fmr_2023 IS NOT NULL
)
SELECT
  id,
  arxiv_id,
  primary_category,
  score,
  title,
  abstract
FROM scored
WHERE score >= 0.40
  AND (
    primary_category IN (
      'cs.LG','cs.CL','cs.AI','cs.CV','cs.IR','cs.NE',
      'stat.ML','cs.MA','cs.SD','eess.AS','eess.IV'
    )
    OR LOWER(title || ' ' || abstract) LIKE '%large language model%'
    OR LOWER(title || ' ' || abstract) LIKE '%language model%'
    OR LOWER(title || ' ' || abstract) LIKE '%llm%'
    OR LOWER(title || ' ' || abstract) LIKE '%transformer%'
    OR LOWER(title || ' ' || abstract) LIKE '%retrieval augmented%'
    OR LOWER(title || ' ' || abstract) LIKE '%rag%'
    OR LOWER(title || ' ' || abstract) LIKE '%agent%'
    OR LOWER(title || ' ' || abstract) LIKE '%reasoning%'
    OR LOWER(title || ' ' || abstract) LIKE '%alignment%'
    OR LOWER(title || ' ' || abstract) LIKE '%safety%'
    OR LOWER(title || ' ' || abstract) LIKE '%hallucination%'
    OR LOWER(title || ' ' || abstract) LIKE '%multimodal%'
    OR LOWER(title || ' ' || abstract) LIKE '%diffusion%'
    OR LOWER(title || ' ' || abstract) LIKE '%quantization%'
    OR LOWER(title || ' ' || abstract) LIKE '%distillation%'
  )
ORDER BY score DESC
LIMIT 10000;
