# V2 Persistence & Parallelism Implementation Plan

## Problem Statement

The current v2 architecture has three issues:

1. **Lost work on cancel.** `machine.yml` has no persistence. If the process dies
   at `write_why_hypotheses`, all prior work (key_outcome, corpus_signals) is lost.
   The paper eventually goes back to `pending` via the stale reaper, starting from
   scratch.

2. **The job layer is redundant.** `batch_scheduler.yml`, `paper_analysis_worker.yml`,
   `single_worker_launcher.yml`, and ~400 lines of `distributed_hooks.py`
   (register/deregister/claim/complete/fail/reap/spawn) exist because `machine.yml`
   doesn't checkpoint. Machine persistence makes all of it unnecessary.

3. **The pipeline is too sequential.** After `key_outcome` (cheap/fast), the expensive
   `why_hypotheses` blocks everything. `reproduction` doesn't depend on `why_hypotheses`
   and could run in parallel with it.

Additionally, v2 shares the arxiv_crawler's `paper_queue` and `worker_registry` SQLite
tables for read-write job tracking. This couples the two systems and prevents unwinding
v1 later.

## Design

### Core idea: machine execution IS the job

Enable `persistence: {enabled: true, backend: local}` on the analysis machine.
Each paper analysis becomes a persistent machine execution. The execution_id is the
job identity. Checkpoints on disk are the indicator of incomplete work. Resume picks
up where it left off. No separate job/worker/scheduler layer needed.

### Decouple from shared DB

| Access | Current | New |
|---|---|---|
| Paper metadata (papers, FTS, relevance, citations, authors) | Read from arxiv DB | **Same** — read-only, no contention |
| Job tracking (paper_queue, worker_registry) | Read-write on arxiv DB | **Own v2 SQLite** (`data/v2_executions.sqlite`) |
| Checkpoints | None | **Local file backend** (`data/checkpoints/`) |
| Reports | `data/*.md` | **Same** |

v2 never writes to the arxiv DB. A one-time seed step reads pending papers from
the arxiv DB and creates execution records in v2's own database.

### Own execution tracking schema

```sql
-- data/v2_executions.sqlite
CREATE TABLE executions (
    execution_id  TEXT PRIMARY KEY,
    arxiv_id      TEXT NOT NULL,
    paper_id      INTEGER NOT NULL,     -- references arxiv DB papers.id (not FK-enforced)
    title         TEXT NOT NULL,
    authors       TEXT NOT NULL DEFAULT '',
    abstract      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
        -- pending | running | done | failed
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    result_path   TEXT,                 -- path to output report .md
    error         TEXT
);
CREATE UNIQUE INDEX idx_executions_arxiv ON executions(arxiv_id);
```

The `UNIQUE(arxiv_id)` index prevents double-processing. The runner does:
1. Seed: query arxiv DB for eligible papers not yet in `executions`.
2. Resume: find `executions` with `status='running'` + checkpoint on disk.
3. Start new: claim `pending` rows up to concurrency limit.

### Pipeline restructure for parallelism

**Dependency graph (from agent input analysis):**

```
                        ┌→ why_hypotheses (expensive) ───────┐
                        │     needs: key_outcome, corpus     │
key_outcome (cheap) ────┤                                    │
  needs: paper text     │                                    ▼
                        └→ reproduction (expensive) ──→ limits_confidence (cheap)
                              needs: key_outcome        needs: key_outcome + why + repro
                                                               │
                                                               ▼
                                                        open_questions (expensive)
                                                          needs: key_outcome + limits
                                                               │
                                                               ▼
                                                        [terminology → assemble → judge → repair]
```

**Model profile mapping:**
- `prototype_structured` (cheap): key_outcome, limits_confidence, report_assembler, completeness_judge, targeted_repair
- `prototype_reasoning` (expensive): why_hypotheses, reproduction, open_questions

**New state flow:**

```
start
  → collect_corpus_signals          (action, no LLM)
  → prepare_paper                   (machine: download PDF, extract text)
  → write_key_outcome               (agent, cheap)
  → parallel_expensive_writers      (machine: [why_hypothesis_m, reproduction_m], expensive)
  → write_limits_confidence         (agent, cheap — has why + repro results)
  → write_open_questions            (agent, expensive)
  → derive_terminology_tags         (action, no LLM)
  → assemble_report                 (agent, cheap)
  → judge_report                    (agent, cheap)
  → normalize_judge_decision        (action)
    → PASS: prepend_frontmatter → done
    → REPAIR: targeted_repair → judge_report (once)
    → FAIL: failed_incomplete
```

