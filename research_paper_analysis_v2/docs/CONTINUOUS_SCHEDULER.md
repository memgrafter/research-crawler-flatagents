# Continuous Scheduler + Async I/O — Implementation Task List

**Goal:** Stop batch-starvation, let expensive work claim freed slots instantly,
unblock the event loop during download/extraction. Minimal diff, no new abstractions.

**Files touched:** `run.py` (scheduler), `hooks.py` (async I/O for 2 functions).

---

## 1. Add phase semaphores and env knobs to `run.py`

**Where:** After the existing module-level constants (line ~30 area, after `FINAL_STATES`).

```python
# --- Phase capacity caps (tunable via env) ---
_MAX_PREP = int(os.environ.get("RPA_V2_MAX_PREP", "0"))        # 0 = no cap
_MAX_EXPENSIVE = int(os.environ.get("RPA_V2_MAX_EXPENSIVE", "0"))
_MAX_WRAP = int(os.environ.get("RPA_V2_MAX_WRAP", "0"))
```

These are optional hard caps. When set to 0 (default), the phase is uncapped and
the total `max_workers` is the only limit. When non-zero, the phase gets its own
`asyncio.Semaphore` that bounds how many concurrent tasks of that type can run.

Sane starting point for 200 workers:
```bash
export RPA_V2_MAX_PREP=150
export RPA_V2_MAX_EXPENSIVE=40
export RPA_V2_MAX_WRAP=30
```

These intentionally sum > 200. The global `max_workers` is the real ceiling. The
per-phase caps only prevent any single phase from eating all slots. Overlap means
no slots go unused when a phase has no work.

---

## 2. Replace `run_once` + `run_daemon` with `run_continuous`

**What changes:** Delete `run_once` and `run_daemon`. Replace with a single
`run_continuous` function. The core loop:

1. Release stale claims once per sweep.
2. Pick one highest-priority task (expensive > wrap > prep).
3. Launch it (subject to global sem + phase sem).
4. `asyncio.wait(FIRST_COMPLETED)` — the instant ANY task finishes, loop back
   to step 2 and fill the freed slot with whatever is highest priority NOW.

**Pseudocode (the actual implementation target):**

```python
async def run_continuous(
    max_workers: int = 200,
    daily_budget: int = 10_000,
    prep_only: bool = False,
    poll_interval: float = 5.0,
    seed_limit: int = 500,
    stale_interval: float = 120.0,
) -> Dict[str, int]:

    conn = get_v2_db()
    stats = {
        "prep": 0, "expensive": 0, "wrap": 0,
        "resumed": 0, "errors": 0,
    }

    global_sem = asyncio.Semaphore(max_workers)

    # Phase sems: None means uncapped (global sem is the only gate)
    prep_sem = asyncio.Semaphore(_MAX_PREP) if _MAX_PREP > 0 else None
    expensive_sem = asyncio.Semaphore(_MAX_EXPENSIVE) if _MAX_EXPENSIVE > 0 else None
    wrap_sem = asyncio.Semaphore(_MAX_WRAP) if _MAX_WRAP > 0 else None

    active: set[asyncio.Task] = set()
    last_stale_check = 0.0

    def _on_done(task: asyncio.Task) -> None:
        active.discard(task)
        exc = task.exception() if not task.cancelled() else None
        if exc:
            stats["errors"] += 1
            logger.error("Task failed: %s", exc)

    async def _guarded(coro, phase_sem):
        """Acquire global + optional phase sem, then run."""
        async with global_sem:
            if phase_sem is not None:
                async with phase_sem:
                    return await coro
            return await coro

    while True:
        now = asyncio.get_event_loop().time()

        # --- Periodic housekeeping (stale release + seed + budget) ---
        if now - last_stale_check > stale_interval:
            release_stale(conn, "prepping", "pending", max_age_minutes=60)
            release_stale(conn, "analyzing", "prepped", max_age_minutes=120)
            release_stale(conn, "wrapping", "analyzed", max_age_minutes=60)
            seed(limit=seed_limit)
            last_stale_check = now

        usage = get_daily_usage(conn)
        if usage["total"] >= daily_budget:
            logger.info("Budget exhausted (%d/%d). Draining.", usage["total"], daily_budget)
            break

        # --- Fill free slots with highest-priority work ---
        launched = 0
        while len(active) < max_workers:
            coro, phase_sem, stat_key = _pick_next(conn, prep_only)
            if coro is None:
                break
            task = asyncio.create_task(_guarded(coro, phase_sem))
            task.add_done_callback(_on_done)
            active.add(task)
            stats[stat_key] += 1
            launched += 1

        # --- Wait for work or poll ---
        if active:
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            # done_callback already handled; loop back to fill slots
        else:
            # Nothing running and nothing to launch — check if finished
            remaining = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE status NOT IN ('done','failed')"
            ).fetchone()[0]
            if remaining == 0:
                logger.info("All work complete.")
                break
            await asyncio.sleep(poll_interval)

    # --- Drain remaining tasks ---
    if active:
        logger.info("Draining %d active tasks...", len(active))
        await asyncio.gather(*active, return_exceptions=True)

    return stats
```

