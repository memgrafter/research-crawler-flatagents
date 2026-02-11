
 Providing detailed async refactor checklist

 Perfect. We’ll keep reclaim behavior as-is.

 Here’s a focused implementation checklist to de-block the event loop and make prep scale to 200 workers.

 ────────────────────────────────────────────────────────────────────────────────

 1) Add async/offload infrastructure in src/research_paper_analysis_v2/hooks.py

 ### 1.1 Imports

 ```python
   import time
   from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, Executor
 ```

 ### 1.2 Module-level singletons + knobs

 ```python
   # --- Prep concurrency controls ---
   _PREP_DOWNLOAD_SEM: Optional[asyncio.Semaphore] = None
   _PREP_EXTRACT_SEM: Optional[asyncio.Semaphore] = None

   # --- Shared async HTTP client ---
   _HTTP_CLIENT: Optional[httpx.AsyncClient] = None
   _HTTP_CLIENT_LOCK: Optional[asyncio.Lock] = None

   # --- Blocking pools ---
   _POOL_LOCK = threading.Lock()
   _IO_POOL: Optional[ThreadPoolExecutor] = None
   _EXTRACT_POOL: Optional[Executor] = None
 ```

 ### 1.3 Helper getters

 ```python
   def _get_prep_download_sem() -> asyncio.Semaphore:
       global _PREP_DOWNLOAD_SEM
       if _PREP_DOWNLOAD_SEM is None:
           n = int(os.environ.get("RPA_V2_PREP_DOWNLOAD_CONCURRENCY", "64"))
           _PREP_DOWNLOAD_SEM = asyncio.Semaphore(max(1, n))
       return _PREP_DOWNLOAD_SEM

   def _get_prep_extract_sem() -> asyncio.Semaphore:
       global _PREP_EXTRACT_SEM
       if _PREP_EXTRACT_SEM is None:
           n = int(os.environ.get("RPA_V2_PREP_EXTRACT_CONCURRENCY", "16"))
           _PREP_EXTRACT_SEM = asyncio.Semaphore(max(1, n))
       return _PREP_EXTRACT_SEM

   def _get_io_pool() -> ThreadPoolExecutor:
       global _IO_POOL
       if _IO_POOL is not None:
           return _IO_POOL
       with _POOL_LOCK:
           if _IO_POOL is None:
               workers = int(os.environ.get("RPA_V2_PREP_IO_THREADS", "64"))
               _IO_POOL = ThreadPoolExecutor(max_workers=max(4, workers), thread_name_prefix="prep-io")
       return _IO_POOL

   def _get_extract_pool() -> Executor:
       global _EXTRACT_POOL
       if _EXTRACT_POOL is not None:
           return _EXTRACT_POOL
       with _POOL_LOCK:
           if _EXTRACT_POOL is None:
               mode = os.environ.get("RPA_V2_PREP_EXTRACT_POOL", "process").lower()
               workers = int(os.environ.get("RPA_V2_PREP_EXTRACT_WORKERS", "12"))
               if mode == "thread":
                   _EXTRACT_POOL = ThreadPoolExecutor(max_workers=max(2, workers), thread_name_prefix="prep-extract")
               else:
                   _EXTRACT_POOL = ProcessPoolExecutor(max_workers=max(2, workers))
       return _EXTRACT_POOL

   def _get_http_client_lock() -> asyncio.Lock:
       global _HTTP_CLIENT_LOCK
       if _HTTP_CLIENT_LOCK is None:
           _HTTP_CLIENT_LOCK = asyncio.Lock()
       return _HTTP_CLIENT_LOCK

   async def _get_http_client() -> httpx.AsyncClient:
       global _HTTP_CLIENT
       if _HTTP_CLIENT is not None:
           return _HTTP_CLIENT
       async with _get_http_client_lock():
           if _HTTP_CLIENT is None:
               limits = httpx.Limits(
                   max_connections=int(os.environ.get("RPA_V2_HTTP_MAX_CONNECTIONS", "256")),
                   max_keepalive_connections=int(os.environ.get("RPA_V2_HTTP_MAX_KEEPALIVE", "128")),
               )
               timeout = httpx.Timeout(60.0)
               _HTTP_CLIENT = httpx.AsyncClient(follow_redirects=True, limits=limits, timeout=timeout)
       return _HTTP_CLIENT
 ```

 ────────────────────────────────────────────────────────────────────────────────

 2) Refactor _download_pdf to true async + bounded concurrency

 Current blocker: sync httpx.get(...) + sync write.

 ### Replace with pattern

 ```python
   async def _download_pdf(self, context: Dict[str, Any]) -> Dict[str, Any]:
       arxiv_id = self._norm(context.get("arxiv_id"))
       if not arxiv_id:
           raise ValueError("No arxiv_id in context for download_pdf")

       self._data_dir.mkdir(parents=True, exist_ok=True)
       safe_id = arxiv_id.replace("/", "_")
       pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
       pdf_path = self._data_dir / f"{safe_id}.pdf"

       if pdf_path.exists():
           logger.info("PDF already exists: %s", pdf_path)
           context["pdf_path"] = str(pdf_path)
           return context

       t0 = time.perf_counter()
       async with _get_prep_download_sem():
           client = await _get_http_client()
           logger.info("Downloading PDF: %s", pdf_url)
           resp = await client.get(pdf_url)
           resp.raise_for_status()
           await asyncio.to_thread(pdf_path.write_bytes, resp.content)

       logger.info("download_pdf done arxiv_id=%s ms=%.1f bytes=%d",
                   arxiv_id, (time.perf_counter()-t0)*1000, len(resp.content))
       context["pdf_path"] = str(pdf_path)
       return context
 ```

 ────────────────────────────────────────────────────────────────────────────────

 3) Refactor _extract_text to executor (no event-loop blocking)

 ### 3.1 Add top-level sync worker function (module scope)

 ```python
   def _extract_pdf_text_sync(pdf_path_str: str, txt_path_str: str) -> Tuple[str, int]:
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
           # Optional safety against surrogate encoding issues:
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

 ### 3.2 Use it from async hook with semaphore + executor

 ```python
   async def _extract_text(self, context: Dict[str, Any]) -> Dict[str, Any]:
       arxiv_id = self._norm(context.get("arxiv_id"))
       if not arxiv_id:
           raise ValueError("No arxiv_id in context for extract_text")

       safe_id = arxiv_id.replace("/", "_")
       pdf_path = self._data_dir / f"{safe_id}.pdf"
       txt_path = self._data_dir / f"{safe_id}.txt"

       t0 = time.perf_counter()
       async with _get_prep_extract_sem():
           loop = asyncio.get_running_loop()
           full_text, ref_count = await loop.run_in_executor(
               _get_extract_pool(),
               _extract_pdf_text_sync,
               str(pdf_path),
               str(txt_path),
           )

       context["paper_text"] = full_text
       context["reference_count"] = ref_count
       logger.info("extract_text done arxiv_id=%s ms=%.1f chars=%d refs=%d",
                   arxiv_id, (time.perf_counter()-t0)*1000, len(full_text), ref_count)
       return context
 ```

 ────────────────────────────────────────────────────────────────────────────────

 4) Keep DB writes as-is for now (safe), but optionally offload heavy reads later

 You can leave:
 - _save_prep_result
 - _save_expensive_result
 - _save_wrap_result
 as currently implemented.

 Optional phase 2 if needed: move _collect_corpus_signals query path into to_thread too.

 ────────────────────────────────────────────────────────────────────────────────

 5) Suggested env defaults for 200 workers

 In run.sh (or shell before launch):

 ```bash
   export RPA_V2_PREP_DOWNLOAD_CONCURRENCY=64
   export RPA_V2_PREP_EXTRACT_CONCURRENCY=16
   export RPA_V2_PREP_EXTRACT_POOL=process
   export RPA_V2_PREP_EXTRACT_WORKERS=12
   export RPA_V2_PREP_IO_THREADS=64
   export RPA_V2_HTTP_MAX_CONNECTIONS=256
   export RPA_V2_HTTP_MAX_KEEPALIVE=128
 ```

 ────────────────────────────────────────────────────────────────────────────────

 6) Verification checklist after implementation

 1. Event loop no longer stalls
     - prepping should continue changing while downloads/extraction run.
 2. Lease/state coherence improves
     - fewer long-lived prepping rows with no active lease.
 3. Throughput
     - PREP DONE cadence increases and smooths out.
 4. Resource sanity
     - TCP FDs increase modestly (not explosive), CPU extraction pool stays bounded.

 ────────────────────────────────────────────────────────────────────────────────

 If you want, next step I can draft this as a concrete patch plan by section/line so you can apply it quickly in one
 pass.


