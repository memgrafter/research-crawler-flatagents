# Research Paper Analysis v3 — Design Document

## Goal

Replace the current 7+ call multi-machine pipeline with a single analyzer machine that uses a pinned KV cache to fan out all content sections in parallel, then judges and repairs serially. Target: ~80% reduction in input tokens, fewer LLM calls, no growing context across turns.

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
  → fan_out_sections          (7 parallel agent calls)
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

### Analyzer Machine — Fan Out

All content sections run in **parallel**, no dependencies between them. Each call receives the same inputs: paper text (from pinned KV cache) + basic metadata.

**7 parallel calls:**

| Section | Input | Output |
|---------|-------|--------|
| key_outcome | paper, title, authors, abstract | problem, core method, primary results with metrics |
| why_mechanism | paper, title, abstract, corpus_signals | 3 mechanisms (claim, mechanism, assumption, evidence anchors, break condition) |
| foundational_learning | paper, title, abstract | 3-6 concepts with why-needed and quick-check question |
| architecture_onboarding | paper, title, abstract | component map, critical path, tradeoffs, failure signatures, first 3 experiments |
| reproduction | paper, title, abstract, reference_count | what is specified, minimum viable plan, unknowns, failure modes |
| open_questions | paper, title, abstract | 3-5 questions (explicit/inferred) with basis, why unresolved, resolving evidence |
| limits_confidence | paper, title, abstract | uncertainties, confidence labels (High/Medium/Low), 3 concrete next checks |

**Why no cross-dependencies?** Each section is written from the paper directly, not from the model's own prior output. This prevents hallucination from filling in blanks — the model doesn't see its own key_outcome when writing why_mechanism, so it can't propagate errors. The assembler just puts headers around the raw outputs.

### Analyzer Machine — Non-Agentic Assembler

A hook action that concatenates section outputs with proper markdown headers. No LLM call:

```python
def assemble_report_sections(context):
    context["report_body"] = (
        f"# {context['title']}\n\n"
        f"## Quick Facts\n- arXiv: {context['arxiv_id']}\n- Authors: {context['authors']}\n- References: {context['reference_count']}\n\n"
        f"## Executive Summary\n{context['key_outcome']}\n\n"
        f"## Method Summary\n{context['method_summary']}\n\n"
        f"## Key Results\n{...}\n\n"
        f"## Why This Works (Mechanism)\n{context['why_mechanism']}\n\n"
        f"## Foundational Learning\n{context['foundational_learning']}\n\n"
        f"## Architecture Onboarding\n{context['architecture_onboarding']}\n\n"
        f"## Open Questions the Paper Calls Out\n{context['open_questions']}\n\n"
        f"## Limitations\n{...}\n\n"
        f"## Confidence\n{...}\n\n"
        f"## Next Checks\n{...}\n"
    )
```

### Analyzer Machine — Judge with Scoring Rubric

The judge outputs structured JSON with per-section scores and feedback, not a single token.

**Scoring rubric:**

| Score | Meaning |
|-------|---------|
| 1 | Missing or unusable |
| 2 | Weak or placeholder |
| 3 | Adequate |
| 4 | Strong |

**Judge output format:**

```json
{
  "scores": {
    "key_outcome": 4,
    "why_mechanism": 2,
    "foundational_learning": 1,
    "architecture_onboarding": 3,
    "reproduction": 4,
    "open_questions": 3,
    "limits_confidence": 4
  },
  "feedback": {
    "why_mechanism": "Missing evidence anchors for mechanism 2; break conditions not specific",
    "foundational_learning": "Only one concept listed, needs 3-6 with why-needed and quick-check"
  }
}
```

### Analyzer Machine — Process Rubric Action

Deterministic logic from scores to action:

```python
def process_rubric(scores):
    # REJECT: overall quality too low (3+ sections scored 1)
    if sum(1 for s in scores.values() if s == 1) >= 3:
        return "REJECT"
    
    # PASS: all sections adequate or better, no weak sections
    if min(scores.values()) >= 3:
        return "PASS"
    
    # REPAIR: fixable issues (some sections scored 2, or only 1-2 at score 1)
    return "REPAIR"
```

### Analyzer Machine — Targeted Repair

With rubric feedback, repair knows exactly which sections to fix. The repair call is small: paper (from pinned cache) + key_outcome + section-specific feedback.

```yaml
repair_specific_sections:
  agent: analyzer
  mode: repair
  input:
    paper_text: context.cleaned_paper_text        # from pinned KV cache
    weak_sections: ["why_mechanism", "foundational_learning"]   # derived from rubric
    section_feedback: { ... }                        # from rubric
    current_section_content: { ... }                 # existing content for those sections
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

**Modes** (one per fan-out section + repair):

| Mode | Prompt template |
|------|----------------|
| `key_outcome` | "Write key outcome: problem, core method, primary results with metrics." |
| `why_mechanism` | "Write 3 mechanisms with claim/mechanism/assumption/evidence/break condition." |
| `foundational_learning` | "Write 3-6 concepts with why-needed and quick-check question." |
| `architecture_onboarding` | "Write component map, critical path, tradeoffs, failure signatures, first 3 experiments." |
| `reproduction` | "Write what is specified, minimum viable plan, unknowns, failure modes." |
| `open_questions` | "Write 3-5 questions with basis (explicit/inferred), why unresolved, resolving evidence." |
| `limits_confidence` | "Write uncertainties, confidence labels (High/Medium/Low), exactly 3 next checks." |
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
| fan-out: 7 sections | 7 × (~2K (pinned) + 1K) = ~21K | 7 parallel |
| judge | ~6K | 1 |
| repair (if needed) | ~2K (pinned) + 0.5K feedback | 1 |
| judge re-eval | ~6K | 1 |
| **Total** | **~34K** | **9-10 max, 8 if PASS first time** |

**~65% reduction in input tokens.** More parallel calls but each is bounded — no growing context. Wall-clock time is dominated by the slowest fan-out section, not the sum of all sections.

---

## Open Questions

1. **ar5iv as primary source** — Should prep fetch from ar5iv.labs.arxiv.org (cleaner HTML, no math artifacts) with PDF fallback? Not implemented yet, planned for later.

2. **Multiple repair passes** — Currently max one repair pass. With pinned cache, a second repair is cheap (just delta tokens). Worth considering but keeping it simple for v1.

3. **Limits/confidence input** — Currently limits_confidence in v2 takes key_outcome + why + repro as input. In v3 it gets only paper + metadata. The model writes limits from the paper directly rather than from its own prior output. This avoids error propagation but means the model might miss nuance that was captured in the why section. Acceptable tradeoff — the paper contains all the information needed.

4. **Judge rubric granularity** — Should the rubric score sub-sections (e.g., mechanism 1, mechanism 2, mechanism 3 separately) or just top-level sections? Starting with top-level; can add granularity later if repair needs more precision.

5. **Method Summary and Key Results sections** — Currently the report_assembler derives these from key_outcome + reproduction content. With non-agentic assembly, we need to decide: does key_outcome mode produce both Executive Summary AND Method Summary + Key Results? Or are those derived in the assembler from key_outcome content? Probably the latter — assembler splits key_outcome into sections.

6. **Section naming alignment** — The fan-out section names must align with judge rubric keys and repair feedback keys. Need a canonical mapping: section name → rubric key → report header.