---

## 3. Implement `_pick_next` — the priority dispatcher

Single function, returns `(coroutine, phase_sem, stat_key)` or `(None, None, None)`.

**Priority order (same as today, just one-at-a-time):**

```python
def _pick_next(conn, prep_only) -> Tuple:
    # 1. Resume incomplete (expensive first, then wrap, then prep)
    for machine_name, config_file, sem, key in [
        ("expensive-pipeline", EXPENSIVE_CONFIG, expensive_sem, "resumed"),
        ("wrap-pipeline", WRAP_CONFIG, wrap_sem, "resumed"),
        ("prep-pipeline", PREP_CONFIG, prep_sem, "resumed"),
    ]:
        if prep_only and machine_name != "prep-pipeline":
            continue
        incomplete = find_incomplete_executions(machine_name)[:1]
        if incomplete:
            return (resume_machine(incomplete[0], machine_name, config_file), sem, key)

    if not prep_only:
        # 2. New expensive (highest value)
        claimed = claim_for_expensive(conn, 1)
        if claimed:
            return (run_expensive(claimed[0]), expensive_sem, "expensive")

        # 3. New wrap (clears pipeline)
        claimed = claim_for_wrap(conn, 1)
        if claimed:
            return (run_wrap(claimed[0]), wrap_sem, "wrap")

    # 4. New prep (fills buffer)
    claimed = claim_for_prep(conn, 1)
    if claimed:
        return (run_prep(claimed[0]), prep_sem, "prep")

    return (None, None, None)
```

**Key detail:** Claims are `limit=1` per call. This is deliberate — claim one,
launch it, loop, re-evaluate priority. No batch over-allocation. The DB round-trip
per claim is ~0.1ms on SQLite WAL; at 200 workers this is negligible vs the
seconds each task takes.

---

## 4. Async-ify `_download_pdf` in `hooks.py`

**Current (blocks event loop):**
```python
resp = httpx.get(pdf_url, follow_redirects=True, timeout=60.0)
resp.raise_for_status()
pdf_path.write_bytes(resp.content)
```

**Replace with:**
```python
import time  # add to imports if missing

# Module-level singleton (add near _V2_WRITE_LOCK)
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP_CLIENT_LOCK: Optional[asyncio.Lock] = None

def _get_http_lock() -> asyncio.Lock:
    global _HTTP_CLIENT_LOCK
    if _HTTP_CLIENT_LOCK is None:
        _HTTP_CLIENT_LOCK = asyncio.Lock()
    return _HTTP_CLIENT_LOCK

async def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    async with _get_http_lock():
        if _HTTP_CLIENT is None:
            _HTTP_CLIENT = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(60.0),
                limits=httpx.Limits(
                    max_connections=int(os.environ.get("RPA_V2_HTTP_MAX_CONN", "128")),
                    max_keepalive_connections=int(os.environ.get("RPA_V2_HTTP_KEEPALIVE", "64")),
                ),
            )
    return _HTTP_CLIENT
```

**New `_download_pdf` body (replace only the download block inside the method):**
```python
if not pdf_path.exists():
    client = await _get_http_client()
    logger.info("Downloading PDF: %s", pdf_url)
    resp = await client.get(pdf_url)
    resp.raise_for_status()
    await asyncio.to_thread(pdf_path.write_bytes, resp.content)
else:
    logger.info("PDF already exists: %s", pdf_path)
```

Three lines changed. No new semaphore needed — the per-phase cap in the scheduler
already limits how many preps run concurrently, and httpx.Limits caps connections.

---

## 5. Offload `_extract_text` in `hooks.py`

**Current (blocks event loop — CPU-bound PDF parsing):**
All of `_extract_text` runs synchronously: `PdfReader`, page iteration, regex, file write.

**Change:** Wrap the entire body in `asyncio.to_thread`.

Add a module-level sync function (outside the class):

```python
def _extract_pdf_text_sync(pdf_path_str: str, txt_path_str: str) -> Tuple[str, int]:
    """Sync PDF extraction — runs in thread pool via to_thread."""
    pdf_path = Path(pdf_path_str)
    txt_path = Path(txt_path_str)

    if txt_path.exists():
        full_text = txt_path.read_text(encoding="utf-8")
    else:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(f"[PAGE {i + 1}]\n{text}")
        full_text = "\n\n".join(pages)
        full_text = full_text.encode("utf-8", "replace").decode("utf-8")
        txt_path.write_text(full_text, encoding="utf-8")

    ref_match = re.search(r"\nReferences\s*\n(.*)", full_text, re.DOTALL | re.IGNORECASE)
    references: List[str] = []
    if ref_match:
        ref_text = ref_match.group(1)
        refs = re.split(r"\n\s*\[?\d+\]?\s*", ref_text)
        references = [r.strip()[:200] for r in refs if len(r.strip()) > 20][:40]

    return full_text, len(references)
```

