---
id: rpav-8sq6
status: closed
deps: []
links: [rpav-je6b, rpav-tpcr, rpav-mppm, rpav-0wkt, rpav-b3m1, rpav-fwbf, rpav-qqed, rpav-e50g, rpav-eew6, rpav-dxor]
created: 2026-06-22T06:12:12Z
type: epic
priority: 0
assignee: trent
tags: [architecture, infrastructure, pipeline]
---
# Epic: Research Paper Analysis V3 Architecture

Research Paper Analysis V3 — Complete Architecture Definition

This epic defines the full architecture for the research paper analysis v3 pipeline, including data preparation, extraction, and digest construction. It encompasses all infrastructure decisions from download strategy through final digest output.

## 1. OVERVIEW

The goal is to build a scalable, fault-tolerant pipeline that processes arXiv papers through three phases:
1. **Prep**: Download PDFs, convert with Docling, store as JSON in object storage
2. **Extraction**: Generate embeddings via local GPU (vert/localhost llama.cpp), store in object storage
3. **Digest**: LLM fan-out analysis, non-agentic assembly, judge, repair

The pipeline supports daily/weekly latest paper processing with rate-limited downloads and distributed checkpoint persistence.

## 2. DOWNLOAD LAYER — THREE-TIER STRATEGY

### Tier 1: Kaggle/GCS (fastest, largest batch)
- Requires `gsutil` CLI tool
- Downloads from GCS prefixes: gs://arxiv-dataset/arxiv/pdf/{month}/{arxiv_id}v1.pdf
- Fast for older papers that are already on Kaggle
- Fallback: if gsutil not found or fails, move to tier 2

### Tier 2: export.arxiv.org (most reliable for recent papers)
- URL: https://export.arxiv.org/pdf/{arxiv_id}
- httpx.AsyncClient with retries and exponential backoff
- Critical for daily/weekly latest papers not yet on Kaggle
- Rate limit handling: 429 → 60s, 300s, 900s, 3600s backoff
- Transient errors (503, 429): retry with exponential backoff
- Permanent errors (404): mark as failed, don't retry

### Tier 3: Standard arxiv API (last resort)
- URL: https://arxiv.org/pdf/{arxiv_id}
- Same retry logic as tier 2
- Used when export.arxiv.org fails

### Rate Limiting Policy Document
```yaml
# data/download_policies.yml — stored as flatmachine data object
sources:
  arxiv_export:
    rate_limit: 429
    backoff_schedule: [60, 300, 900, 3600]
    max_retries: 4
    queue_zero_on_429: true
    zero_queue_duration: 3600
    
  arxiv_api:
    rate_limit: 429
    backoff_schedule: [60, 300, 900, 3600]
    max_retries: 4
    queue_zero_on_429: true
    zero_queue_duration: 3600
    
  kaggle_gcs:
    rate_limit: none
    backoff_schedule: [5, 15, 45, 120]
    max_retries: 4
```

### Concurrency Limits
- Download concurrency: 5 (keep low to avoid arxiv throttling)
- Extraction concurrency: 60
- Corpus signals concurrency: 60

## 3. DOCING CONVERSION — ELT ON THE PDF

### Why Docling Over pypdf?
- Math rendering: LaTeX formulas preserved as markdown math blocks
- Table extraction: tables as markdown tables, not just text blobs
- Figure metadata: captions and descriptions extracted
- Better layout understanding: handles columns, footnotes, headers correctly

### Output Format Decision
Store the full DoclingDocument JSON (pydantic-based, stable schema). This is ELT — extract first, transform later in processing machine. If docling changes its output format, the adapter can handle it without re-extracting. The extraction is the bottleneck, not the transformation.

### Storage Layout
```
data/papers_docling_json/{arxiv_id}.json  # one file per paper
```

For S3 later:
```
s3://bucket/prep_batches/{batch_id}/{paper_ids}.json  # batched for efficiency
```

