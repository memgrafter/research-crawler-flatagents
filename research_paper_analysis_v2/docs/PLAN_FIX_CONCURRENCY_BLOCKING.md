
 Planning async blocking offload

 100% — with 200 workers, we should treat prep as a proper async + worker-pool pipeline.

 Plan (no code yet)

 ### 1) Remove event-loop blocking in prep actions

 File: src/research_paper_analysis_v2/hooks.py

 Target methods:
 - _download_pdf (currently sync httpx.get)
 - _extract_text (currently sync PdfReader + file I/O)

 Plan:
 - Use async HTTP (httpx.AsyncClient) for downloads.
 - Offload PDF parsing + heavy text extraction to an executor:
     - preferably ProcessPoolExecutor (CPU-heavy parsing),
     - or ThreadPool as first step.
 - Offload large file read/write (read_text, write_text, write_bytes) with asyncio.to_thread(...).

 ────────────────────────────────────────────────────────────────────────────────

 ### 2) Add bounded internal prep resource gates (independent of --workers)

 Even with 200 machine workers, local resources need caps.

 Add semaphores for:
 - prep_download_concurrency (network/disk)
 - prep_extract_concurrency (CPU/pdf parsing)

 This keeps the event loop responsive while still letting many tasks progress concurrently.

 ────────────────────────────────────────────────────────────────────────────────

 ### 3) Add executor + client singletons (module-level)

 Avoid per-task creation overhead and FD churn:
 - shared async HTTP client
 - shared extraction pool(s)

 Use env knobs like:
 - RPA_V2_PREP_DOWNLOAD_CONCURRENCY
 - RPA_V2_PREP_EXTRACT_CONCURRENCY
 - RPA_V2_PREP_EXTRACT_WORKERS

 ────────────────────────────────────────────────────────────────────────────────

 ### 4) Fix status/lease drift visibility and reclaim policy

 You currently have many prepping rows without active leases; that inflates counts and hides real concurrency.

 Plan:
 - add a lease-aware “stuck prep” metric (prepping with no active lease).
 - optionally reclaim faster than 60m for unleased prep rows (configurable), so queue state reflects reality.

 ────────────────────────────────────────────────────────────────────────────────

 ### 5) Add instrumentation so we can tune 200 workers safely

 Track:
 - active downloads
 - active extractions
 - queue wait time before each gate
 - prep duration by sub-stage (download_pdf, extract_text, write_key_outcome)
 - event-loop lag (optional)

 This gives us hard data to set good defaults.

 ────────────────────────────────────────────────────────────────────────────────

 Rollout order (low risk)

 1. Phase A (safest): wrap blocking file/pdf work in to_thread / executor.
 2. Phase B: switch download to AsyncClient + shared client.
 3. Phase C: add semaphores + env knobs + metrics.
 4. Phase D: tighten stale reclaim for unleased prepping rows.

 ────────────────────────────────────────────────────────────────────────────────

 If you want, next I can turn this into an exact implementation checklist with default values for a 200-worker setup (e.g., download=48, extract=12 as a starting point).