The parallel step saves one serial expensive-model call. `why_hypotheses` starts
as soon as `key_outcome` finishes instead of waiting for `reproduction`.

### Concurrency control

The runner provides backpressure:

```python
sem = asyncio.Semaphore(max_concurrent)  # e.g., 3

async def run_one(execution):
    async with sem:
        machine = FlatMachine(config_file="config/machine.yml")
        if execution.has_checkpoint:
            await machine.resume(execution.snapshot)
        else:
            await machine.execute(input=execution.input)
```

**Policy: resume before start.** Incomplete executions get priority over new papers.
This ensures depth-first completion — 3 papers finish before the 4th starts,
instead of 2000 papers stuck at `parallel_expensive_writers`.

With `max_concurrent=3`, at most 3 papers are in-flight, which means at most
3 concurrent expensive-model calls in the parallel step (plus 3 more for
open_questions, staggered). This matches the old `--workers 3` behavior.

### prepare_paper as a machine

`prepare_paper` (PDF download + text extraction) becomes its own machine so it
gets independent checkpointing and retry. If PDF download fails, only that step
retries — not the whole pipeline.

```yaml
# prepare_paper_machine.yml
states:
  start:
    type: initial
    transitions: [{to: download}]
  download:
    action: download_pdf
    on_error: failed
    transitions: [{to: extract}]
  extract:
    action: extract_text
    on_error: failed
    transitions: [{to: done}]
  done:
    type: final
    output:
      section_text: context.section_text
      reference_count: context.reference_count
  failed:
    type: final
    output:
      error: context.error
```

## Dependency Graph (tasks)

```
[1] Create v2 executions DB schema + migration
 │
 ├──[2] Write seed script (arxiv DB → v2 executions)
 │
 ├──[3] Create prepare_paper_machine.yml + hooks
 │   │
 │   └──[4] Create wrapper machines for parallel step
 │       ├── why_hypothesis_machine.yml
 │       └── reproduction_machine.yml
 │           │
 │           └──[5] Restructure machine.yml
 │                   - add persistence config
 │                   - add prepare_paper machine invocation
 │                   - add parallel_expensive_writers state
 │                   - reorder states per dependency graph
 │
 ├──[6] Write new runner (run.py)
 │       - seed from arxiv DB
 │       - resume incomplete
 │       - start new up to concurrency limit
 │       - daemon mode with poll loop
 │
 └──[7] Update hooks.py
         - extract download_pdf / extract_text actions from distributed_hooks
         - keep corpus_signals, terminology, frontmatter, judge actions
         - add save_result / mark_failed actions (write to v2 executions DB)

[8] Integration test: run 2–3 papers end-to-end
    - verify checkpoint/resume (kill mid-pipeline, restart)
    - verify parallel_expensive_writers produces both outputs
    - verify report quality unchanged

[9] Cleanup
    - delete batch_scheduler.yml
    - delete paper_analysis_worker.yml
    - delete single_worker_launcher.yml
    - delete run_batch.py
    - delete distributed_hooks.py
    - delete run_single_worker.py (replaced by run.py)
    - update README.md
```

## Task List

### Phase 1: Own DB + decouple (no behavior change yet)

- [x] **1.1** Create `data/v2_executions.sqlite` schema
  - `executions` table with execution_id, arxiv_id, paper_id, title, authors,
    abstract, status, created_at, updated_at, result_path, error
  - Migration script or auto-create in runner

- [x] **1.2** Write `seed.py` (or seed function in runner)
  - Read-only query against arxiv DB: eligible papers not yet in v2 executions
  - Insert `pending` rows into v2 executions DB
  - Criteria: `papers` joined with `paper_queue WHERE status='pending'`,
    latest version only, `NOT EXISTS` in v2 executions
  - This is the **only** point that reads from arxiv paper_queue

### Phase 2: Restructure pipeline

- [x] **2.1** Create `config/prepare_paper_machine.yml`
  - States: download_pdf → extract_text → done | failed
  - Move PDF download + text extraction logic from `distributed_hooks._prepare_paper`
    into `hooks.py` as `download_pdf` and `extract_text` actions

- [x] **2.2** Create `config/why_hypothesis_machine.yml`
  - Single-agent wrapper machine (~15 lines)
  - Agent: why_hypothesis_writer with retry backoffs [2, 8, 16]
  - Input: title, key_outcome, abstract, section_text, corpus_signals,
    corpus_neighbors, token_target
  - Output: `{content: context.result}`

