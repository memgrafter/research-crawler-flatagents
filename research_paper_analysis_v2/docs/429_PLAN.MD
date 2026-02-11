# 429 Rate Limit Fix Plan

## Problem

Running 500 workers against 200 RPM arcee and 100 RPM pony. The current system has no proactive rate limiter. It blasts requests, eats 429s, retries in thundering herds (40,086 arcee 429s in one 57-minute run). The `type: retry` backoff loops at the agent level create uncontrollable retry storms across hundreds of concurrent tasks.

## Desired Throughput

Pipeline is naturally balanced at ~33 papers/minute end-to-end:

| Phase | Model | Calls/paper | Target RPM | Papers/min |
|---|---|---|---|---|
| Prep | arcee | 1 | ~33 | ~33 |
| Expensive | pony | 3 | 100 | ~33 |
| Wrap | arcee | ~4 | ~132 | ~33 |

Total arcee: ~165/200 RPM. Total pony: 100/100 RPM. Fits with headroom.

Prep must never starve expensive. Prep needs 1 arcee call per paper to feed 3 pony calls per paper (1:3 ratio).

## Architecture

### 1. Switch all agents from `retry` to `default` execution

Every agent config in every machine currently has:
```yaml
execution:
  type: retry
  backoffs: [2, 8, 16]
```

Change to:
```yaml
execution:
  type: default
```

This means: one attempt, error surfaces immediately to the machine. No more agent-level retry storms.

Affected files:
- `config/prep_machine.yml` (write_key_outcome)
- `config/expensive_machine.yml` — no agent states directly, but its sub-machines:
  - `config/why_hypothesis_machine.yml`
  - `config/reproduction_machine.yml`
  - `config/open_questions_machine.yml`
- `config/wrap_machine.yml` (write_limits_confidence, assemble_report, judge_report, targeted_repair_once)

### 2. Add typed `on_error` routing in machine YAMLs

Each agent state gets:
```yaml
on_error:
  default: <phase>_failed
  RateLimitError: rate_limit_wait
```

Each machine gets a `rate_limit_wait` state:
```yaml
rate_limit_wait:
  action: wait_for_rate_limit
  transitions:
    - to: "{{ context._retry_state }}"
```

The hook must stash which state to return to in `context._retry_state` before the wait action runs. This could be done in `onError` hook or by having the machine set it via `output_to_context` on the error path. Needs investigation into how the flatmachines SDK surfaces the originating state name on error transitions.

**Open question**: Does the SDK pass the failing state name to `on_error` routing? If not, each agent state may need its own dedicated wait state that hardcodes the return transition (e.g., `wait_then_retry_limits`, `wait_then_retry_assembler`). Ugly but works.

### 3. Shared per-model token bucket in hooks.py

Add to `hooks.py`:
- A `TokenBucket` class (async, with lock)
- Two shared instances keyed by model name (arcee, pony)
- Bucket learns its rate from `AgentResult.rate_limit.windows[].limit` — not hardcoded
- Default seed rate conservative (e.g., 3/sec for arcee, 1.5/sec for pony) until first response updates it

New action handler `_wait_for_rate_limit`:
- Reads model identity and rate_limit info from context
- Calls `bucket.acquire()` which blocks until a token is available
- Returns context

**Open question**: How does the rate_limit info from AgentResult get into the machine context? The `output_to_context` mapping on the agent state only fires on success. On error, the SDK may put error info in `context.last_error` as a string, losing the structured rate_limit data. Need to check if the Python SDK's error path preserves `AgentResult.rate_limit` somewhere accessible.

### 4. Remove redundant pressure controls

- `run.sh`: Remove `RPA_V2_MAX_WRAP=600` (bucket handles pacing now)
- `hooks.py`: The `_RATE_LIMIT_GATES` dict, `any_rate_limit_gate_closed()`, 1-second cooldown gate, pony-100-mode — all become dead code once the bucket is in place. Remove or keep as fallback.
- `run.py`: The `llm_gated` check in `_pick_next` can be removed if the bucket is the sole rate limiter.

### 5. Balance prep vs wrap for arcee budget

The existing `_PREPPED_LOW_WATERMARK` scheduler logic controls which phase gets priority. When the prepped buffer is low, the scheduler launches more prep tasks, which means more prep calls enter the arcee bucket queue first. This already works — the bucket just makes it enforceable instead of aspirational.

No new balancing logic needed. The bucket + scheduler priority + pipeline structure (1 prep call : 4 wrap calls per paper) naturally keeps prep fed.

## Open Questions (must resolve before implementing)

1. **Error context shape**: What does the Python flatmachines SDK put in context when an agent fails? Is it just `context.last_error` (string) or does it preserve the structured `AgentResult` with `rate_limit` windows? If string-only, the bucket can't learn the rate from headers and needs a different seeding mechanism (e.g., parse the rate limit headers from the error string, or seed from env vars on first 429).

2. **Dynamic return state**: Can a `rate_limit_wait` state dynamically transition back to the state that triggered it? The `{{ context._retry_state }}` approach requires something to set that value. If the SDK doesn't expose the originating state, need per-agent wait states or a hook-based approach.

3. **Parallel machine error handling**: The expensive_machine runs 3 sub-machines in parallel (`mode: settled`). Each sub-machine has its own agent with `type: default`. If one sub-machine hits a 429, does the parent expensive_machine see a RateLimitError on the `parallel_expensive_writers` state? Or does the sub-machine's own `on_error` handle it? Need to trace the error propagation path through parallel machine execution.

4. **Non-429 transient errors**: The current retry backoffs also handle 503s, timeouts, and other transient failures. Switching to `type: default` means those also surface immediately. The `on_error` routing should probably handle `ServiceUnavailableError` and `TimeoutError` similarly to `RateLimitError`, or there needs to be a general transient-error wait state.

5. **Bucket fairness under contention**: With 500 workers and a bucket draining at ~3.3/sec (200/min), `acquire()` calls will queue up. Need to ensure FIFO ordering so no task starves. A simple `asyncio.Lock` + sleep loop may not be fair; may need an `asyncio.Queue` or ordered semaphore.