## 4. OBJECT STORE DECISION

### The "Zillion Tiny Objects" Problem
One object per paper in S3 is wasteful (S3 charges $0.023/1000 objects). For bulk non-agentic data, we should batch into large objects rather than one-per-record.

### Storage Design
- **Checkpoint metadata** (execution_id → paper_ids, status): small, one-per-execution object
- **PDF text / docling JSON**: batched into ~100MB objects: `s3://bucket/prep/{batch_id}/{paper_ids}.json` or `.parquet`
- **Individual PDFs**: if needed: `s3://bucket/pdfs/{year_month}/{arxiv_id}.pdf`

### Metadata Index
For local storage, use SQLite (already in codebase, connection pooling pattern). For S3 later, consider:
- Parquet files — one per batch, with paper_id as partition key
- Flatfile JSON — one file per paper, but just metadata (tiny)
- Dolt — if we need SQL queries

### Dolt vs Object Store
Dolt is overkill for bulk non-agentic data. We don't need versioned SQL — we need addressable objects with a simple index. Object storage + SQLite index is sufficient unless we need range queries or aggregations.

## 5. SINGLE QUEUE WITH STATE-BASED ROUTING

### Current Two-Queue Setup (Problem)
```
Queue 1: All papers (title, abstract, id) → FMR ranking → filter (fmr >= 0.60)
Queue 2: High-fmr papers → extraction (PDF → docling JSON) → digest
```

Problems: duplicate storage, two queue management systems, papers stuck in Queue 1 while waiting for extraction.

### Proposed Single Queue Setup
```
Queue: All papers (title, abstract, id, fmr_score, state)
State transitions: pending → ranked → extracting → extracted → digesting → done
```

### Queue Table (SQLite)
```sql
CREATE TABLE papers (
    arxiv_id TEXT PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    fmr_score REAL,
    state TEXT DEFAULT 'pending',  -- pending | ranked | extracting | extracted | digesting | done | failed
    docling_json_path TEXT,         -- path to the docling JSON file
    digest_path TEXT,               -- path to the final digest
    updated_at TIMESTAMP
);
```

### State-Based Routing Logic
```python
def route_paper(paper):
    if paper.state == 'pending':
        return 'rank_machine'      # Calculate FMR score
    elif paper.state == 'ranked' and paper.fmr_score >= 0.60:
        return 'extract_machine'  # Download PDF, convert with docling
    elif paper.state == 'extracted':
        return 'digest_machine'   # LLM fan-out, assemble digest
    else:
        return None               # Skip (fmr < 0.60 or failed)
```

### Priority by Progress
Papers closer to completion get priority. When allocating machines, prioritize started machines over creating new ones.

## 6. PAPER MACHINE AS ORCHESTRATOR

### Paper Machine Design
Each paper has its own "paper machine" that:
- Checks if current phase machine is complete
- If yes → queues next phase machine
- If no → goes back to queue (waits for resource)
- Only updates state (phase stored as state) and launches the next machine, then consumes the machine outcome

### Paper Machine YAML Structure
```yaml
# paper_machine.yml — orchestrates all phases for one paper
states:
  start:
    transitions: [check_phase]
    
  check_phase:
    action: check_current_phase_status
    transitions:
      - condition: context.phase == 'ranking' and context.ranking_complete
        to: launch_extraction

      - condition: context.phase == 'extraction' and context.extraction_complete
        to: launch_digest

      - to: wait_for_wakeup  # back to queue, waits for event

  launch_extraction:
    action: queue_submachine(extract_machine)
    transitions: [wait_for_extraction]

  wait_for_extraction:
    action: consume_extraction_result
    transitions: [check_phase]

  launch_digest:
    action: queue_submachine(digest_machine)
    transitions: [wait_for_digest]

  wait_for_digest:
    action: consume_digest_result
    transitions: [done]

  done:
    type: final
```