- [x] **2.3** Create `config/reproduction_machine.yml`
  - Same pattern as 2.2 for reproduction_writer
  - Input: title, key_outcome, abstract, section_text, reference_count, token_target
  - Output: `{content: context.result}`

- [x] **2.4** Restructure `config/machine.yml`
  - Add `persistence: {enabled: true, backend: local, checkpoint_on: [machine_start, execute, machine_end]}`
  - Add `machines:` block referencing prepare_paper, why_hypothesis, reproduction
  - Replace `write_why_hypotheses` + `write_reproduction` states with single
    `parallel_expensive_writers` state using `machine: [why_hypothesis_machine, reproduction_machine]`
  - Add `prepare_paper` machine invocation state (before `write_key_outcome`)
  - Remove `section_text` / `reference_count` from initial context (comes from prepare_paper)
  - Keep all other states (limits_confidence, open_questions, terminology, assemble,
    judge, repair) as-is — they're already agents with retry

- [x] **2.5** Update `hooks.py`
  - Add `download_pdf` action (extracted from distributed_hooks._prepare_paper)
  - Add `extract_text` action (extracted from distributed_hooks._prepare_paper)
  - Add `save_execution_result` action — writes result_path + status='done' to v2 DB
  - Add `mark_execution_failed` action — writes error + status='failed' to v2 DB
  - Corpus signals hooks keep read-only access to arxiv DB (unchanged)
  - All v2 DB writes use a separate connection to `data/v2_executions.sqlite`

### Phase 3: New runner

- [x] **3.1** Write `run.py`
  - `seed()` — call seed function (phase 1.2)
  - `resume_incomplete()` — find executions with status='running' + checkpoint
  - `claim_pending(limit)` — atomic UPDATE on v2 executions: pending → running
  - `run(max_concurrent, poll_interval)` — semaphore + resume-before-start policy
  - CLI: `python run.py --workers 3 --daemon --poll-interval 5`
  - Single file, no separate scheduler/launcher scripts

- [x] **3.2** Daemon mode
  - Poll loop: seed → resume → start new → sleep → repeat
  - Exit when: no pending, no running, no incomplete checkpoints

### Phase 4: Validate + cleanup

- [x] **4.1** Integration test: 2–3 papers end-to-end
  - Confirm reports match v2 quality (same agents, same prompts)
  - Kill process mid-`parallel_expensive_writers`, restart, verify resume
  - Verify `v2_executions.sqlite` tracks status correctly
  - Verify no writes to arxiv DB `paper_queue` or `worker_registry`

- [x] **4.2** Delete dead code
  - `config/batch_scheduler.yml`
  - `config/paper_analysis_worker.yml`
  - `config/single_worker_launcher.yml`
  - `src/research_paper_analysis_v2/distributed_hooks.py`
  - `run_batch.py`
  - `run_single_worker.py`

- [x] **4.3** Update `README.md`
  - New usage: `python run.py --workers 3`
  - Document v2 executions DB schema
  - Document checkpoint/resume behavior
  - Remove references to batch scheduler, worker launcher

## Files changed/created

| Action | File |
|---|---|
| **Create** | `config/prepare_paper_machine.yml` |
| **Create** | `config/why_hypothesis_machine.yml` |
| **Create** | `config/reproduction_machine.yml` |
| **Create** | `run.py` |
| **Create** | `schema/v2_executions.sql` |
| **Modify** | `config/machine.yml` — persistence, parallelism, prepare_paper |
| **Modify** | `src/.../hooks.py` — add download/extract/save/fail actions, v2 DB connection |
| **Delete** | `config/batch_scheduler.yml` |
| **Delete** | `config/paper_analysis_worker.yml` |
| **Delete** | `config/single_worker_launcher.yml` |
| **Delete** | `src/.../distributed_hooks.py` |
| **Delete** | `run_batch.py` |
| **Delete** | `run_single_worker.py` |

## DB access after migration

| Database | Access | Purpose |
|---|---|---|
| `arxiv_crawler/data/arxiv.sqlite` | **Read-only** | Seed eligible papers, corpus signals (FTS, relevance, citations, authors) |
| `data/v2_executions.sqlite` | **Read-write** (v2 only) | Execution tracking: pending → running → done/failed |
| `data/checkpoints/` | **Read-write** (v2 only) | Machine persistence backend |

