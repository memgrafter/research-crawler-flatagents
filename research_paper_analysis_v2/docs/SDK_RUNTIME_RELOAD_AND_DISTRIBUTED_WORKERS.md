# FlatAgents/FlatMachines Runtime Reload Notes (Distributed-Worker Aware)

Date: 2026-02-14
Scope: investigation notes for `~/code/flatagents/sdk/python` and implications for this repo’s multi-worker execution model.

## Why this doc

We want fewer daemon restarts while preserving correctness for **distributed workers** (multiple processes/machines claiming work concurrently).

---

## Key findings from SDK (read-only investigation)

### 1) Machine configs are loaded per `FlatMachine` instance
- `flatmachines/flatmachines/flatmachine.py` loads config in `__init__` via `_load_config()`.
- A running machine execution does not live-reload its YAML mid-flight.

**Implication:** new machine instances see updated config; currently-running instances do not.

### 2) Agent executors are cached per machine instance
- `FlatMachine._get_executor()` caches in `self._agents`.

**Implication:** model/profile changes do not affect already-created executors inside that running machine instance.

### 3) FlatAgent adapter resolves profiles at executor creation time
- `flatmachines/adapters/flatagent.py` loads/discovers profiles when creating executor.
- `flatagents/flatagent.py` resolves model config at agent initialization.

**Implication:** newly-created executors pick up profile changes quickly (good for distributed workers that frequently create new machine instances).

### 4) SDK default checkpoint manager does not auto-purge terminal checkpoints
- `flatmachines/persistence.py` saves snapshots + latest pointer.
- Cleanup is explicit (`flatmachines/lifecycle.py` cleanup helpers).

**Implication:** warm resume is naturally compatible unless an app-specific backend chooses to purge terminal snapshots.

### 5) Distributed primitives exist at SDK level
- `flatmachines/distributed.py` provides worker registration + work backends (SQLite/memory).

**Implication:** SDK philosophy aligns with multi-process workers and externally coordinated work claiming.

---

## What is actually “hot” vs “restart-required”

## Hot (typically no full daemon restart)
- New machine/agent YAML for **newly-created** machine instances.
- Profile changes for **newly-created** agent executors.
- Scheduler behavior controlled by external state that is polled at runtime (if implemented in app logic).

## Usually requires restart
- Python code edits.
- Module-level constants read at import/startup.
- Process singletons initialized once (locks/backends/semaphores) unless app explicitly supports rebind/reinit.

---

## Distributed-worker design guidance (to avoid frequent restarts)

Use a **runtime control plane** (DB/table/kv) that workers poll.

### Recommended control-plane pattern
1. Store mutable controls externally (DB row/table):
   - phase caps (`max_prep`, `max_expensive`, `max_wrap`)
   - gating toggles
   - cooldown floors / retry profile selectors
2. Workers poll controls on a short interval (e.g., 1–5s) and apply on next scheduling tick.
3. Keep controls **versioned** (`updated_at` or monotonic version) for deterministic rollout.
4. Keep machine context/state in checkpoint/store, not in process globals.

This is distributed-safe because each worker independently converges to the same control state without needing coordinated process restarts.

---

## Operational model for this repo

### Prefer
- Changing behavior via data-plane controls (DB) and config values consumed at claim/tick boundaries.
- Rolling restarts only for code-level changes.

### Avoid
- Critical runtime policy locked in module-level constants only read once.
- “Restart-all-workers” as the primary mechanism for normal throttling/gating changes.

---

## Warm-restart specific note

If warm restart depends on checkpoint history, terminal checkpoint purge policy must preserve the data needed for reuse (or move warm-state into execution-row JSON).

For one-off jobs, retaining terminal checkpoints is often acceptable; for long-lived clusters, pair retention with TTL cleanup.

---

## Practical rule of thumb

- **Policy/config changes:** should be hot (control plane + polling).
- **Code changes:** rolling restart.
- **Emergency correctness fix:** restart acceptable, then backfill hot-control path.

---

## References inspected

- `flatmachines/flatmachines/flatmachine.py`
- `flatmachines/flatmachines/persistence.py`
- `flatmachines/flatmachines/lifecycle.py`
- `flatmachines/flatmachines/adapters/flatagent.py`
- `flatagents/flatagents/flatagent.py`
- `flatagents/flatagents/profiles.py`
- `flatmachines/flatmachines/distributed.py`
- `flatmachines/README.md`, `flatmachines/MACHINES.md`, `flatmachines/PIPELINE_SCHEDULER_PLAN.md`