### Sub-Machines (Simple State Machines, One Per Phase)
Each sub-machine is just a state machine that does one thing. The extraction sub-machine writes the docling JSON directly to storage (not through parent paper machine).

#### Ranking Machine
```yaml
# ranking_machine.yml
states:
  start:
    transitions: [rank_paper]
    
  rank_paper:
    agent: ranking_agent
    input:
      title: context.title
      abstract: context.abstract
    output_to_context:
      fmr_score: output.content
    transitions: [mark_complete]
    
  mark_complete:
    action: send_event(
      event="submachine_complete",
      phase="ranking",
      paper_id=context.paper_id
    )
    transitions: [done]
    
  done:
    type: final
```

#### Extraction Machine
```yaml
# extraction_machine.yml
states:
  start:
    transitions: [download_pdf]
    
  download_pdf:
    action: download_pdf(context.paper_id)
    transitions: [convert_docling]
    
  convert_docling:
    action: convert_docling(
      paper_id=context.paper_id,
      output_path="data/papers_docling_json/{paper_id}.json"
    )
    transitions: [mark_complete]
    
  mark_complete:
    action: send_event(
      event="submachine_complete",
      phase="extraction",
      paper_id=context.paper_id
    )
    transitions: [done]
    
  done:
    type: final
```

#### Digest Machine (Fan-Out → Assemble → Judge)
```yaml
# digest_machine.yml
states:
  start:
    transitions: [fan_out_sections]
    
  fan_out_sections:
    machine:
      - narrative_lead_machine
      - method_results_machine
      - why_mechanism_machine
      - reproduction_machine
      - open_questions_machine
      - limits_confidence_machine
    mode: settled
    input:
      title: context.title
      paper_text: context.paper_text  # Read from docling JSON
    output_to_context:
      narrative_lead: output.narrative_lead_machine.content
      method_results: output.method_results_machine.content
      # ... etc
    transitions: [assemble_report]
    
  assemble_report:
    action: assemble_report(context)
    transitions: [judge_report]
    
  judge_report:
    agent: completeness_judge
    input:
      report_body: context.report_body
    output_to_context:
      judge_decision_raw: output.content
    transitions: [save_digest]
    
  save_digest:
    action: save_analyzer_result(context)
    transitions: [mark_complete]
    
  mark_complete:
    action: send_event(
      event="submachine_complete",
      phase="digest",
      paper_id=context.paper_id
    )
    transitions: [done]
    
  done:
    type: final
```

## 7. CONTROLLER MACHINE — STATELESS ORCHESTRATOR

### Controller Machine Design
The controller is a FlatMachine that continuously checks machine counts and launches sub-machines according to policy. It does not need snapshot/restore because it's stateless — just reads config from disk and launches machines based on that config.

```yaml
# controller_machine.yml
spec: flatmachine
spec_version: 4.1.0
data:
  name: sub-machine-controller
  persistence:
    enabled: false  # No snapshot/restore needed — stateless
  settings:
    max_steps: -1  # Run continuously
  context:
    machine_counts:
      ranking: 0
      extraction: 0
      digest: 0
    allocations:
      ranking: 40
      extraction: 35
      digest: 25
    policy: "furthest_along_first"  # Restart machines furthest along first

  states:
    start:
      transitions: [check_and_launch]

    check_and_launch:
      action: check_machine_counts
      transitions: [launch_batch]

    launch_batch:
      action: launch_submachines_in_batches
      transitions: [sleep]

    sleep:
      hooks: controller-wakeup
      action: sleep(1)  # Sleep 1 second between checks
      transitions: [check_and_launch]

    done:
      type: final
```