No writes to `paper_queue`. No writes to `worker_registry`. v1 can be unwound
independently.

## SDK verification results

All checked against flatmachines SDK installed in v2's .venv.

### ✅ Persistence: works as needed

- `LocalFileBackend(base_dir=".checkpoints")` — atomic writes via temp+rename.
  Keys are `{execution_id}/step_{step:06d}_{event}.json`. Latest pointer at
  `{execution_id}/latest`.
- `CheckpointManager(backend, execution_id)` — `save_checkpoint(snapshot)` and
  `load_latest() -> MachineSnapshot`.
- `FlatMachine.__init__` reads `persistence:` from YAML config. `backend: local`
  creates `LocalFileBackend()`, `backend: memory` creates `MemoryBackend()`.
  `checkpoint_on` defaults to all events: `[machine_start, state_enter, execute,
  state_exit, machine_end]`.
- **Checkpoint directory defaults to `.checkpoints`**. We'll override to
  `data/checkpoints` via constructor: `LocalFileBackend(base_dir="data/checkpoints")`.

### ✅ Resume: works via `resume_from` parameter

- `FlatMachine.execute(resume_from=execution_id)` — sets execution_id, loads
  latest snapshot from persistence backend, restores context/step/state/metrics,
  resumes pending launches (outbox pattern). If snapshot event is `machine_end`,
  returns immediately (already complete).
- **No separate `resume()` method** — it's a parameter on `execute()`.
- **Execution_id is controllable**: `FlatMachine(..., _execution_id=...)` or just
  use `resume_from`.

### ✅ Parallel machine invocation: works as expected

- `machine: [a, b]` handled in `_execute_state`. Calls
  `_invoke_machines_parallel(machines, input_data, mode, timeout)`.
- Each child gets a UUID execution_id. All launched concurrently via
  `asyncio.create_task`. `mode: settled` waits for all.
- **Output structure**: `Dict[str, Any]` keyed by machine name. So for
  `machine: [why_hypothesis_machine, reproduction_machine]`, output is:
  ```python
  {
    "why_hypothesis_machine": {"content": "..."},
    "reproduction_machine": {"content": "..."}
  }
  ```
  In `output_to_context`, access via `output.why_hypothesis_machine.content`.
- Errors per-child: `{"_error": "...", "_error_type": "..."}`.

### ✅ Locking: automatic with local persistence

- `backend: local` automatically sets `LocalFileLock()`. Two runners can't execute
  the same execution_id concurrently. `NoOpLock` for memory backend.

### ✅ Rate limit info: available but not surfaced to hooks

- `AgentResult.rate_limit` carries `RateLimitState` (remaining_requests,
  remaining_tokens, etc.) from provider headers.
- `extract_rate_limit_info(headers)` parses OpenAI-style headers (which
  OpenRouter also returns).
- **However**: the execution loop calls `_accumulate_agent_metrics(result)` which
  tracks cost/usage but does NOT expose rate_limit to hooks or context. Rate limit
  info is available in the AgentResult but silently dropped.
- **Implication for quota tracking**: we'll need to track request counts ourselves
  in the runner (increment a counter per agent call) rather than relying on
  provider headers. This is fine for a daily budget — just count calls.

### ⚠️ LocalFileBackend has no `list()` method

- The spec defines `list(prefix)` on PersistenceBackend, but the SDK's
  `LocalFileBackend` only implements `save`, `load`, `delete`.
- **Workaround**: scan the checkpoint directory directly with `pathlib.glob`.
  Checkpoint layout is `{base_dir}/{execution_id}/latest`, so:
  ```python
  for latest_file in Path("data/checkpoints").glob("*/latest"):
      execution_id = latest_file.parent.name
  ```
  This is reliable because the SDK writes a `latest` pointer for every execution.

### ⚠️ Peer machines don't inherit parent's persistence backend

- `_launch_and_write` creates a new `FlatMachine(config_dict=target_config, ...)`.
  It passes `result_backend` and `agent_registry` but NOT `persistence`.
- **Implication**: the why_hypothesis_machine and reproduction_machine wrapper
  machines will use whatever persistence their own config specifies. If we want
  them to checkpoint too, they need their own `persistence:` config in YAML.
  For the initial implementation, these are fast single-agent machines — memory
  persistence is fine. Only the outer analysis_machine needs durable checkpoints.

## Two-machine split for quota optimization

