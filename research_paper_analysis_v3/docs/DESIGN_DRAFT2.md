# Research Paper Analysis v3 — Design Document

## Goal

Replace the current 7+ call multi-machine pipeline with a single analyzer machine that uses a pinned KV cache to fan out all content sections in parallel, then judges and repairs serially. Produce digests optimized for agent-first consumption: structured frontmatter for machine readability, narrative lead for coherence, analytical depth for decision-making.

Target: ~80% reduction in input tokens, no growing context across turns, predictable token budget per digest.

---

## Current Architecture (v2) — The Problem

Three machines, 7-8 LLM calls per paper, each re-sending the full paper text (~14K tokens):

```
prep_machine:      key_outcome (1 call)
expensive_machine: why + reproduction + open_questions (3 parallel calls)
wrap_machine:      limits (1) → assemble (1) → judge (1) → repair (0-1)
```

Problems:
- Paper text sent to every agent — ~98K input tokens total
- Growing context in later calls (limits_confidence sees key_outcome + why + repro)
- Report assembler is an LLM call that just concatenates sections
- Judge outputs a single token (PASS/REPAIR/FAIL) with no diagnostic information
- Repair agent re-reads the entire report to figure out what's wrong

**Digest format problems:**
- Redundant layers (Quick Facts, Executive Summary, Method Summary all repeat the same info)
- No structured frontmatter for machine-readable queries
- Missing signals: author hedging, concrete reproduction gaps, use-case relevance
- Token count varies wildly by paper complexity and model whimsy
- Reads like a spec sheet, not a coherent story — no narrative spine

---

## New Architecture (v3)

### Two Machines

**prep_machine** — download, extract, clean paper. Produces cleaned_paper_text and corpus signals. No LLM calls.

```
download_pdf → extract_text → clean_paper → collect_corpus_signals → save_prep_result
```

**analyzer_machine** — all content generation, judging, and repair in one machine.

```
start
  → fan_out_sections          (parallel agent calls)
    → assemble_report         (non-agentic action)
      → judge                 (scoring rubric → JSON)
        → process_rubric      (deterministic: PASS / REPAIR / REJECT)
          ├─ PASS  → prepend_frontmatter → save_result → done
          ├─ REJECT → failed_incomplete
          └─ REPAIR → repair_specific_sections → assemble_report → judge → ...
```

### Prep Machine

Same as current v2 prep but adds `clean_paper` and drops `write_key_outcome`:

| Action | Purpose |
|--------|---------|
| `download_pdf` | Download PDF (ar5iv HTML preferred, PDF fallback) |
| `extract_text` | Extract text from PDF or parse ar5iv HTML |
| `clean_paper` | Strip `[PAGE N]` markers, collapse blank lines, join broken wraps, remove references section, remove appendices, normalize encoding |
| `collect_corpus_signals` | Neighbor search in arxiv DB |
| `save_prep_result` | Persist to v2_executions DB |

Output: `cleaned_paper_text`, `corpus_signals`, `corpus_neighbors`, metadata.

---

## Digest Format

The digest has two parts: **YAML frontmatter** (machine-readable signals) and **Markdown body** (narrative lead + structured sections).

### Frontmatter

```yaml
---
title: Attention Is All You Need
arxiv_id: '1706.03762'
year: 2017
domain: nlp, machine-translation
task: sequence-transduction (encoder-decoder)
key_metric: BLEU (EN-DE: 28.4, EN-FR: 41.8)
baseline_beaten: ensemble RNN attention models
novelty_level: paradigm-shifting
reproducibility: high
code_available_at_publication: no
---
```

Structured fields for filtering and agent querying without parsing prose. A coding agent can check `domain`, `task`, `key_metric`, `novelty_level` without reading the body.

### Body Structure

**Narrative lead:**

```markdown
## What This Paper Did

[2-3 paragraphs telling the story: what came before, what they did, why it matters, the key insight. Gives you a mental model before hitting details.]
```

This is the most important section — it gives coherence. Without it, the digest reads like a spec sheet. With it, you understand the paper after two paragraphs and can then drill into specifics.

**Structured sections (for lookup, not sequential reading):**

```markdown
## Method
[Architecture, algorithm, key design choices. Not a summary — the actual method.]

## Key Results
[Metrics, comparisons. Table format preferred when applicable.]

## Why It Works
[2-3 mechanisms. Each: claim → mechanism → assumption → break condition.]

## What's Specified (for reproduction)
[Concrete hyperparameters, datasets, training details that ARE in the paper.]

## What's Missing (blocking reproduction)
[Gaps — things you'd need to guess or search for elsewhere.]

## Author Uncertainties & Hedges
[Where did the authors themselves express doubt, hand-wave, or defer?
Distinct from analyst confidence — captures author honesty.]

## Open Questions
[3-5 questions (from paper + inferred). Each: question → basis → why unresolved.]

## Failure Modes & Limitations
[Known issues, edge cases, complexity barriers.]

## Confidence Assessment
[High/Medium/Low per claim cluster. Brief justification.]

## Next Checks
[3 concrete experiments/validations to run.]
```

