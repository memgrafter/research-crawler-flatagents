# Research Paper Analysis V2 Plan

## Vision
Build a simpler, quality-first pipeline that generates useful, scannable reports with:
1. YAML frontmatter (including terminology tags for retrieval/clustering)
2. Key outcome summary
3. Why-it-matters analysis with hypotheses (not final claims)
4. Practical reproduction notes
5. Limits/confidence/next checks

The pipeline should avoid heavy JSON extractor/fixer chains, minimize retries, and keep one shared completeness judge.

---

## Design Principles
- **Single responsibility per step** (content extraction, why-analysis, reproduction, formatting, judging)
- **Prefer deterministic actions** (SQL/FTS/tag normalization) over extra LLM calls
- **Use one shared judge** for content presence/completeness, not factual adjudication
- **At most one repair pass** to control latency/cost
- **Token targets are soft guidance** (no retry loop for length)

---

## High-Level Architecture

```text
prepare_paper (existing parse/download)
   |
   v
collect_corpus_signals (SQL/FTS action, no LLM)
   |
   +--> write_key_outcome
   +--> write_why_hypotheses
   +--> write_reproduction
   +--> write_limits_confidence
   |
   v
derive_terminology_tags (deterministic + optional cheap normalization)
   |
   v
assemble_report (deterministic template)
   |
   v
completeness_judge (shared)
   |--- pass --> prepend_frontmatter_v2 --> done
   |
   +--- fail --> targeted_repair_once --> completeness_judge --> done/fail
```

---

## Report Contract (V2)

## 1) YAML Frontmatter
Includes metadata, quality gate result, corpus context, and terminology tags.

## 2) Key Outcome Snapshot
What the paper did and the main measurable outcome.
- Soft target: **120–180 tokens**

## 3) Why It Matters (Hypothesis Ledger)
Generate plausible hypotheses for impact/significance.
For each hypothesis include:
- hypothesis statement
- why plausible
- existing evidence from this paper/corpus
- what evidence could invalidate it
- what evidence could strengthen it
- Soft target: **350–550 tokens**

## 4) Technical Reproduction Notes
Concise practical reproduction guidance:
- setup assumptions
- data/task assumptions
- model/training/evaluation essentials
- expected failure modes
- Soft target: **220–380 tokens**

## 5) Limits / Confidence / Next Checks
Clearly separate knowns vs unknowns.
- confidence tags (high/medium/low)
- 3 concrete validation checks
- Soft target: **120–220 tokens**

---

## Frontmatter Schema (Draft)

```yaml
---
title: "..."
arxiv_id: "..."
source_url: "..."
generated_at: "2026-02-06T14:00:00Z"
pipeline_version: "rpa-v2"

quality_gate:
  pass: true
  score: 8
  missing_items: []

corpus_context:
  neighbors_considered: 25
  neighbors_used: 8
  relevance_signal: "fmr+citation+author_h"

token_targets:
  key_outcome: "120-180"
  why_hypotheses: "350-550"
  reproduction: "220-380"
  limits_confidence: "120-220"

terminology_tags:
  - transformer
  - self-attention
  - sequence-modeling
  - scaling-laws

domain_tags:
  - core_architecture_components
  - evaluation_benchmarking

terminology_tag_meta:
  transformer:
    weight: 0.92
    sources: [paper_text, corpus_neighbors, taxonomy]
  self-attention:
    weight: 0.88
    sources: [paper_text, taxonomy]

tagging:
  taxonomy_version: "word_clouds_v1"
  canonicalization_map: "config/terminology_map.yml"
---
```

---

## Terminology Tagging Strategy

### Inputs
- paper title/abstract/section headers/body snippets
- top-K related corpus neighbors via FTS
- taxonomy seeds from `queries/word_clouds/*.txt`

### Method
1. Candidate term extraction (noun-phrase/keyword + acronym capture)
2. Canonicalization (synonym map, singularization, kebab-case slugs)
3. Scoring per term:
   - paper salience
   - corpus support
   - taxonomy alignment
4. Keep top **8–15** tags
5. Emit `terminology_tags`, optional `domain_tags`, and `terminology_tag_meta`

### Constraints
- No speculative tags unsupported by text/corpus/taxonomy
- Prefer stable canonical terms for clustering/search consistency

---

## Corpus Signals (Action Layer, No LLM)
Use SQLite to build compact evidence pack:
- related papers (FTS match + latest version)
- relevance score (`paper_relevance.fmr_score`)
- citation count (`paper_citations.cited_by_count`)
- author impact (`authors.h_index` via `paper_authors`)

Output: `corpus_signals` object consumed by writer agents.

---

## Agent Responsibilities

### `write_key_outcome`
- Extract concise outcome and key measurable points
- No formatting/judging

### `write_why_hypotheses`
- Produce hypothesis ledger (no final truth claims)
- Tie hypotheses to available evidence

### `write_reproduction`
- Provide practical reproduction scaffold
- Avoid overlong protocol-level detail

### `write_limits_confidence`
- Explicit uncertainties and next checks
- Confidence labels per claim cluster

### `completeness_judge` (shared)
- Check section presence, non-empty content, markdown integrity, no placeholders
- Not a factual correctness judge

### `targeted_repair_once`
- Fill missing/empty sections only
- Preserve existing good sections

---

## Model Class Selection
- `collect_corpus_signals`: **no model**
- `write_key_outcome`: **precision extraction model**, temp 0.1–0.2
- `write_why_hypotheses`: **strong reasoning model**, temp 0.3–0.5
- `write_reproduction`: **precision technical model**, temp 0.1–0.2
- `write_limits_confidence`: **precision model**, temp 0.1–0.2
- `completeness_judge`: **cheap strict model**, temp 0.0
- `targeted_repair_once`: **cheap precision model**, temp 0.1–0.2

---

## Cost/Latency Controls
- Single-pass writers; no iterative self-critique loop
- One shared judge + max one repair pass
- Soft token targets only (no length-based retries)
- Deterministic assembly/frontmatter where possible

---

## Initial Build Plan
1. Create config skeleton (`machine.yml`, new agent YAMLs)
2. Implement `collect_corpus_signals` action in hooks
3. Implement deterministic `derive_terminology_tags`
4. Implement report assembly template
5. Implement shared completeness judge + one repair route
6. Run on 5–10 papers and inspect quality/cost deltas

---

## Success Criteria
- Fewer states/agents than v1, easier to reason about
- Report always includes all required sections
- Terminology tags are stable and useful for retrieval/clustering
- Lower average API calls and latency vs v1
- Better subjective usefulness of “Why It Matters” section
