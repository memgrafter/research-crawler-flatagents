# Throttling Plan (Rate-Limit Handling)

## Goal
Prevent poisoning when the model provider returns 429/rate-limit errors. Requeue jobs without error/attempt penalty, suspend **only** the affected provider/model from spawning new workers, while allowing other providers to continue.

## Scope
- Applies to `research_paper_analysis` distributed workers and the scaling daemon.
- Code locations:
  - `research_paper_analysis/python/src/research_paper_analysis/distributed_hooks.py`
  - `research_paper_analysis/config/parallelization_checker.yml`
  - `research_paper_analysis/config/paper_analysis_worker.yml`

## Where 429 is Detected (Error Source)
FlatAgents injects error info into context on any exception:
- `context["last_error"]` = stringified exception
- `context["last_error_type"]` = exception class name

Additional error fields already used by this code:
- `context["error"]` (set by `_prepare_paper`)
- `context.get("analysis_result", {}).get("error")`

**Primary detection input:** `last_error` with fallbacks listed above.

### Error extraction helper (to add)
Add a helper in `DistributedPaperAnalysisHooks`:

```python
RATE_LIMIT_PATTERNS = [
    r"429",
    r"ratelimiterror",
    r"too many requests",
    r"tokens per day limit exceeded",
    r"requests per second limit exceeded",
    r"requests per minute limit exceeded",
]


def _collect_error_text(context: dict) -> str:
    parts = []
    for key in ("last_error", "error"):
        value = context.get(key)
        if value:
            parts.append(str(value))
    analysis_error = (context.get("analysis_result") or {}).get("error")
    if analysis_error:
        parts.append(str(analysis_error))
    return " | ".join(parts)


def _is_rate_limit_error(context: dict) -> bool:
    error_text = _collect_error_text(context).lower()
    if context.get("last_error_type") == "RateLimitError":
        return True
    return any(re.search(pattern, error_text) for pattern in RATE_LIMIT_PATTERNS)
```

## Provider/Model Control Point (Allow-list)
Throttle only selected provider/model pairs via env:

```
RATE_LIMIT_THROTTLE_MODELS="cerebras/zai-glm-4.7"
```

**Parsing:** comma/space separated. Example:
```
RATE_LIMIT_THROTTLE_MODELS="cerebras/zai-glm-4.7,openai/gpt-4o"
```

### How provider/model is resolved
Throttle must be **per worker type**, not global. We derive a throttle key:

```
key = f"{provider}/{name}"  # e.g., "cerebras/zai-glm-4.7"
```

#### Worker (paper_analysis_worker) path
`FlatMachine` injects profiles into context as `_profiles`.
Resolve in hooks:

```python
profiles = context.get("_profiles") or {}
profiles_dict = profiles.get("profiles", {})
profile_name = profiles.get("override") or profiles.get("default")
profile = profiles_dict.get(profile_name, {})
provider = profile.get("provider")
name = profile.get("name")
```

#### Checker (parallelization_checker) path
The checker runs in a **separate machine** and must resolve the worker’s profile
from the worker config it is about to spawn.

Use `context["worker_config_path"]` from `parallelization_checker.yml`:

```yaml
context:
  worker_config_path: "paper_analysis_worker.yml"
```

Resolve worker config → profiles.yml → provider/name:

```python
config_dir = Path(__file__).parent.parent.parent.parent / "config"
worker_config = config_dir / context["worker_config_path"]
# Load worker config YAML, read `profiles: ./profiles.yml` if present
# Then load profiles.yml using flatagents.profiles.load_profiles_from_file
# Resolve override/default profile to provider/name
```

If provider/name is missing, skip throttling.

## Throttle State Storage (per provider/model)
Store throttle state in `crawler_state` (arxiv.sqlite):

- `worker_throttle_until:<provider>/<name>` = ISO timestamp
- `worker_throttle_reason:<provider>/<name>` = optional string

Example key:
```
worker_throttle_until:cerebras/zai-glm-4.7
```

### SQL (upsert)

```sql
INSERT INTO crawler_state (key, value)
VALUES (?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
```

## Backoff Policy (Env-driven)
Defaults (can be overridden):

```
RATE_LIMIT_BACKOFF_SECONDS=900
RATE_LIMIT_RPS_BACKOFF_SECONDS=60
RATE_LIMIT_DAY_BACKOFF_SECONDS=86400
```

Backoff selection logic:
- If error contains `tokens per day limit exceeded` → use `RATE_LIMIT_DAY_BACKOFF_SECONDS`.
- If error contains `requests per second/minute limit exceeded` → use `RATE_LIMIT_RPS_BACKOFF_SECONDS`.
- Otherwise → `RATE_LIMIT_BACKOFF_SECONDS`.

## Behavior Changes (Implementation Details)
### A) `DistributedPaperAnalysisHooks._fail_paper`
**Current:** increments attempts and poisons at max retries.

**New behavior on rate-limit + allow-listed:**
- **Requeue without penalty**:
  - `status = 'pending'`
  - `error = NULL`
  - do **NOT** increment `attempts`
  - clear `claimed_by`, `claimed_at`, `worker`
  - clear `finished_at`, `started_at` to keep queue clean
- **Set per-model throttle** in `crawler_state` (see above).

SQL example:

```sql
UPDATE paper_queue
SET status = 'pending',
    error = NULL,
    claimed_by = NULL,
    claimed_at = NULL,
    worker = NULL,
    started_at = NULL,
    finished_at = NULL
WHERE id = ?
```

**Non-rate-limit:** keep existing retry/poison logic with `attempts += 1`.

### B) Spawn Suppression (`_calculate_spawn`)
`parallelization_checker.yml` uses `calculate_spawn`.

**New logic:**
- Resolve throttle key **for the worker config being spawned**.
- Look up `worker_throttle_until:<key>` in `crawler_state`.
- If `now < throttle_until`:
  - force `workers_to_spawn = 0`
  - set `context.throttled = true`
  - set `context.throttle_until = <timestamp>`

This **only suppresses the throttled provider/model**. Other worker configs remain unaffected.

Optional cleanup:
- If `now >= throttle_until`, clear the key (optional), or simply allow spawns.

### C) Logging (Optional but recommended)
When throttled, log:
- provider/model key
- throttle_until
- reason

In `parallelization_checker.yml` output, optionally expose:

```yaml
output:
  throttled: "{{ context.throttled | default(false) }}"
  throttle_until: "{{ context.throttle_until | default('') }}"
```

## Heterogeneous Worker Behavior
- Each worker type uses its own profile key.
- Throttling is **per key**; only that worker type is blocked.
- Other worker types can continue to spawn and process jobs.

## Minimal Code Touches
1. `research_paper_analysis/python/src/research_paper_analysis/distributed_hooks.py`
   - Add helpers:
     - `_collect_error_text(context)`
     - `_is_rate_limit_error(context)`
     - `_resolve_throttle_key(context, worker_config_path=None)`
     - `_get_throttle_until(key)` / `_set_throttle_until(key, until, reason)`
   - Update `_fail_paper` to requeue + throttle on rate-limit
   - Update `_calculate_spawn` to block spawning for throttled provider/model

2. `research_paper_analysis/config/parallelization_checker.yml`
   - Ensure `worker_config_path` is present in context (already set). Optionally add throttle status to output.

## Future Extensions (Not Implemented Yet)
- Provider-specific wait/check loops.
- Automatic fallback to alternate profiles when throttled.
- Task-level throttling instead of provider-wide throttling.