### Controller Launch Logic
```python
class SubMachineController:
    def __init__(self):
        self.allocation_counts = {
            'ranking': 40,
            'extraction': 35,
            'digest': 25,
        }
        self.running_counts = {}
        self.queues = {
            'ranking': Queue(),    # Papers waiting for ranking
            'extraction': Queue(), # Papers waiting for extraction
            'digest': Queue(),     # Papers waiting for digest
        }
        
    def check_machine_counts(self):
        # Get current machine counts from FlatMachines API
        self.running_counts = get_running_machines()
        
    def launch_submachines_in_batches(self):
        # Launch in batches: 5 ranking, 5 extraction, 3 digest per batch
        BATCH_SIZES = {'ranking': 5, 'extraction': 5, 'digest': 3}
        
        for phase in ['ranking', 'extraction', 'digest']:
            if self.running_counts.get(phase, 0) < self.allocation_counts[phase]:
                # Get papers from queue (furthest along first)
                papers = self.queues[phase].get_batch(BATCH_SIZES[phase])
                
                # Launch sub-machines for these papers
                for paper in papers:
                    launch_submachine(phase, paper)
```

### Allocation Tuning
The allocation is fixed and tuned based on machine constraints:
1. Number of parallel embeddings available (local GPU)
2. Number of parallel downloads available (arxiv export rate limits)
3. Number of parallel extractions available (docling conversion throughput)
4. Number of parallel digestions available (LLM concurrency)

Centralized configuration so it can be tuned without patching or redeploying individual machines.

## 8. EVENT ROUTING — FLATMACHINES SIGNAL SYSTEM

### What Exists (Fully Implemented)
- **signals.py**: Signal backends (MemorySignalBackend, SQLiteSignalBackend) store durable signals by named channel. Trigger backends (NoOpTrigger, FileTrigger, SocketTrigger) emit wake hints.
- **dispatcher.py**: SignalDispatcher reads signals, queries checkpoints for `waiting_channel` matches, and resumes machines via MachineResumer. Two modes: --once (drain all pending) and --listen (bind UDS via SOCK_DGRAM).
- **dispatch_signals.py**: CLI entrypoint (`python -m flatmachines.dispatch_signals`) — the actual systemd/launchd target process.

### Trigger Backends
- **FileTrigger**: Touches `/tmp/flatmachines/trigger` → OS watches the file (systemd PathChanged / launchd WatchPaths) → starts dispatcher
- **SocketTrigger**: Sends channel name as UDS datagram → dispatcher's `listen()` loop reads it and dispatches immediately

### What We Need to Build
- Systemd/launchd unit generation (currently external)
- Custom event routing for paper machine wakeup when sub-machine completes

## 9. PROCESS MANAGEMENT — SUB-MACHINE INVOKER STRATEGIES

### Three Invoker Strategies (in actions.py)
1. **InlineInvoker** (default): Sub-machines run as asyncio background tasks in the same event loop. No process isolation.
2. **SubprocessInvoker**: Spawns real OS processes via `subprocess.Popen` with `start_new_session=True` (detached). Uses `python -m flatmachines.run --config <temp_config.json> --input '{"..."}' --execution-id <id>` as the entrypoint (run.py).
3. **QueueInvoker**: Abstract base for cloud queues — enqueues work and polls ResultBackend for results.

### What's Missing
- No process health monitoring, no automatic restart on crash
- SubprocessInvoker is fire-and-forget with no lifecycle tracking of the spawned process

## 10. MACHINE LIFECYCLE AND PERSISTENCE

### What Exists
- Start: `FlatMachine.execute()` acquires a lock, loads checkpoint if resuming, enters state loop
- Stop: Lock released in finally: block; background tasks awaited
- SIGINT handling: `execute_sync()` installs a handler that calls `machine.cancel()` (cancels agent subprocesses) then cancels the main task
- Cancel: `FlatMachine.cancel()` propagates to all cached executors via their `.cancel()` method

