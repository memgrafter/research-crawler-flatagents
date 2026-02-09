# Design and Implementation Plan: Decentralized Autoscaling

## Inputs Reviewed
- `DECENTRALIZED_AUTOSCALING_PLAN.md` (initial architecture: self-registering workers + registry + scaler)
- `~/code/flatagents/MACHINES.md` (FlatMachine hooks, looping, launch, transitions, error handling)

---

## Goals
- Add a **decentralized autoscaling** workflow for FlatMachine workers that can grow/shrink without a central orchestrator.
- Reuse the existing **paper analysis pipeline** as the initial workload (e.g., `analyzer_machine.yml`/`refiner_machine.yml`), but keep the worker machine configurable so other task types can be plugged in later.
- Provide a small **registry + scaler** utility for runtime control.
- Keep changes **minimal** and consistent with current repo patterns (SQLite, FlatMachine hooks, YAML configs).

## Non-Goals
- Building a full production scheduler (no Kubernetes, no distributed lock service).
- Adding a new LLM pipeline; we reuse existing machines as the initial workload example.
- Building a UI or long-running daemon for scaling.

---

## Architecture Overview

### Components
1. **Work Queue**
   - Use a queue table for work items (this repo can map to `paper_queue` in `arxiv_crawler/data/arxiv.sqlite`).
   - Workers claim tasks by setting `status=running`, `started_at`, and `worker` (their worker_id).

2. **Worker Registry**
   - A new SQLite table in the same DB (`worker_registry`).
   - Tracks worker lifecycle: `active` → `terminating` → `terminated` (or removed).
   - Includes heartbeat timestamps to detect stale workers.

3. **Long-Running Worker FlatMachine**
   - A new FlatMachine YAML (e.g., `research_paper_analysis/config/autoscaling_worker.yml`).
   - Uses `action` hooks for DB operations + a dedicated worker machine (`paper_analysis_worker.yml` in this repo) to process each work item.

4. **Scaler CLI**
   - A lightweight Python script (e.g., `research_paper_analysis/python/src/research_paper_analysis/scaler.py`).
   - Reads `--target-workers`, scales up by launching new worker processes, scales down by setting `status=terminating`.

---

## Registration Backend (FlatAgents Runtime Spec Extension)

To keep autoscaling portable across SDKs and future distributed deployments, formalize worker registration as a runtime backend in `~/code/flatagents/flatagents-runtime.d.ts`.

### Proposed Runtime Interfaces (planning only)
- `RegistrationBackend` interface (SDK contract):
  - `register(worker: WorkerRegistration): Promise<WorkerRecord>`
  - `heartbeat(worker_id: string, status?: WorkerStatus, metadata?: Record<string, any>): Promise<void>`
  - `updateStatus(worker_id: string, status: WorkerStatus): Promise<void>`
  - `get(worker_id: string): Promise<WorkerRecord | null>`
  - `list(filter?: WorkerFilter): Promise<WorkerRecord[]>`
  - Optional: lease-based semantics (`lease_expires_at` or `renewLease`) to standardize stale detection.

- Types:
  - `WorkerStatus = "active" | "terminating" | "terminated" | "lost"`
  - `WorkerRegistration` (host, pid, metadata, capabilities/tags, started_at)
  - `WorkerRecord` (registration + `last_heartbeat`, `current_task_id`)
  - `WorkerFilter` (status, host, capability tags, freshness cutoff)

### Behavioral Requirements (SDK Contract)
- Registration must be idempotent for the same `worker_id`.
- `heartbeat` must refresh `last_heartbeat` and may update status/metadata.
- `updateStatus` must persist the lifecycle state for subsequent reads.
- `list(filter)` must support `status` filtering; other filter fields are optional.
- Stale detection must be possible using `last_heartbeat` and/or an optional lease field (algorithm is backend-defined).

### SDK Configuration (Contract Only)
- Extend `BackendConfig` with a `registration` selector (exact schema TBD).
- Extend `SDKRuntimeWrapper` with `registration_backend?: RegistrationBackend`.
- SDKs must ship at least one local backend suitable for single-process usage; additional backends are implementation-defined.

### Implications for This Repo
- The `worker_registry` SQLite table becomes the `SQLiteRegistrationBackend` implementation.
- Autoscaling hooks should depend on the registration backend interface, not raw SQL, to align with SDK contracts.
- The scaler will read `RegistrationBackend.list()` so alternate backends can plug in without changing logic.

### Value and Distributed-Scale Implications
- **Interoperability**: a single registry contract across SDKs enables mixed Python/TS/Rust workers.
- **Heterogeneous scheduling**: capability metadata (GPU/CPU, model access, region) supports targeted routing.
- **Fault tolerance**: heartbeat/lease semantics make stale detection consistent across backends.
- **Scale-up path**: the same interface lets deployments move from local to distributed registries without changing worker logic.

---

## Data Model

### `worker_registry` (new table)
Suggested schema (SQLite):
- `worker_id TEXT PRIMARY KEY`
- `status TEXT NOT NULL` (`active`, `terminating`, `terminated`, `lost`)
- `started_at TEXT NOT NULL`
- `last_heartbeat TEXT NOT NULL`
- `current_task_id INTEGER` (optional: `work_queue.id`, `paper_queue.id` in this repo)
- `pid INTEGER` (optional)
- `host TEXT` (optional)
- `metadata_json TEXT` (optional)

This table is the **SQLite RegistrationBackend** implementation described above.

