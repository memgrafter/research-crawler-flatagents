# Research Paper Analysis V2

Automated analysis pipeline for arXiv papers. Downloads PDFs, extracts text,
runs LLM-based analysis, and produces structured markdown reports with YAML
frontmatter, terminology tags, mechanism analysis, reproduction notes, and
confidence assessments.

Built on [FlatAgents](https://github.com/anthropics/flatagents) /
[FlatMachines](https://github.com/anthropics/flatagents).

## Architecture

The pipeline is split into two machines with independent persistence and
concurrency control:

```
prep_machine.yml (cheap model, high concurrency)
┌──────────────────────────────────────────────────┐
│  download_pdf → extract_text                     │
│  → collect_corpus_signals (FTS, no LLM)          │
│  → write_key_outcome (1 cheap req)               │
│  → save to v2 executions DB                      │
└──────────────────────────────────────────────────┘
                    │
                    ▼  status: prepped
analysis_machine.yml (expensive model, low concurrency)
┌──────────────────────────────────────────────────┐
│  parallel_expensive_writers                       │
│    ├─ why_hypothesis_machine (1 expensive req)    │
│    └─ reproduction_machine   (1 expensive req)    │
│  → write_limits_confidence   (1 cheap req)        │
│  → write_open_questions      (1 expensive req)    │
│  → derive_terminology_tags   (deterministic)      │
│  → assemble_report           (1 cheap req)        │
│  → judge_report              (1 cheap req)        │
│  → [repair loop: 0-2 cheap reqs]                  │
│  → prepend_frontmatter                            │
│  → save report + mark done                        │
└──────────────────────────────────────────────────┘
```

**Why two machines?** The cheap and expensive models share a daily quota.
Prep runs at high concurrency to build a buffer. Analysis runs when the
expensive model is available. This maximizes expensive-model utilization
without wasting quota on cheap work during availability windows.

Both machines use `persistence: {enabled: true, backend: local}`. If the
process dies mid-pipeline, restart picks up from the last checkpoint.

## Quick start

```bash
cd research_paper_analysis_v2
uv sync

# Seed papers from the arxiv crawler DB, then run
python run.py --workers 3 --daemon
```

## Usage

```bash
# Full daemon: seed, prep, and analyze until done or budget exhausted
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
│   ├── prep_machine.yml            # Prep pipeline machine
│   ├── analysis_machine.yml        # Analysis pipeline machine
│   ├── why_hypothesis_machine.yml  # Wrapper for parallel execution
│   ├── reproduction_machine.yml    # Wrapper for parallel execution
│   ├── key_outcome_writer.yml      # Agent: key outcome (cheap)
│   ├── why_hypothesis_writer.yml   # Agent: mechanism analysis (expensive)
│   ├── reproduction_writer.yml     # Agent: reproduction notes (expensive)
│   ├── limits_confidence_writer.yml # Agent: limits/confidence (cheap)
│   ├── open_questions_writer.yml   # Agent: open questions (expensive)
│   ├── report_assembler.yml        # Agent: markdown assembly (cheap)
│   ├── completeness_judge.yml      # Agent: PASS/REPAIR/FAIL (cheap)
│   ├── targeted_repair.yml         # Agent: fix weak sections (cheap)
│   └── terminology_map.yml         # Canonical term → alias mapping
├── schema/
│   └── v2_executions.sql           # V2 executions DB schema
├── src/research_paper_analysis_v2/
│   ├── __init__.py
│   └── hooks.py                    # All hook actions (prep + analysis)
├── data/
│   ├── v2_executions.sqlite        # Execution tracking DB (auto-created)
│   ├── checkpoints/                # Machine persistence (auto-created)
│   ├── *.pdf                       # Downloaded papers
│   ├── *.txt                       # Extracted text
│   └── *.md                        # Generated reports
├── run.py                          # Runner: seed, prep, analysis, resume
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
pending → prepping → prepped → analyzing → done
                   ↘          ↘
                  failed      failed
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
| `status` | TEXT | pending / prepping / prepped / analyzing / done / failed |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |
| `prep_output` | TEXT | JSON blob: key_outcome, section_text, corpus_signals, etc. |
| `result_path` | TEXT | Path to output report .md |
| `error` | TEXT | Error message if failed |

### Daily usage table

| Column | Type | Description |
|---|---|---|
| `date` | TEXT PK | YYYY-MM-DD |
| `total_calls` | INTEGER | Total API calls today |
| `cheap_calls` | INTEGER | Cheap model calls |
| `expensive_calls` | INTEGER | Expensive model calls |

## Models

Configured in `config/profiles.yml`:

| Profile | Model | Used by |
|---|---|---|
| `prototype_structured` (cheap) | `openrouter/openai/gpt-oss-120b:free` | key_outcome, limits_confidence, report_assembler, completeness_judge, targeted_repair |
| `prototype_reasoning` (expensive) | `openrouter/openrouter/pony-alpha` | why_hypothesis, reproduction, open_questions |

## Persistence and resume

Both machines checkpoint after every state transition. Checkpoints are stored
in `data/checkpoints/{execution_id}/`. If the process dies:

1. Next run finds incomplete checkpoints via `data/checkpoints/*/latest`
2. Resumes from the last completed state
3. All intermediate results (key_outcome, section_text, etc.) are preserved

The runner always **resumes before starting new work** (depth-first completion).

### Stale execution recovery

Executions stuck in `prepping` for >60 minutes or `analyzing` for >120 minutes
are automatically released back to `pending` or `prepped` respectively.

## Budget management

The cheap and expensive models share a 1000 req/day OpenRouter quota. The runner
tracks usage in the `daily_usage` table and stops when the budget is exhausted.

**Request costs per paper:**
- Prep: 1 cheap request (key_outcome)
- Analysis: 3 expensive + 3–5 cheap = 6–8 requests

**Daily capacity at 1000 req budget:**
- ~40 papers prepped + ~130 papers analyzed (typical)
- Prep buffer allows immediate analysis starts when expensive model is available

**Budget strategy:**
- Runner prioritizes analysis over prep when prepped buffer is above `--min-buffer`
- When buffer is low, runner fills it with cheap prep calls
- `--prep-only` mode burns budget exclusively on prep (good for end-of-day buffer fill)

## Hooks reference

All actions are implemented in `src/research_paper_analysis_v2/hooks.py`:

### Prep actions
| Action | Description |
|---|---|
| `download_pdf` | Downloads PDF from arxiv to `data/` |
| `extract_text` | Extracts text from PDF, parses sections and references |
| `collect_corpus_signals` | FTS neighbor search, FMR scores, citation counts (read-only arxiv DB) |
| `save_prep_result` | Writes prep output JSON to v2 executions DB, sets status=prepped |

### Analysis actions
| Action | Description |
|---|---|
| `unpack_parallel_results` | Unpacks parallel machine output dict into context fields |
| `derive_terminology_tags` | Deterministic tag scoring from paper text, corpus, taxonomy |
| `prepend_frontmatter_v2` | Generates YAML frontmatter and prepends to report |
| `normalize_judge_decision` | Extracts PASS/REPAIR/FAIL from judge output |
| `set_repair_attempted` | Sets repair flag to prevent infinite repair loops |
| `save_analysis_result` | Writes report .md to disk, sets status=done in v2 DB |
| `mark_execution_failed` | Sets status=failed with error message in v2 DB |

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
