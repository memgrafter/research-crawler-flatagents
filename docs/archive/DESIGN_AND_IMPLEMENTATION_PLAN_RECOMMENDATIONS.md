# Review: Decentralized Autoscaling Plan vs. FlatAgents

*Reviewed: 2026-01-28*

---

## ✅ What Aligns Well with FlatAgents

| Aspect | Assessment |
|--------|------------|
| **Hook-based architecture** | Excellent use of `on_machine_start`, `on_machine_end`, and `on_action` hooks for worker lifecycle |
| **State machine design** | The proposed state flow is idiomatic FlatAgents: `initial` → action states → `final`, with transition conditions |
| **Looping patterns** | Using transitions back to `get_work` and `heartbeat_and_check` follows the documented loop pattern |
| **Action states** | Custom actions like `register_worker`, `dequeue_work`, `update_heartbeat` via `on_action` hook is the right approach |
| **Context/inputs** | `db_path`, `worker_id`, config values in context aligns with FlatAgents conventions |

---

## ⚠️ Design Considerations

### 1. `RegistrationBackend` as a new runtime interface

The plan proposes adding `RegistrationBackend` to `flatagents-runtime.d.ts`. This makes sense for portability—`flatagents-runtime.d.ts` currently has:
- `ExecutionLock`, `PersistenceBackend`, `ResultBackend`, `MachineHooks`, `LLMBackend`, `MachineInvoker`

A `RegistrationBackend` would be a natural peer.

> [!IMPORTANT]
> Decide whether this should be a first-class FlatAgents runtime concept or stay as an application-level abstraction.

### 2. Lease/heartbeat semantics

The plan mentions optional lease-based semantics. FlatAgents already has `timeout` on states.

**Recommendation**: Consider integrating with the existing `timeout` mechanism rather than adding a parallel concept.

### 3. Worker machine as "long-running"

FlatAgents machines are designed for bounded execution with `max_steps` safety. The design loops back via transitions, which is correct, but ensure `max_steps` is set high enough or disabled for this use case.

---

## ❌ Potential Issues

### 1. No use of `launch:` for fire-and-forget

The scaler spawns workers via `subprocess.Popen`. FlatAgents has native `launch:` for fire-and-forget machines.

**Alternative**: The scaler itself could be a FlatMachine that uses `launch:` to spawn worker machines, making the entire system machine-composable.

### 2. SQLite as work queue

FlatAgents has `ResultBackend` for inter-machine communication via URIs (`flatagents://{execution_id}/result`).

**Alternative**: Model work items as FlatAgents results instead of a raw SQLite table, gaining compatibility with distributed backends (Redis, etc.) without changing worker logic.

### 3. Missing `foreach` for batch processing

If you want a controller machine to process a batch of work items, `foreach` with `mode: settled` is the pattern. The current design treats workers as independent long-running processes, which is fine for autoscaling but misses potential FlatAgents-native batching.

---

## 💡 Suggestions

### 1. Consider a two-level architecture

```yaml
# coordinator.yml - FlatMachine that manages work distribution
states:
  get_pending:
    action: fetch_pending_work
    transitions:
      - condition: "context.items | length > 0"
        to: process_batch
      - to: wait_for_work
  
  process_batch:
    foreach: "{{ context.items }}"
    machine: paper_analyzer
    mode: settled
    output_to_context:
      results: "{{ output }}"
    transitions:
      - to: get_pending
```

### 2. Formalize the `RegistrationBackend` contract

Do this before implementing the SQLite version—this ensures future Redis/Consul backends have a clear spec.

### 3. Document interaction with `persistence:` config

Should worker machines checkpoint their state for crash recovery?

---

## 📊 Summary

| Category | Rating |
|----------|--------|
| FlatAgents pattern compliance | 🟢 Strong |
| Runtime spec extension design | 🟡 Good, needs formalization |
| Architecture completeness | 🟢 Comprehensive |
| Integration with existing FlatAgents features | 🟡 Could use more native features |

**Overall**: This is a solid plan that correctly leverages FlatAgents hooks and state patterns. The main opportunity is deeper integration with FlatAgents native constructs (`launch:`, `foreach:`, `ResultBackend`) rather than relying on external mechanisms (subprocess, raw SQLite). The `RegistrationBackend` proposal is a valuable contribution to the runtime spec if you want to pursue it.