### `work_queue` (existing or to add)
- Use `status` transitions: `pending` → `running` → `done`/`failed`.
- Populate `worker` with `worker_id` and result/error fields appropriate for the workload.
- In this repo, the concrete table is `paper_queue` in `arxiv_crawler/data/arxiv.sqlite` (with `summary_path`/`error`).

---

## Worker Machine Design (FlatMachine)

### Key FlatMachine Features Used
- `on_machine_start` / `on_machine_end` hooks for registration and cleanup.
- `action` states for queue and registry operations.
- Looping transitions for continuous operation.
- `timeout` + `sleep` action to avoid busy-waiting.

### Proposed State Flow
```
start (initial)
  → register_worker (action)
  → get_work (action)
     ↳ if no work: wait
     ↳ if work: process_work
  → process_work (machine: worker_task_machine.yml (paper_analysis_worker.yml in this repo))
  → record_result (action)
  → heartbeat_and_check (action)
     ↳ if status=terminating: shutdown
     ↳ else: get_work

wait
  → heartbeat_and_check

shutdown (final)
```

### Context/Inputs
- `db_path`: path to the queue/registry DB (default to `arxiv_crawler/data/arxiv.sqlite` in this repo).
- `worker_id`: generated on startup if not provided.
- `heartbeat_interval_seconds`, `idle_wait_seconds`.

### Hook Actions
- `register_worker`
- `dequeue_work` (claim next `work_queue` row, `paper_queue` in this repo)
- `mark_task_started` / `mark_task_finished`
- `update_heartbeat`
- `sleep`
- `set_terminating` (used by scaler)
- `cleanup_worker`

---

## Scaler Utility Design

### Inputs
- `--target-workers <N>` (required)
- `--db-path` (optional)
- `--max-launch-per-cycle` (optional, for gradual scale up)
- `--dry-run` (optional)

### Logic
1. Query `worker_registry` for `status=active` and `status=terminating`.
2. Treat workers with stale heartbeats (e.g., > 2× heartbeat interval) as `lost`.
3. **Scale Up**: If `active < target`, launch new worker processes.
4. **Scale Down**: If `active > target`, set `status=terminating` on surplus workers.

### Launch Strategy
- Spawn new workers using `subprocess.Popen`:
  ```bash
  python -m research_paper_analysis.worker --db-path ...
  ```
- Workers register themselves on startup (no pre-provisioning required).

---

## Implementation Plan (Phased)

### Phase 0: FlatAgents Runtime Spec Update (Planning Only)
1. In `~/code/flatagents/flatagents-runtime.d.ts`, add the `RegistrationBackend` interface + `Worker*` types.
2. Extend `BackendConfig` and `SDKRuntimeWrapper` with `registration`/`registration_backend` entries.
3. Document baseline semantics: idempotent registration, heartbeat/lease expiry, capability metadata fields, and stale detection guidance.

### Phase 1: Data Layer + Hooks
1. Add a migration in `arxiv_crawler/schema.sql` for `worker_registry`.
2. Implement a local `SQLiteRegistrationBackend` (or equivalent helper) that satisfies the planned `RegistrationBackend` interface.
3. Add a new hook class, e.g. `research_paper_analysis/autoscaling_hooks.py`, that implements:
   - `on_machine_start`: register worker + set `status=active`.
   - `on_machine_end`: mark worker `terminated` and clear `current_task_id`.
   - `on_action`: queue and heartbeat actions.
4. Ensure DB helpers are consistent with existing SQLite patterns.

### Phase 2: Worker FlatMachine
1. Add `research_paper_analysis/config/autoscaling_worker.yml`.
2. Add a workload wrapper machine (e.g., `paper_analysis_worker.yml` in this repo) that invokes the existing analyzer pipeline config(s).
3. Wire in actions and loop transitions (as described above).

### Phase 3: Worker Runner
1. Add `research_paper_analysis/python/src/research_paper_analysis/worker.py`:
   - Loads `autoscaling_worker.yml`.
   - Accepts `--db-path`, `--worker-id` (optional), `--idle-wait-seconds`.
   - Runs `FlatMachine.execute()` in a long-running loop.

### Phase 4: Scaler CLI
1. Add `research_paper_analysis/python/src/research_paper_analysis/scaler.py`:
   - Implements scale-up/down logic.
   - Spawns new workers via subprocess when scaling up.
   - Marks workers `terminating` when scaling down.
2. Add docstring + CLI help examples.

### Phase 5: Documentation & Runbook
1. Add a short README section or separate `AUTOSCALING.md`:
   - Populate the work queue (e.g., `arxiv_crawler` in this repo).
   - Start scaler: `python -m research_paper_analysis.scaler --target-workers 3`.
   - Observe workers in `worker_registry`.
2. Document environment requirements (`CEREBRAS_API_KEY`, DB path).

---

## Operational Notes
- Use a heartbeat interval (e.g., 30–60s) to detect stale workers.
- Keep `idle_wait_seconds` low enough for responsiveness but avoid busy loops.
- Prefer `status=terminating` over hard-killing processes to allow graceful shutdown.
- All worker operations should be **idempotent** and safe under retries.

---

## Open Questions
- Confirm the canonical SQLite DB location (`arxiv_crawler/data/arxiv.sqlite`) or make it configurable via env.
- Decide if worker registry should be pruned automatically or kept for audit.
- Clarify how work results should be persisted in the queue row (e.g., `paper_queue.summary_path` in this repo).
- Finalize `RegistrationBackend` naming, status lifecycle, and lease/heartbeat semantics for the FlatAgents runtime spec.
- Decide whether capability metadata/namespace fields are required in the runtime spec for multi-tenant or heterogeneous clusters.