**New `_extract_text` method (replaces existing):**
```python
async def _extract_text(self, context: Dict[str, Any]) -> Dict[str, Any]:
    arxiv_id = self._norm(context.get("arxiv_id"))
    if not arxiv_id:
        raise ValueError("No arxiv_id in context for extract_text")

    safe_id = arxiv_id.replace("/", "_")
    pdf_path = self._data_dir / f"{safe_id}.pdf"
    txt_path = self._data_dir / f"{safe_id}.txt"

    full_text, ref_count = await asyncio.to_thread(
        _extract_pdf_text_sync, str(pdf_path), str(txt_path),
    )

    context["paper_text"] = full_text
    context["reference_count"] = ref_count
    return context
```

Uses `asyncio.to_thread` (default thread pool). No ProcessPoolExecutor, no extra
semaphore, no pickle overhead. Good enough — pypdf is I/O-bound not CPU-bound.

---

## 6. Update `run_daemon` and CLI wiring

**`run_daemon`:** Delete entirely. `run_continuous` IS the daemon — it loops
internally, seeds periodically, drains on budget/completion.

**`main()` changes:**
- `--daemon` flag: call `run_continuous` (was `run_daemon`)
- Non-daemon mode: call `run_continuous` but add a `max_passes=1` style limit,
  OR just keep calling `run_continuous` since it exits when all work is done.
- Add `--max-prep`, `--max-expensive`, `--max-wrap` CLI args that override the
  env-var defaults.

Minimal CLI diff:
```python
parser.add_argument("--max-prep", type=int, default=0, help="Max concurrent prep tasks (0=uncapped)")
parser.add_argument("--max-expensive", type=int, default=0, help="Max concurrent expensive tasks (0=uncapped)")
parser.add_argument("--max-wrap", type=int, default=0, help="Max concurrent wrap tasks (0=uncapped)")
```

---

## 7. Daily usage tracking adjustment

Current `increment_daily_usage` is called once at end of `run_once` batch.
With continuous scheduling there's no batch boundary.

**Fix:** Call `increment_daily_usage` inside the `_on_done` callback based on what
type of task completed, OR do it periodically (every N completions or every 60s).
Simplest: increment on every task completion. The DB write is a single
`INSERT ... ON CONFLICT UPDATE` — ~0.1ms per call, fine at this scale.

Track phase per-task by attaching it to the task:
```python
task.phase = stat_key  # "prep", "expensive", "wrap"
```

In `_on_done`:
```python
phase = getattr(task, "phase", None)
if phase == "prep":
    increment_daily_usage(conn, cheap=PREP_REQS)
elif phase == "expensive":
    increment_daily_usage(conn, expensive=EXPENSIVE_REQS)
elif phase == "wrap":
    increment_daily_usage(conn, cheap=WRAP_REQS)
```

---

## Summary of all changes

| # | File | What | Lines changed (approx) |
|---|------|------|----------------------|
| 1 | `run.py` | Add 3 env-var constants | +4 |
| 2 | `run.py` | `run_continuous` replaces `run_once`+`run_daemon` | ~80 new, ~120 deleted |
| 3 | `run.py` | `_pick_next` helper | +25 |
| 4 | `hooks.py` | Async HTTP client singleton | +20 |
| 5 | `hooks.py` | `_download_pdf` async | ~3 lines changed |
| 6 | `hooks.py` | `_extract_pdf_text_sync` + `_extract_text` rewrite | +25 new, ~25 deleted |
| 7 | `run.py` | CLI args + `main()` wiring | ~10 changed |
| 8 | `run.py` | `_on_done` usage tracking | +8 |

**Total:** ~160 lines new, ~145 lines deleted. Net delta: ~+15 lines.

**No new files. No new dependencies. No new abstractions.**

## Env var summary

| Variable | Default | Purpose |
|----------|---------|---------|
| `RPA_V2_MAX_PREP` | `0` (uncapped) | Max concurrent prep tasks |
| `RPA_V2_MAX_EXPENSIVE` | `0` (uncapped) | Max concurrent expensive tasks |
| `RPA_V2_MAX_WRAP` | `0` (uncapped) | Max concurrent wrap tasks |
| `RPA_V2_HTTP_MAX_CONN` | `128` | httpx connection pool size |
| `RPA_V2_HTTP_KEEPALIVE` | `64` | httpx keepalive connections |

## Verification

1. **Slot reclaim latency:** Run with `--workers 50`. Start 40 preps.
   When preps finish and create prepped rows, expensive tasks should
   start within seconds (not after all 40 preps complete).
2. **Phase caps:** Set `RPA_V2_MAX_PREP=10 --workers 50`. Confirm no more
   than 10 prep tasks run concurrently (rest are expensive/wrap).
3. **Event loop health:** During downloads, other tasks should continue
   transitioning states (log timestamps won't cluster/stall).
4. **Budget enforcement:** Set `--budget 20`. Confirm it stops after ~20 reqs.