### Comprehensive Checkpoint System (persistence.py)
- Three backends: MemoryBackend, LocalFileBackend (atomic writes via temp+rename), SQLiteCheckpointBackend (WAL mode, indexed columns for `waiting_channel`, `event`, `current_state`)
- Checkpoint events: machine_start, state_enter, execute, state_exit, machine_end, waiting, tool_call — each state transition is checkpointed
- Config store: Content-addressed config storage (ConfigStore protocol with MemoryConfigStore, LocalFileConfigStore, SQLiteConfigStore) — configs stored once by SHA-256 hash, referenced from checkpoints
- Resume: MachineSnapshot carries full context, step, state, metrics, waiting_channel, tool_loop_state, and config_hash. Resume reconstructs the machine from the config store using ConfigStoreResumer in resume.py

### What's Missing
- No automatic crash recovery — checkpoints exist but nothing reads them on process restart. The user must explicitly call `execute(resume_from=execution_id)`.
- No health checking or liveness probes.
- Restart logic exists only for launched sub-machines via the outbox pattern (`_resume_pending_launches` on resume) — if a child was checkpointed but not yet launched, it gets re-launched on parent resume.

## 11. SUB-MACHINE LAUNCHING — THREE PATTERNS

### Three Patterns (in flatmachine.py)
1. `_invoke_machine_single`: Blocking call to a single peer machine. Creates the child FlatMachine inline, shares persistence/lock/result_backend, awaits result.
2. `_invoke_machines_parallel`: Parallel execution of multiple machines via asyncio.gather. Two modes: "settled" (wait for all) and "any" (first to complete wins, rest become background tasks).
3. `_launch_fire_and_forget`: Fire-and-forget via the invoker's launch() method — writes result to ResultBackend after completion.

All sub-machine launches use the **outbox pattern** (LaunchIntent in backends.py): intent is checkpointed before launch, so if the parent crashes between checkpoint and launch, `_resume_pending_launches()` re-launches on resume.

### What's Missing
- Sub-machines always run in-process (via InlineInvoker) unless explicitly configured with SubprocessInvoker. No automatic process boundary decision.
- No queue-based sub-machine launching for distributed deployments (the QueueInvoker exists but is abstract — no concrete cloud implementation).

## 12. DISTRIBUTED WORKER PATTERN (REFERENCE)

### What Exists (distributed.py, distributed_hooks.py, work.py)
- RegistrationBackend (worker register/heartbeat/status)
- WorkPool (atomic push/claim/complete/fail with retry + poison queue)
- DistributedWorkerHooks (action handlers: get_pool_state, claim_job, complete_job, fail_job, register_worker, deregister_worker, heartbeat, list_stale_workers, reap_stale_workers, calculate_spawn, spawn_workers)
- Auto-scaling: Checker machine calculates workers needed, spawns via `_spawn_workers` which calls launch_machine() (subprocess spawn)

### ResultBackend (backends.py)
InMemoryResultBackend with asyncio Event-based blocking reads. URI scheme `flatagents://{execution_id}/result`. This is the IPC mechanism between parent and child machines.

### MachineInvoker Abstraction (actions.py)
Clean separation of how machines are invoked (inline, subprocess, queue). The framework doesn't assume a deployment model.

### What's Missing
- No built-in controller that continuously monitors and manages multiple running machines (the distributed worker pattern is user-land — you configure it via YAML states + hooks actions, not as a runtime primitive).
- No leader election or coordinator service.
- No cluster-wide state view across multiple hosts.

## 13. ARCHITECTURE DECISIONS — SINGLE PROCESS VS SEPARATE PROCESSES

### Single Process Model
All machines run in one process. If the process crashes, everything dies. No isolation.

### Separate Process Model (Recommended)
- **Controller machine**: runs in its own process (separate from sub-machines)
- **Paper machines**: run within the controller process (lightweight orchestrators, no separate process needed)
- **Sub-machines**: run in separate processes (fault isolation, independent restart)

### Controller Process Crash Recovery
If the controller process crashes:
1. Paper machines die — they're in-process
2. Sub-machines survive — they're in their own processes
3. Need to recover paper state from the database

