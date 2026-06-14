# v3 Session Summary — 2026-06-12

## What was built

### Digest Format (DESIGN_DRAFT2.md)

New format replaces v2's redundant layers with:
- **YAML frontmatter** — structured fields for machine readability (domain, task, key_metric, novelty_level, reproducibility)
- **Narrative lead** ("What This Paper Did") — 2-3 paragraphs giving coherent mental model before details
- **10 structured sections**: Method, Key Results, Why It Works, What's Specified, What's Missing, Author Uncertainties & Hedges, Open Questions, Failure Modes & Limitations, Confidence Assessment, Next Checks

Removed: Quick Facts (redundant with frontmatter), separate Executive Summary/Method Summary/Key Results layers, Foundational Learning, Architecture Onboarding (spread into other sections)

Prototype digest: `docs/attention-is-all-you-need-digest-prototype-20260612.md` (~2100 tokens)

### Machine Architecture

**Single analyzer machine** with 6 parallel fan-out sub-machines, KV cache warmup, non-agentic assembly, judge with scoring rubric, repair loop.

Flow: `warmup → fan-out (6 parallel) → assemble → judge → PASS/REPAIR/FAIL`

**Files:**

| File | Purpose |
|------|---------|
| `config/analyzer_machine.yml` | Main pipeline — warmup → fan-out → assemble → judge → repair |
| `config/kv_cache_machine.yml` | KV cache warmup sub-machine (runs before fan-out) |
| `config/machines/narrative_lead_machine.yml` | Fan-out: What This Paper Did + Author Uncertainties |
| `config/machines/method_results_machine.yml` | Fan-out: Method + Key Results |
| `config/machines/why_mechanism_machine.yml` | Fan-out: Why It Works (mechanisms) |
| `config/machines/reproduction_machine.yml` | Fan-out: What's Specified + What's Missing |
| `config/machines/open_questions_machine.yml` | Fan-out: Open Questions |
| `config/machines/limits_confidence_machine.yml` | Fan-out: Failure Modes + Confidence + Next Checks |
| `config/agents/analyzer.flatagent.yml` | Single agent with mode-based prompt templating (Jinja2) |
| `config/agents/completeness_judge.flatagent.yml` | Judge with per-section scoring rubric (1-4, outputs JSON) |
| `config/agents/kv_cache_warmup.flatagent.yml` | Warmup agent — sends shared prefix through LLM |
| `config/prompts/analyzer.prompt.yml` | Mode-switching prompt (narrative_lead, method_results, why_mechanism, reproduction, open_questions, limits_confidence, repair) |
| `config/prompts/completeness_judge.prompt.yml` | Judge prompt — checks new v3 section headers |
| `config/prompts/kv_cache_warmup.prompt.yml` | Warmup prompt — "This is a prefix cache event, reply ok" |
| `config/profiles.yml` | Points to local llama.cpp at 192.168.1.21:8081 |
| `src/research_paper_analysis_v3/hooks.py` | All non-agentic actions: unpack_fan_out_results, assemble_report, normalize_judge_decision, mark_cache_pinned, prepend_frontmatter_v3, save_analyzer_result, mark_execution_failed |
| `pyproject.toml` | Dependencies: flatagents, flatmachines, pyyaml |

### KV Cache Warmup (PROVEN WORKING)

Warmup sends system prompt + full paper text through LLM before fan-out. The serving backend's prefix cache auto-saves the processed prefix. Fan-out calls then hit 99.9% cache read rate — only paying for ~12-13 new tokens per call instead of ~12,800.

Dry-run results (`dry_run_fanout.py`):
- Warmup: 12,812 input tokens, 2 output tokens ("ok")
- Fan-out: 6 calls × ~12,950 input tokens each, but ~12,930 from cache per call
- Total fan-out: 77,730 input tokens, 77,652 cached (99.9%)

### Tickets

```
rpav-b3m1  Write v3 DB schema          ← ready to start
rpav-dxor  Write prep machine           ← depends on b3m1
rpav-1xkn  Write main runner (scheduler) ← depends on dxor + b3m1
```

### What's NOT done yet

- **Prep machine** — no download/extract/clean pipeline for v3
- **Main runner** — no run.py scheduler
- **DB schema** — no v3 executions table
- **Judge rubric alignment** — the judge prompt checks for new section headers but hasn't been tested end-to-end
- **End-to-end test** — analyzer machine has never been run as a FlatMachine (only individual agent calls via dry-run scripts)

### Key design decisions to remember

1. **Narrative lead is critical** — v2 digests read like spec sheets; the narrative gives coherence before details
2. **Author Uncertainties & Hedges is new** — captures where authors themselves hedged, distinct from analyst confidence
3. **What's Specified / What's Missing** replaces vague reproduction section — actionable split for coding agents
4. **Non-agentic assembly** — string concatenation, not an LLM call
5. **Judge outputs structured JSON** (scores + feedback), not a single PASS/REPAIR/FAIL token
6. **KV cache warmup is its own machine**, not part of fan-out — clear separation for later pin API integration

### Dry-run scripts

- `dry_run_warmup.py` — tests warmup agent call
- `dry_run_fanout.py` — tests warmup + sequential fan-out, measures cache hit rate
- Both work; can be kept as test harness or deleted later