### Format Design Decisions

1. **No redundant layers.** No Quick Facts (that's frontmatter). No Executive Summary separate from "What This Paper Did." One section per topic, stated once.

2. **"What's Specified" + "What's Missing"** replaces the vague reproduction section. For coding agents, this is the most actionable split — tells you immediately what you can copy and what you need to figure out.

3. **"Author Uncertainties & Hedges"** is new. Captures where authors said "we conjecture," "it remains unclear," "future work." Gold for knowledge synthesis — tells you the boundaries of the authors' own confidence.

4. **No Architecture Onboarding as a named section.** Its content spreads more naturally: component map → Method, failure signatures → Failure Modes, first experiments → Next Checks. Each section becomes more focused.

5. **No Foundational Learning.** Prerequisite concepts are useful for human learning but add tokens that coding agents (which have broad knowledge) don't need. If a concept is truly essential for understanding the method, it belongs in the Method section as inline explanation.

6. **Token budget target: ~2000-3000 tokens per digest.** The prototype Attention Is All You Need digest is ~2100 tokens. At this size, ~50-80 papers fit in a 160K context window for synthesis — vs. ~14-20 at current v2 length.

---

## Analyzer Machine — Fan Out

### Section Mapping to Fan-Out Calls

Each fan-out mode produces one or more digest sections. The paper text is pinned in KV cache; each call pays for the paper once + its own short prompt.

**6 parallel calls (no cross-dependencies):**

| Mode | Digest sections produced | Input |
|------|------------------------|-------|
| `narrative_lead` | What This Paper Did | paper, title, authors, abstract, corpus_signals |
| `method_results` | Method, Key Results | paper, title, abstract |
| `why_mechanism` | Why It Works | paper, title, abstract |
| `reproduction` | What's Specified, What's Missing | paper, title, abstract |
| `open_questions` | Open Questions | paper, title, abstract |
| `limits_confidence` | Failure Modes & Limitations, Confidence Assessment, Next Checks | paper, title, abstract |

**Why no cross-dependencies?** Each section is written from the paper directly, not from the model's own prior output. This prevents hallucination propagation — the model doesn't see its own narrative lead when writing why_mechanism, so it can't propagate errors. The assembler just puts headers around the raw outputs.

**Note on `author_uncertainties`:** This section is produced by the `narrative_lead` mode alongside "What This Paper Did." The rationale: the model is already doing close reading of author language for the narrative, and capturing hedges is a natural extension of that task — it's about how the authors wrote, which the narrative mode is already focused on.

### Non-Agentic Assembler

A hook action that concatenates section outputs with proper markdown headers. No LLM call:

```python
def assemble_report_sections(context):
    context["report_body"] = (
        f"# {context['title']}\n\n"
        f"{context['narrative_lead']}\n\n"          # includes What This Paper Did + Author Uncertainties
        f"{context['method_results']}\n\n"          # Method + Key Results
        f"{context['why_mechanism']}\n\n"
        f"{context['reproduction']}\n\n"            # What's Specified + What's Missing
        f"{context['open_questions']}\n\n"
        f"{context['limits_confidence']}\n"         # Failure Modes + Confidence + Next Checks
    )
```

### Judge with Scoring Rubric

The judge outputs structured JSON with per-section scores and feedback.

**Scoring rubric:**

| Score | Meaning |
|-------|---------|
| 1 | Missing or unusable |
| 2 | Weak or placeholder |
| 3 | Adequate |
| 4 | Strong |

**Rubric keys** (aligned with fan-out modes):

```json
{
  "scores": {
    "narrative_lead": 4,
    "author_uncertainties": 3,
    "method_results": 4,
    "why_mechanism": 2,
    "reproduction": 4,
    "open_questions": 3,
    "limits_confidence": 4
  },
  "feedback": {
    "why_mechanism": "Missing break conditions for mechanism 2; assumptions not explicit",
    "author_uncertainties": "Only two hedges identified, paper has more"
  }
}
```

### Process Rubric Action

Deterministic logic from scores to action:

```python
def process_rubric(scores):
    # REJECT: overall quality too low (3+ sections scored 1)
    if sum(1 for s in scores.values() if s == 1) >= 3:
        return "REJECT"

    # PASS: all sections adequate or better
    if min(scores.values()) >= 3:
        return "PASS"

    # REPAIR: fixable issues
    return "REPAIR"
```

### Targeted Repair

With rubric feedback, repair knows exactly which sections to fix. The repair call is small: paper (from pinned cache) + section-specific feedback.

```yaml
repair_specific_sections:
  agent: analyzer
  mode: repair
  input:
    paper_text: context.cleaned_paper_text        # from pinned KV cache
    weak_sections: ["why_mechanism"]              # derived from rubric
    section_feedback: { ... }                      # from rubric
    current_section_content: { ... }               # existing content for those sections
```

The model rewrites only the weak sections. Assembler recomposes, judge re-evaluates. Max one repair pass (same as v2).

---

## KV Cache Pinning

The serving backend supports pinning KV cache via a proxy API. The paper text (~1-2K tokens after cleaning) is pinned once per execution. Every analyzer call — fan-out sections, repair — reads the paper from the pinned cache rather than re-processing it.

**Key properties:**
- Pin is per-execution (per paper), not global
- Multiple concurrent executions can pin independently
- The cache is reused across all turns of the same execution
- No growing context — each call pays for paper once + its own short prompt

---

## Agent Design

One agent definition (`analyzer.flatagent.yml`), multiple modes selected by the machine action. Same system prompt, different user prompt templates per mode.

**System prompt** (constant across all modes):
```
You are a technical research analyst for ML papers. Be precise and evidence-aware.
Never use markdown code fences.
```

**Modes:**

| Mode | Prompt template |
|------|----------------|
| `narrative_lead` | "Write 'What This Paper Did' (2-3 paragraphs telling the story: context, what they did, why it matters, key insight). Then write 'Author Uncertainties & Hedges' — where did the authors express doubt, hand-wave, or defer?" |
| `method_results` | "Write 'Method' (architecture, algorithm, key design choices with specifics) and 'Key Results' (metrics, comparisons, table format if applicable)." |
| `why_mechanism` | "Write 'Why It Works' with 2-3 mechanisms. Each must include: claim, mechanism explanation, assumption, break condition." |
| `reproduction` | "Write 'What's Specified' (concrete details present in the paper for reproduction) and 'What's Missing' (gaps blocking full reproduction)." |
| `open_questions` | "Write 3-5 open questions. Each: question, basis in paper (explicit/inferred), why unresolved, what evidence would resolve it." |
| `limits_confidence` | "Write 'Failure Modes & Limitations', then 'Confidence Assessment' (High/Medium/Low per claim cluster with brief justification), then 'Next Checks' (3 concrete experiments/validations)." |
| `repair` | "Fix these specific sections based on judge feedback: ..." |

The judge agent (`completeness_judge.flatagent.yml`) is a separate agent with its own structured prompt that outputs the scoring rubric JSON.

---

## Token Budget Comparison

**Current v2 per paper:**

| Step | Input tokens | Calls |
|------|-------------|-------|
| key_outcome | ~14K (paper) + 1K | 1 |
| why + repro + open_q | 3 × (~14K + 2K) | 3 |
| limits | ~5K (key_outcome + why + repro) | 1 |
| assemble | ~5K (all sections) | 1 |
| judge | ~4K | 1 |
| repair | ~4K | 0-1 |
| **Total** | **~98K** | **7-8** |

**Proposed v3 per paper:**

| Step | Input tokens | Calls |
|------|-------------|-------|
| fan-out: 6 sections | 6 × (~2K pinned + 1K) = ~18K | 6 parallel |
| judge | ~5K | 1 |
| repair (if needed) | ~2K pinned + 0.5K feedback | 1 |
| judge re-eval | ~5K | 1 |
| **Total** | **~28K** | **8-9 max, 7 if PASS first time** |

**~71% reduction in input tokens.** Fewer parallel calls than draft 1 (6 vs 7) because we eliminated Foundational Learning and merged Architecture Onboarding into other sections. Each call is bounded — no growing context. Wall-clock time dominated by the slowest fan-out section.

---

## Open Questions

1. **ar5iv as primary source** — Should prep fetch from ar5iv.labs.arxiv.org (cleaner HTML, no math artifacts) with PDF fallback? Not implemented yet, planned for later.

2. **Multiple repair passes** — Currently max one repair pass. With pinned cache, a second repair is cheap (just delta tokens). Worth considering but keeping it simple for v1.

3. **Author Uncertainties in narrative_lead mode** — We're bundling this with the narrative lead because both require close reading of author language. Alternative: give it its own fan-out call. Tradeoff: one more parallel call vs. potentially better quality from focused attention.

4. **Judge rubric granularity** — Should the rubric score sub-sections (e.g., mechanism 1, mechanism 2 separately) or just top-level sections? Starting with top-level; can add granularity later if repair needs more precision.

5. **Frontmatter field set** — Is the current set of frontmatter fields right? Should we add `related_work_positioning`? Should `novelty_level` be more granular (e.g., incremental/significant/paradigm-shifting)?

6. **Token budget enforcement** — Should prompts include explicit token/length constraints per section to ensure predictable digest sizes? E.g., "Method: max 400 words." Currently relying on prompt specificity to control length implicitly.

7. **Section naming alignment** — The fan-out mode names, judge rubric keys, and report headers must align. Need a canonical mapping document: mode → rubric key → report header → sections produced.