Recovery process when controller restarts:
```python
def on_controller_restart():
    # Read all paper states from database
    papers = get_all_papers_from_db()
    
    # For each paper, check if its current sub-machine is still running
    for paper in papers:
        if paper.phase == 'ranking' and not is_submachine_running(paper):
            launch_submachine('ranking', paper)  # Restart crashed sub-machine
            
        elif paper.phase == 'extraction' and not is_submachine_running(paper):
            launch_submachine('extraction', paper)  # Restart crashed sub-machine
            
        elif paper.phase == 'digest' and not is_submachine_running(paper):
            launch_submachine('digest', paper)  # Restart crashed sub-machine
```

## 14. TICKET DEPENDENCIES

### rpav-tpcr: Add embeddings via local GPU (vert/localhost) running llama.cpp (P2)
- Extend existing llama.cpp serving at 192.168.1.21 to serve embeddings
- Add embedding model config to profiles.yml
- Create embedding generation utility (similar to KV cache warmup but for text embeddings)
- Store embeddings as .npy files keyed by arxiv_id + model name

### Closed (doesn't fit current architecture)
- ~~rpav-je6b: Add S3-compatible object store backend~~ — already have working SQLite backend with indexed columns, adding S3 would be like adding MongoDB
- ~~rpav-mppm: Store vector embeddings in object store~~ — depended on rpav-je6b

## 14. SERVICE LIFECYCLE — listener_os PATTERN (DECISION)

### Decision: Use FlatMachines listener_os Pattern for Paper Machine Activation

After investigating the FlatMachines SDK, we decided to use the **listener_os pattern** for paper machine service lifecycles instead of a custom supervisor process. This eliminates the need for process health monitoring and crash recovery logic because:

1. **The OS is the supervisor** — systemd/launchd watches for events and starts the dispatcher
2. **Paper machines park on `wait_for`** — durable checkpoints store state between phases
3. **Sub-machines signal via `send_and_notify`** — FileTrigger + dispatch-once resumes parked machines
4. **The dispatcher is idempotent** — drains all pending signals, resumes matching machines, exits

### How It Works
```
Paper Machine parks on wait_for → Sub-machine completes → send_and_notify → OS wakes dispatcher → Paper machine resumes
```

### Paper Machine YAML with `wait_for` (Final Design)
```yaml
# paper_machine.yml
states:
  start:
    transitions: [check_phase]
    
  check_phase:
    action: check_current_phase_status
    transitions:
      - condition: context.phase == 'ranking' and context.ranking_complete
        to: launch_extraction

      - condition: context.phase == 'extraction' and context.extraction_complete
        to: launch_digest

      - to: wait_for_wakeup  # Parks here until sub-machine completes

  wait_for_wakeup:
    wait_for: "paper/{{ context.paper_id }}"  # Per-paper signal channel
    timeout: 86400  # 24h max wait
    output_to_context:
      phase_complete: "{{ output.phase_complete }}"
      result_path: "{{ output.result_path }}"
    transitions:
      - condition: context.phase == 'ranking' and context.phase_complete
        to: launch_extraction

      - condition: context.phase == 'extraction' and context.phase_complete
        to: launch_digest

      - to: done  # Digest complete → final
```

### Sub-Machine Completion Hooks
```python
from flatmachines import send_and_notify, FileTrigger, SQLiteSignalBackend

signal_backend = SQLiteSignalBackend(db_path="data/papers.sqlite")
trigger_backend = FileTrigger(base_path="data/trigger")

# When extraction sub-machine finishes converting a paper:
signal_id = await send_and_notify(
    signal_backend=signal_backend,
    trigger_backend=trigger_backend,
    channel=f"paper/{arxiv_id}",  # Per-paper channel
    data={
        "phase_complete": True,
        "result_path": f"data/papers_docling_json/{arxiv_id}.json",
    },
)
```