### Motivation

The cheap and expensive models share a 1000 req/day OpenRouter quota. The expensive
model (pony-alpha) has intermittent availability. To maximize expensive-model
utilization:

1. Pre-run cheap work (prep + key_outcome) for many papers during off-peak
2. When expensive model becomes available, immediately start analysis — no waiting
   for cheap prep steps
3. At end of day, use remaining quota to prep more papers for tomorrow

### Architecture

```
prep_machine.yml                    analysis_machine.yml
─────────────────                   ──────────────────────
prepare_paper (action, 0 req)       parallel_expensive_writers
  ↓                                   why_hypothesis (1 req, expensive)
collect_corpus_signals (action, 0)    reproduction (1 req, expensive)
  ↓                                     ↓
write_key_outcome (agent, 1 req)    write_limits_confidence (1 req, cheap)
  ↓                                     ↓
done                                write_open_questions (1 req, expensive)
                                        ↓
Cost: 1 cheap req/paper             derive_terminology_tags (action, 0)
                                        ↓
                                    assemble_report (1 req, cheap)
                                        ↓
                                    judge_report (1 req, cheap)
                                        ↓
                                    [repair loop: 0-2 req, cheap]
                                        ↓
                                    done

                                    Cost: 3 expensive + 3-5 cheap = 6-8 req/paper
```

### Execution status flow

```
pending → prepping → prepped → analyzing → done
                                         → failed
```

### Runner budget logic

```
daily_budget = 1000
used_today = count from v2 executions DB (calls since midnight)
remaining = daily_budget - used_today

buffer_depth = count(status='prepped')
min_buffer = 20  # always keep this many prepped

# Decision logic:
if expensive_model_available():
    # Use budget for analysis (6-8 reqs/paper)
    run analysis_machine on prepped papers
elif buffer_depth < min_buffer:
    # Prep more (1 req/paper, cheap model always available)
    run prep_machine on pending papers
elif approaching_end_of_day() and remaining > 0:
    # Burn remaining budget on prep for tomorrow
    run prep_machine on pending papers
else:
    # Wait and poll for expensive model
    sleep(poll_interval)
```

### Quota counter

Since rate_limit headers aren't surfaced to hooks, the runner maintains a simple
counter in the v2 executions DB:

```sql
CREATE TABLE daily_usage (
    date TEXT PRIMARY KEY,      -- YYYY-MM-DD
    total_calls INTEGER NOT NULL DEFAULT 0,
    cheap_calls INTEGER NOT NULL DEFAULT 0,
    expensive_calls INTEGER NOT NULL DEFAULT 0
);
```

The `on_state_exit` hook increments the counter after each agent call. The runner
reads it before deciding what to run.

## Revised task list

### Phase 1: Own DB + decouple (no behavior change yet)

- [x] **1.1** Create v2 executions DB schema (`data/v2_executions.sqlite`)
  - `executions` table: execution_id, arxiv_id, paper_id, title, authors,
    abstract, status (pending/prepping/prepped/analyzing/done/failed),
    created_at, updated_at, prep_output (JSON), result_path, error
  - `daily_usage` table: date, total_calls, cheap_calls, expensive_calls
  - Auto-create on first run

- [x] **1.2** Write seed function
  - Read-only query against arxiv DB: eligible papers not yet in v2 executions
  - Insert `pending` rows into v2 executions DB
  - This is the **only** point that reads from arxiv `paper_queue`

### Phase 2: Prep machine

- [x] **2.1** Extract `download_pdf` and `extract_text` actions into `hooks.py`
  - Move from `distributed_hooks._prepare_paper`
  - Keep in hooks.py alongside existing corpus_signals, terminology, frontmatter

- [x] **2.2** Create `config/prep_machine.yml`
  - States: prepare_paper (download + extract) → collect_corpus_signals →
    write_key_outcome → done
  - `persistence: {enabled: true, backend: local}`
  - Output: section_text, reference_count, key_outcome, corpus_signals,
    corpus_neighbors

- [x] **2.3** Add `save_prep_result` action to hooks
  - Writes prep output to `executions.prep_output` (JSON blob)
  - Updates status: prepping → prepped

### Phase 3: Analysis machine

- [x] **3.1** Create `config/why_hypothesis_machine.yml` (wrapper, ~15 lines)
- [x] **3.2** Create `config/reproduction_machine.yml` (wrapper, ~15 lines)

