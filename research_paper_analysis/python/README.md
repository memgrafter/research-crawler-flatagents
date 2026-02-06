# Research Paper Analysis Pipeline

A **production-quality** machine topology demo with **2 peer machines**, **5 agents**, and a **self-judging improvement loop**.

Analyzes the full "Attention Is All You Need" paper (40KB, 15 pages) without truncation.

## Features Demonstrated

- **Machine Peering**: Main machine launches and coordinates 2 peer machines
- **Self-Judging Loop**: Summary refined until quality score ≥ 8/10
- **Multi-Agent Pipeline**: 5 specialized agents across machines
- **Automatic PDF Download**: Downloads from arXiv by ID or URL if not present
- **Formatted Output**: Saves markdown report to `data/analysis_report.md`

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                    MAIN: research-pipeline                          │
│                                                                      │
│   ┌─────────┐     ┌───────────────────┐     ┌──────────────────┐   │
│   │  start  │ ──▶ │     analyze       │ ──▶ │      refine      │   │
│   └─────────┘     │  (peer machine)   │     │  (peer machine)  │   │
│                   └───────────────────┘     └──────────────────┘   │
│                           │                         │              │
│                           ▼                         ▼              │
│                   ┌───────────────────┐     ┌──────────────────┐   │
│                   │ PEER: analyzer    │     │ PEER: refiner    │   │
│                   │                   │     │ (self-judging)   │   │
│                   │ ├─ abstract_      │     │                  │   │
│                   │ │  analyzer       │     │ ├─ synthesizer   │   │
│                   │ │                 │     │ │    ↓           │   │
│                   │ └─ section_       │     │ ├─ critic        │   │
│                   │    analyzer       │     │ │    ↓ (loop)    │   │
│                   └───────────────────┘     │ └─ until         │   │
│                                             │    quality ≥ 8   │   │
│                   ┌───────────────────┐     └──────────────────┘   │
│                   │     format        │ ◀──────────────────────────│
│                   │   (agent only)    │                            │
│                   └───────────────────┘                            │
│                           │                                        │
│                   ┌───────────────────┐                            │
│                   │       done        │                            │
│                   │  (save report)    │                            │
│                   └───────────────────┘                            │
└────────────────────────────────────────────────────────────────────┘
```

## Agents

| Agent | Role | Machine |
|-------|------|---------|
| `abstract_analyzer` | Extracts findings, methodology, contributions | analyzer |
| `section_analyzer` | Extracts technical details, results | analyzer |
| `synthesizer` | Creates/improves executive summary | refiner |
| `critic` | Judges quality, suggests improvements | refiner |
| `formatter` | Creates markdown report | main |

## Self-Judging Loop

The `refiner` peer machine implements a quality improvement loop:

1. **Synthesize**: Create summary (or improve based on critique)
2. **Critique**: Rate quality 1-10, identify weaknesses
3. **Decision**: 
   - If quality ≥ 8 → done
   - If iterations < 3 → loop back to synthesize
   - If iterations ≥ 3 → done (max attempts)

Typical runs: 2-3 iterations to reach quality threshold.

## Quick Start

```bash
cd python
export CEREBRAS_API_KEY="your-key"
./run.sh
```

`run.sh` uses `uv sync` to create the venv and install all dependencies in one step.
All deps are declared in `pyproject.toml`.

Analyze a specific arXiv paper by ID or URL:

```bash
./run.sh --arxiv 1706.03762
./run.sh --arxiv https://arxiv.org/abs/1706.03762
./run.sh --arxiv https://arxiv.org/pdf/1706.03762.pdf
./run.sh --arxiv https://arxiv.org/html/1706.03762
```

## Local Development (flatagents / flatmachines)

To develop against local checkouts of flatagents and flatmachines, edit
`pyproject.toml` — the `[tool.uv.sources]` block at the bottom controls this:

```toml
# Uncommented = local editable installs (default for dev)
[tool.uv.sources]
flatagents = { path = "../../../flatagents/sdk/python/flatagents", editable = true }
flatmachines = { path = "../../../flatagents/sdk/python/flatmachines", editable = true }
```

Then run `uv sync` (or just `./run.sh` — it calls `uv sync` for you).
Code changes in the local flatagents/flatmachines are reflected immediately
since they're installed as editable.

To switch back to PyPI releases, comment out the `[tool.uv.sources]` block
and run `uv sync` again.

No `--local` or `--upgrade` flags needed — `uv sync` is declarative and
always makes the venv match what `pyproject.toml` says.

Run the human-loop summarizer REPL (batch selection + approvals):

```bash
./run_summarizer_repl.sh --db ../arxiv_crawler/data/arxiv.sqlite --limit 10
```

Run the search REPL (FTS5 over title+abstract, then human-loop approvals; DB queue update only):

```bash
./run_search_repl.sh --db ../arxiv_crawler/data/arxiv.sqlite --limit 20 --query "retrieval augmented generation"
```

Queue the top matches without prompts:

```bash
./run_search_repl.sh --db ../arxiv_crawler/data/arxiv.sqlite --limit 20 --query "retrieval augmented generation" --auto-summarize
```

Use a term list file + impact ordering (top papers first):

```bash
./run_search_repl.sh --db ../arxiv_crawler/data/arxiv.sqlite --limit 30 --query-file queries/agents_autonomous_systems.txt --order-by impact --llm-relevant-only
```

Wrapper for Agents & Autonomous Systems search (defaults to impact ordering):

```bash
./run_agents_autonomous_search.sh
```

Notes:
- First run may build the FTS index (one-time). Use `--rebuild-fts` to force rebuild.
- The FTS query supports SQLite FTS5 syntax (phrases, OR, NEAR, etc.).
- `--query-file` loads newline-delimited terms and joins them with OR (combined with `--query` via AND).
- `--order-by impact` sorts by FMR score → max author h-index → citations (BM25 as tie-breaker).
- `--auto-summarize` skips prompts and queues all matches.
- `--show-count` prints total match count (slow on large DBs).
- Use `--llm-relevant-only` to restrict results to papers marked `llm_relevant = 1`.
- `run_search_repl.sh` only updates the DB queue; it does not spawn workers.

## Example Output

```
Title: Attention Is All You Need
Quality Score: 9/10
Citations Found: 40
Summary Preview: This paper addresses the limitations of dominant sequence 
transduction models...

📄 Report saved to: ../data/analysis_report.md

--- Statistics ---
Execution ID: 84e54fa3-6825-4729-a153-a59460282af0
Total API calls: 10
Estimated cost: $0.02
```

## Output Files

```
../data/
├── 1706.03762.pdf  # Paper (auto-downloaded)
├── 1706.03762.txt  # Extracted text (generated)
└── analysis_report.md             # Final formatted report
```

## API Call Budget

With self-judging loop, typically uses 8-12 API calls:
- Abstract analyzer: 1
- Section analyzer: 1  
- Synthesize/Critique loop: 4-8 (2-3 iterations × 2 agents)
- Formatter: 1

Budget of 25 calls allows for worst case of 3 full improvement iterations.

## Files

```
../config/
├── machine.yml              # Parent pipeline
├── analyzer_machine.yml     # Child: content analysis
├── refiner_machine.yml      # Child: self-judging loop
├── abstract_analyzer.yml    # Agent: abstract analysis
├── section_analyzer.yml     # Agent: section analysis
├── synthesizer.yml          # Agent: summary creation
├── critic.yml               # Agent: quality judgment
└── formatter.yml            # Agent: markdown formatting
```