### OS Activation (systemd/launchd)
- **Linux**: `~/.config/systemd/user/paper-dispatch.path` watches trigger file → starts `paper-dispatch.service` (oneshot) → runs `python -m flatmachines.dispatch_signals --once`
- **macOS**: `~/Library/LaunchAgents/dev.flatmachines.paper-dispatch.plist` with `WatchPaths` → same dispatch-once logic
- **Cron/timer**: systemd timer or launchd `StartCalendarInterval` for scheduled runs

### What This Solves
- **No custom supervisor needed** — the OS handles process activation
- **Crash recovery is automatic** — if dispatcher crashes, next trigger starts a new one; signals are durable in SQLite
- **Paper machines are durable** — parked on checkpoints, resume from any phase after crash
- **Event-driven or cron-driven** — same mechanism, different trigger source

## 15. DB SCHEMA — GENERATED COLUMNS ON CHECKPOINTS (DECISION)

### Decision: Use SQLite Generated Columns on FlatMachines Checkpoints for Paper Metadata

Investigated three options for querying paper metadata (fmr_score, docling_json_path, etc.) stored inside FlatMachines checkpoint blobs:

1. **Separate papers table** — rejected (duplicates data, out of sync risk, contradicts "no custom papers table" principle)
2. **Query JSON blobs directly with json_extract()** — works but no indexes = full table scans on every query
3. **Generated columns on machine_checkpoints** — chosen (best of both worlds)

### How It Works
- FlatMachines SDK creates `machine_checkpoints` and `machine_latest` tables automatically via `_ensure_schema()`
- We add **VIRTUAL generated columns** via `ALTER TABLE` at startup:
  ```sql
  ALTER TABLE machine_checkpoints
  ADD COLUMN fmr_score REAL
  GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.fmr_score')) VIRTUAL;
  ```
  **Note:** Must use VIRTUAL, not STORED — SQLite won't allow adding STORED columns to tables with existing rows, and the SDK writes checkpoints during execution before our migration runs.
- Generated columns are computed from the blob — single source of truth stays the checkpoint
- They auto-update whenever FlatMachines writes a new checkpoint
- We can create indexes on them for efficient queries: `CREATE INDEX idx_fmr ON machine_checkpoints(fmr_score)`

### Why This Is Better
- ✅ No schema conflict with FlatMachines SDK (we add columns, don't replace tables)
- ✅ Indexed queries on paper metadata without a separate table
- ✅ Zero data duplication — computed from the blob
- ✅ Auto-sync — FlatMachines writes the blob, columns update automatically
- ✅ `current_state` is already an indexed column in the base schema (SDK provides this)

### Example Query (efficient, uses indexes)
```sql
SELECT ml.execution_id AS paper_id, mc.fmr_score
FROM machine_latest ml
JOIN machine_checkpoints mc ON ml.latest_key = mc.checkpoint_key
WHERE mc.current_state = 'ranked'
  AND mc.fmr_score >= 0.6
```

### Rejected: v3_papers.sql as standalone schema
- Removed `schema/v3_papers.sql` — it duplicated tables the SDK already creates
- The migration script (generated columns) will live in the hooks init code instead

## 16. IMMEDIATE NEXT STEPS

1. Implement generated columns migration (new ticket)
2. Implement rpav-dxor (prep machine with download + Docling)
3. Implement rpav-usau (DB integration hooks)
4. Design the prep machine YAML with docling conversion
5. Design the controller machine YAML
6. Implement the main runner/scheduler (rpav-1xkn)

## Notes

**2026-06-22T06:12:31Z**

Epic created with full architecture definition including: three-tier download strategy, Docling conversion (ELT on PDF), object store design, single queue with state-based routing, paper machine as orchestrator, controller machine (stateless), event routing via FlatMachines signal system, process management strategies, and crash recovery patterns. Investigation of FlatMachines SDK completed — key findings: signal system fully implemented, no automatic crash recovery, SubprocessInvoker is fire-and-forget, outbox pattern for sub-machine launch durability.