- [x] **3.3** Create `config/analysis_machine.yml`
  - `persistence: {enabled: true, backend: local}`
  - States: parallel_expensive_writers → write_limits_confidence →
    write_open_questions → derive_terminology_tags → assemble_report →
    judge_report → normalize → repair/done
  - Input: all prep_output fields passed in by runner

- [x] **3.4** Add `save_analysis_result` and `mark_execution_failed` actions
  - Write result_path + status='done' to v2 executions DB
  - Write error + status='failed' on failure

### Phase 4: Runner

- [x] **4.1** Write `run.py`
  - `seed()` — read arxiv DB, create pending rows
  - `probe_expensive_model()` — lightweight check (tiny prompt or last-known state)
  - `run_prep(budget)` — claim pending → prepping, run prep_machine, mark prepped
  - `run_analysis(budget)` — claim prepped → analyzing, run analysis_machine, mark done
  - Budget tracking: increment `daily_usage` after each machine completes
  - Resume-before-start policy for both phases

- [x] **4.2** Runner CLI + daemon mode
  - `python run.py --workers 5 --daemon --poll-interval 10`
  - Poll loop: check budget → check expensive availability → decide prep vs analysis → run → sleep
  - Exit when: no pending, no prepped, no incomplete, or budget exhausted

### Phase 5: Validate + cleanup

- [x] **5.1** Integration test: 2–3 papers end-to-end
  - Run prep for 3 papers, verify prepped status + prep_output in DB
  - Run analysis for prepped papers, verify reports match current quality
  - Kill process mid-analysis, restart, verify resume
  - Verify zero writes to arxiv DB

- [x] **5.2** Delete dead code
  - `config/batch_scheduler.yml`
  - `config/paper_analysis_worker.yml`
  - `config/single_worker_launcher.yml`
  - `src/.../distributed_hooks.py`
  - `run_batch.py`, `run_single_worker.py`

- [x] **5.3** Update README.md, delete obsolete docs
  - New usage: `python run.py --workers 5 --daemon`
  - Document two-machine architecture, budget logic, resume behavior

## Files changed/created (revised)

| Action | File |
|---|---|
| **Create** | `config/prep_machine.yml` |
| **Create** | `config/analysis_machine.yml` |
| **Create** | `config/why_hypothesis_machine.yml` |
| **Create** | `config/reproduction_machine.yml` |
| **Create** | `run.py` |
| **Create** | `schema/v2_executions.sql` |
| **Modify** | `src/.../hooks.py` — add download/extract/save actions, v2 DB connection, daily_usage counter |
| **Delete** | `config/machine.yml` (replaced by prep + analysis machines) |
| **Delete** | `config/batch_scheduler.yml` |
| **Delete** | `config/paper_analysis_worker.yml` |
| **Delete** | `config/single_worker_launcher.yml` |
| **Delete** | `src/.../distributed_hooks.py` |
| **Delete** | `run_batch.py`, `run_single_worker.py` |

## DB access after migration

| Database | Access | Purpose |
|---|---|---|
| `arxiv_crawler/data/arxiv.sqlite` | **Read-only** | Seed eligible papers, corpus signals (FTS, relevance, citations, authors) |
| `data/v2_executions.sqlite` | **Read-write** (v2 only) | Execution tracking + daily usage counter |
| `data/checkpoints/` | **Read-write** (v2 only) | Machine persistence (prep + analysis) |

No writes to `paper_queue`. No writes to `worker_registry`. v1 can be unwound
independently.

## Risk notes

- **Parallel machine output mapping**: verified. Output is `Dict[str, Any]` keyed
  by machine name. `output.why_hypothesis_machine.content` is the correct path.

- **Checkpoint size**: `section_text` can be large. Checkpoints include full context.
  Monitor file sizes. If problematic, store text on disk and reference by path.

- **Corpus signals DB contention**: arxiv DB opened read-only by v2, read-write by
  crawler. WAL mode handles this.

- **Seed idempotency**: `UNIQUE(arxiv_id)` + `INSERT OR IGNORE`.

- **No `list()` on LocalFileBackend**: scan `data/checkpoints/*/latest` with glob.

- **Peer machine persistence**: wrapper machines (why_hypothesis, reproduction) use
  memory persistence by default. Only prep_machine and analysis_machine need durable
  checkpoints.

- **Expensive model probe cost**: the availability probe itself consumes a request.
  Use the smallest possible prompt, or track last-known availability + time-based
  heuristic to reduce probe frequency.
