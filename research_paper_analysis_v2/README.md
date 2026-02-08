# Research Paper Analysis V2

Automated analysis pipeline for arXiv papers. Downloads PDFs, extracts text,
runs LLM-based analysis, and produces structured markdown reports with YAML
frontmatter, terminology tags, mechanism analysis, reproduction notes, and
confidence assessments.

Built on [FlatAgents](https://github.com/anthropics/flatagents) /
[FlatMachines](https://github.com/anthropics/flatagents).

## Architecture

The pipeline is split into three machines to maximize expensive model utilization.
Cheap and expensive calls never share a phase — when pony-alpha is available,
every API call in flight is pony-alpha.

```
prep_machine.yml (cheap model, 1 req)
┌────────────────────────────────────────────────┐
│  download_pdf → extract_text                   │
│  → collect_corpus_signals (FTS, no LLM)        │
│  → write_key_outcome (1 cheap req)             │
│  → save to v2 executions DB                    │
└────────────────────────────────────────────────┘
                    │
                    ▼  status: prepped
expensive_machine.yml (pony-alpha only, 3 req)
┌────────────────────────────────────────────────┐
│  parallel_expensive_writers                    │
│    ├─ why_hypothesis_machine  (1 pony-alpha)   │
│    ├─ reproduction_machine    (1 pony-alpha)   │
│    └─ open_questions_machine  (1 pony-alpha)   │
│  → save to v2 executions DB                    │
└────────────────────────────────────────────────┘
                    │
                    ▼  status: analyzed
wrap_machine.yml (cheap model, 3-5 req)
┌────────────────────────────────────────────────┐
│  write_limits_confidence   (1 cheap req)       │
│  → derive_terminology_tags (deterministic)     │
│  → assemble_report         (1 cheap req)       │
│  → judge_report            (1 cheap req)       │
│  → [repair loop: 0-2 cheap reqs]              │
│  → prepend_frontmatter                         │
│  → save report + mark done                     │
└────────────────────────────────────────────────┘
```

**Why three phases?** The cheap and expensive models share a daily quota.
By isolating expensive calls into their own phase, the runner can:
- Dedicate all worker slots to pony-alpha when it's available
- Batch cheap work (prep + wrap) separately, never wasting expensive availability
- Build a large prep buffer so expensive work can run continuously

## Quick start

```bash
cd research_paper_analysis_v2
uv sync

# Seed papers from the arxiv crawler DB, then run
python run.py --workers 3 --daemon
```

Or use the shell wrapper (auto-rebuilds venv):

```bash
./run.sh --workers 3 --daemon
```

## Usage

```bash
# Full daemon: seed, prep, expensive, wrap until done or budget exhausted
python run.py --workers 5 --daemon

# Prep only: fill buffer with cheap key_outcome calls
python run.py --workers 10 --prep-only --daemon

# Single pass (no loop): run one batch then exit
python run.py --workers 3

# Seed only: populate v2 executions DB from arxiv DB, no execution
python run.py --seed-only

# Custom budget and polling
python run.py --workers 5 --budget 500 --poll-interval 15 --daemon
```

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `-w`, `--workers` | 3 | Max concurrent machines |
| `-b`, `--budget` | 1000 | Daily request budget (shared cheap + expensive) |
| `-d`, `--daemon` | off | Poll loop until done or budget exhausted |
| `-p`, `--poll-interval` | 10.0 | Seconds between daemon passes |
| `--prep-only` | off | Only run prep phase (fill buffer) |
| `--seed-only` | off | Only seed from arxiv DB, no execution |
| `--min-buffer` | 20 | Min prepped papers before prioritizing analysis |
| `--seed-limit` | 500 | Max papers to seed per pass |

## Project structure

```
research_paper_analysis_v2/
├── config/
│   ├── profiles.yml                # Model profiles (cheap + expensive)
│   ├── prep_machine.yml            # Phase 1: prep pipeline
│   ├── expensive_machine.yml       # Phase 2: expensive-only pipeline
│   ├── wrap_machine.yml            # Phase 3: cheap wrap pipeline
│   ├── why_hypothesis_machine.yml  # Wrapper for parallel execution
│   ├── reproduction_machine.yml    # Wrapper for parallel execution
│   ├── open_questions_machine.yml  # Wrapper for parallel execution
│   ├── key_outcome_writer.yml      # Agent: key outcome (cheap)
│   ├── why_hypothesis_writer.yml   # Agent: mechanism analysis (expensive)
│   ├── reproduction_writer.yml     # Agent: reproduction notes (expensive)
│   ├── open_questions_writer.yml   # Agent: open questions (expensive)
│   ├── limits_confidence_writer.yml # Agent: limits/confidence (cheap)
│   ├── report_assembler.yml        # Agent: markdown assembly (cheap)
│   ├── completeness_judge.yml      # Agent: PASS/REPAIR/FAIL (cheap)
│   ├── targeted_repair.yml         # Agent: fix weak sections (cheap)
│   └── terminology_map.yml         # Canonical term → alias mapping
├── schema/
│   └── v2_executions.sql           # V2 executions DB schema
├── src/research_paper_analysis_v2/
│   ├── __init__.py
│   └── hooks.py                    # All hook actions (prep + expensive + wrap)
├── data/
│   ├── v2_executions.sqlite        # Execution tracking DB (auto-created)
│   ├── checkpoints/                # Machine persistence (auto-created)
│   ├── *.pdf                       # Downloaded papers
│   ├── *.txt                       # Extracted text
│   └── *.md                        # Generated reports
├── run.py                          # Runner: seed, prep, expensive, wrap, resume
├── run.sh                          # Shell wrapper (uv sync + run.py)
├── PLAN.md                         # Original v2 design document
├── IMPLEMENTATION_PLAN.md          # Persistence/parallelism migration plan
├── OPENROUTER_FREE_WORKERS.md      # Budget planning for OpenRouter free tier
├── FLATAGENTS_TIPS.md              # Design tips from v2 development
├── TODO.md                         # Outstanding work items
└── todos.md                        # SDK follow-ups
```

## Databases

| Database | Location | Access | Purpose |
|---|---|---|---|
| Arxiv DB | `../arxiv_crawler/data/arxiv.sqlite` | **Read-only** | Seed eligible papers, corpus signals (FTS, relevance, citations, authors) |
| V2 executions DB | `data/v2_executions.sqlite` | **Read-write** | Execution tracking, daily usage counter |

V2 never writes to the arxiv DB. Override paths with environment variables:
- `ARXIV_DB_PATH` — arxiv crawler database
- `V2_EXECUTIONS_DB_PATH` — v2 executions database

### Execution status flow

```
pending → prepping → prepped → analyzing → analyzed → wrapping → done
                   ↘          ↘            ↘
                  failed      failed      failed
```

### Executions table

| Column | Type | Description |
|---|---|---|
| `execution_id` | TEXT PK | Machine execution identity (UUID) |
| `arxiv_id` | TEXT UNIQUE | Paper identifier |
| `paper_id` | INTEGER | References arxiv DB papers.id |
| `title` | TEXT | Paper title |
| `authors` | TEXT | Paper authors |
| `abstract` | TEXT | Paper abstract |
| `status` | TEXT | pending / prepping / prepped / analyzing / analyzed / wrapping / done / failed |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |
| `prep_output` | TEXT | JSON: key_outcome, section_text, corpus_signals, etc. |
| `expensive_output` | TEXT | JSON: why_hypotheses, reproduction_notes, open_questions |
| `result_path` | TEXT | Path to output report .md |
| `error` | TEXT | Error message if failed |

## Models

Configured in `config/profiles.yml`:

| Profile | Model | Phase | Used by |
|---|---|---|---|
| `prototype_structured` (cheap) | `openrouter/openai/gpt-oss-120b:free` | Prep + Wrap | key_outcome, limits_confidence, report_assembler, completeness_judge, targeted_repair |
| `prototype_reasoning` (expensive) | `openrouter/openrouter/pony-alpha` | Expensive | why_hypothesis, reproduction, open_questions |

## Persistence and resume

All three machines checkpoint after every state transition. Checkpoints are stored
in `data/checkpoints/{execution_id}/`. If the process dies:

1. Next run finds incomplete checkpoints via `data/checkpoints/*/latest`
2. Resumes from the last completed state
3. All intermediate results (key_outcome, section_text, etc.) are preserved

The runner always **resumes before starting new work** (depth-first completion).

### Stale execution recovery

| Status | Max age | Falls back to |
|---|---|---|
| `prepping` | 60 min | `pending` |
| `analyzing` | 120 min | `prepped` |
| `wrapping` | 60 min | `analyzed` |

## Budget management

The cheap and expensive models share a 1000 req/day OpenRouter quota. The runner
tracks usage in the `daily_usage` table and stops when the budget is exhausted.

**Request costs per paper:**
- Prep: 1 cheap request
- Expensive: 3 pony-alpha requests (all parallel)
- Wrap: 3–5 cheap requests

**Budget strategy (maximize pony-alpha):**
1. Expensive gets worker priority — when prepped papers exist, run pony-alpha first
2. Wrap clears analyzed papers — fast cheap work, keeps pipeline flowing
3. Prep fills buffer — builds queue for next expensive burst
4. `--prep-only` mode fills buffer without burning expensive quota

**Daily capacity at 1000 req budget:**
- Prep 50 papers: 50 cheap calls
- Expensive 50 papers: 150 pony-alpha calls
- Wrap 50 papers: 150-250 cheap calls
- Total: ~350-450 calls for 50 complete papers
- Or: maximize expensive → ~200 pony-alpha calls = ~66 papers through expensive phase

## Hooks reference

All actions in `src/research_paper_analysis_v2/hooks.py`:

### Prep actions (prep_machine.yml)
| Action | Description |
|---|---|
| `download_pdf` | Downloads PDF from arxiv to `data/` |
| `extract_text` | Extracts text from PDF, parses sections and references |
| `collect_corpus_signals` | FTS neighbor search, FMR scores, citation counts (read-only arxiv DB) |
| `save_prep_result` | Writes prep output JSON to v2 DB, sets status=prepped |

### Expensive actions (expensive_machine.yml)
| Action | Description |
|---|---|
| `unpack_expensive_results` | Unpacks 3 parallel machine outputs into context fields |
| `save_expensive_result` | Writes expensive output JSON to v2 DB, sets status=analyzed |

### Wrap actions (wrap_machine.yml)
| Action | Description |
|---|---|
| `derive_terminology_tags` | Deterministic tag scoring from paper text, corpus, taxonomy |
| `prepend_frontmatter_v2` | Generates YAML frontmatter and prepends to report |
| `normalize_judge_decision` | Extracts PASS/REPAIR/FAIL from judge output |
| `set_repair_attempted` | Sets repair flag to prevent infinite repair loops |
| `save_wrap_result` | Writes report .md to disk, sets status=done in v2 DB |
| `mark_execution_failed` | Sets status=failed with error message (shared across phases) |

## Report format

Each report is a markdown file with YAML frontmatter:

```yaml
---
ver: rpa2
title: "Paper Title"
arxiv_id: "2508.17400"
source_url: https://arxiv.org/abs/2508.17400
tags: [retrieval, scaling-laws, embedding, ...]
core_contribution: "One-line summary..."
---
```

Followed by sections:
- Quick Facts
- Executive Summary
- Method Summary
- Key Results
- Why This Works (Mechanism)
- Foundational Learning
- Architecture Onboarding
- Open Questions the Paper Calls Out
- Limitations
- Confidence
- Next Checks
