"""Hooks for Research Paper Analysis V2.

Implements custom FlatMachine actions for three pipeline phases:

Prep actions (prep_machine.yml):
- download_pdf
- extract_text
- collect_corpus_signals
- save_prep_result

Expensive actions (expensive_machine.yml):
- unpack_expensive_results
- save_expensive_result
- mark_execution_failed

Wrap actions (wrap_machine.yml):
- derive_terminology_tags
- prepend_frontmatter_v2
- normalize_judge_decision
- set_repair_attempted
- save_wrap_result
- mark_execution_failed
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import time

import httpx
import yaml
from pypdf import PdfReader

from flatmachines import LoggingHooks
from flatagents import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Transient error classification
# ---------------------------------------------------------------------------
_TRANSIENT_PATTERNS = (
    "ServiceUnavailableError", "503",
    "RateLimitError", "rate_limit", "429",
    "peer closed connection",
    "timeout", "Timeout",
    "APIError",
)

# Per-model 429 gates — each model has its own asyncio.Event, starts open (set).
# Cleared on 429 detection for that model, re-set after cooldown.
_RATE_LIMIT_GATES: Dict[str, asyncio.Event] = {}
_RATE_LIMIT_COOLDOWN = int(os.environ.get("RPA_V2_429_COOLDOWN_SECS", "1"))


def get_rate_limit_gate(model: str) -> asyncio.Event:
    """Lazy-init a per-model 429 gate (must be called from event loop)."""
    if model not in _RATE_LIMIT_GATES:
        ev = asyncio.Event()
        ev.set()  # open
        _RATE_LIMIT_GATES[model] = ev
    return _RATE_LIMIT_GATES[model]


def any_rate_limit_gate_closed() -> bool:
    """True if ANY model gate is closed (for scheduler gating)."""
    return any(not ev.is_set() for ev in _RATE_LIMIT_GATES.values())


def _is_transient(error: str) -> bool:
    return any(p in error for p in _TRANSIENT_PATTERNS)


def _is_rate_limit(error: str) -> bool:
    return "429" in error or "RateLimitError" in error or "rate_limit" in error.lower()

# ---------------------------------------------------------------------------
# Concurrency caps — all in one place, all env-overridable.
# ---------------------------------------------------------------------------
# Prep I/O (local semaphores in this module)
# best to keep download concurrency low to avoid throttling from arxiv. frees the pdfs for other work.
PREP_DOWNLOAD_CONCURRENCY = int(os.environ.get("RPA_V2_PREP_DOWNLOAD_CONCURRENCY", "5"))
PREP_EXTRACT_CONCURRENCY = int(os.environ.get("RPA_V2_PREP_EXTRACT_CONCURRENCY", "60"))
PREP_CORPUS_CONCURRENCY = int(os.environ.get("RPA_V2_PREP_CORPUS_CONCURRENCY", "60"))

# PDF download httpx client pool (our own AsyncClient for arxiv downloads)
HTTP_MAX_CONNECTIONS = int(os.environ.get("RPA_V2_HTTP_MAX_CONN", "128"))
HTTP_MAX_KEEPALIVE = int(os.environ.get("RPA_V2_HTTP_KEEPALIVE", "64"))
DOWNLOAD_USER_AGENT = os.environ.get("RPA_V2_DOWNLOAD_USER_AGENT", "")

# LLM aiohttp pool (litellm reads these at import time — set in run.sh)
# AIOHTTP_CONNECTOR_LIMIT        — total TCP connections (default 300)
# AIOHTTP_CONNECTOR_LIMIT_PER_HOST — per-host TCP connections (default 50)
# Both go to openrouter.ai so per-host is the binding constraint.
AIOHTTP_CONNECTOR_LIMIT = int(os.environ.get("AIOHTTP_CONNECTOR_LIMIT", "500"))
AIOHTTP_CONNECTOR_LIMIT_PER_HOST = int(os.environ.get("AIOHTTP_CONNECTOR_LIMIT_PER_HOST", "500"))
# Push into env so litellm picks them up at import time (no-op if run.sh already set them).
os.environ.setdefault("AIOHTTP_CONNECTOR_LIMIT", str(AIOHTTP_CONNECTOR_LIMIT))
os.environ.setdefault("AIOHTTP_CONNECTOR_LIMIT_PER_HOST", str(AIOHTTP_CONNECTOR_LIMIT_PER_HOST))

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "under",
    "using", "based", "toward", "towards", "via", "into", "onto", "their", "there",
    "paper", "approach", "method", "model", "models", "results", "analysis", "study",
    "new", "improving", "improved", "towards", "through", "within", "without", "between",
}

# Process-wide SQLite connection caches keyed by DB path.
_CONN_CACHE_LOCK = threading.Lock()
_ARXIV_CONN_CACHE: Dict[str, sqlite3.Connection] = {}
_V2_CONN_CACHE: Dict[str, sqlite3.Connection] = {}
# Thread-local cache for corpus signal connections (one per pool thread).
_CORPUS_THREAD_LOCAL = threading.local()
_V2_WRITE_LOCK: Optional[asyncio.Lock] = None

# Prep I/O singletons (created lazily on first use).
_DOWNLOAD_SEM: Optional[asyncio.Semaphore] = None
_EXTRACT_SEM: Optional[asyncio.Semaphore] = None
_CORPUS_SEM: Optional[asyncio.Semaphore] = None
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP_CLIENT_LOCK: Optional[asyncio.Lock] = None


def _get_v2_write_lock() -> asyncio.Lock:
    global _V2_WRITE_LOCK
    if _V2_WRITE_LOCK is None:
        _V2_WRITE_LOCK = asyncio.Lock()
    return _V2_WRITE_LOCK


def _get_download_sem() -> asyncio.Semaphore:
    global _DOWNLOAD_SEM
    if _DOWNLOAD_SEM is None:
        _DOWNLOAD_SEM = asyncio.Semaphore(max(1, PREP_DOWNLOAD_CONCURRENCY))
    return _DOWNLOAD_SEM


def _get_extract_sem() -> asyncio.Semaphore:
    global _EXTRACT_SEM
    if _EXTRACT_SEM is None:
        _EXTRACT_SEM = asyncio.Semaphore(max(1, PREP_EXTRACT_CONCURRENCY))
    return _EXTRACT_SEM


def _get_corpus_sem() -> asyncio.Semaphore:
    global _CORPUS_SEM
    if _CORPUS_SEM is None:
        _CORPUS_SEM = asyncio.Semaphore(max(1, PREP_CORPUS_CONCURRENCY))
    return _CORPUS_SEM


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
            headers = {}
            if DOWNLOAD_USER_AGENT:
                headers["User-Agent"] = DOWNLOAD_USER_AGENT
            _HTTP_CLIENT = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(60.0),
                headers=headers,
                limits=httpx.Limits(
                    max_connections=HTTP_MAX_CONNECTIONS,
                    max_keepalive_connections=HTTP_MAX_KEEPALIVE,
                ),
            )
    return _HTTP_CLIENT


def _extract_pdf_text_sync(pdf_path_str: str, txt_path_str: str) -> Tuple[str, int]:
    """Sync PDF text extraction — runs in thread pool via asyncio.to_thread."""
    pdf_path = Path(pdf_path_str)
    txt_path = Path(txt_path_str)

    if txt_path.exists() and txt_path.stat().st_size > 0:
        full_text = txt_path.read_text(encoding="utf-8", errors="replace")
    else:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(f"[PAGE {i + 1}]\n{text}")
        full_text = "\n\n".join(pages)
        # pypdf can produce lone surrogates from math-heavy PDFs; scrub them.
        full_text = full_text.encode("utf-8", "replace").decode("utf-8")
        txt_path.write_text(full_text, encoding="utf-8")

    ref_match = re.search(r"\nReferences\s*\n(.*)", full_text, re.DOTALL | re.IGNORECASE)
    references: List[str] = []
    if ref_match:
        ref_text = ref_match.group(1)
        refs = re.split(r"\n\s*\[?\d+\]?\s*", ref_text)
        references = [r.strip()[:200] for r in refs if len(r.strip()) > 20][:40]

    return full_text, len(references)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify_title(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return cleaned or "paper"


class V2Hooks(LoggingHooks):
    """Custom action hooks for the V2 pipeline.

    Manages two DB connections:
    - arxiv DB (read-only): corpus signals, neighbor search
    - v2 executions DB (read-write): execution tracking, daily usage
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        v2_db_path: Optional[str] = None,
        neighbors_considered: int = 25,
        neighbors_used: int = 8,
        log_level: int = 20,
    ):
        super().__init__(log_level=log_level)
        self._repo_root = Path(__file__).resolve().parents[3]
        self._project_root = Path(__file__).resolve().parents[2]
        self._data_dir = self._project_root / "data"

        # Arxiv DB — read-only for corpus signals
        self._db_path = db_path or os.environ.get(
            "ARXIV_DB_PATH",
            str(self._repo_root / "arxiv_crawler" / "data" / "arxiv.sqlite"),
        )
        self._neighbors_considered = int(neighbors_considered)
        self._neighbors_used = int(neighbors_used)
        self._conn: Optional[sqlite3.Connection] = None

        # V2 executions DB — read-write, owned by v2
        self._v2_db_path = v2_db_path or os.environ.get(
            "V2_EXECUTIONS_DB_PATH",
            str(self._data_dir / "v2_executions.sqlite"),
        )
        self._v2_conn: Optional[sqlite3.Connection] = None

    # -------------------------------------------------------------------------
    # DB connections
    # -------------------------------------------------------------------------

    def _conn_or_none(self) -> Optional[sqlite3.Connection]:
        """Arxiv DB connection (read-only for corpus signals)."""
        path = Path(self._db_path)
        if not path.exists():
            logger.warning("DB not found for corpus signals: %s", path)
            return None

        key = str(path.resolve())
        cached = _ARXIV_CONN_CACHE.get(key)
        if cached is not None:
            self._conn = cached
            return cached

        timeout_s = float(os.environ.get("RPA_V2_SQLITE_TIMEOUT_SECONDS", "30"))
        busy_timeout_ms = int(os.environ.get("RPA_V2_SQLITE_BUSY_TIMEOUT_MS", "10000"))

        with _CONN_CACHE_LOCK:
            cached = _ARXIV_CONN_CACHE.get(key)
            if cached is not None:
                self._conn = cached
                return cached

            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=max(timeout_s, 1.0))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {max(busy_timeout_ms, 1)}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            _ARXIV_CONN_CACHE[key] = conn
            self._conn = conn
            return conn

    def _v2_db(self) -> sqlite3.Connection:
        """V2 executions DB connection (read-write, owned by v2)."""
        path = Path(self._v2_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = str(path.resolve())

        cached = _V2_CONN_CACHE.get(key)
        if cached is not None:
            self._v2_conn = cached
            return cached

        with _CONN_CACHE_LOCK:
            cached = _V2_CONN_CACHE.get(key)
            if cached is not None:
                self._v2_conn = cached
                return cached

            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 10000")
            # Auto-create schema
            schema_file = self._project_root / "schema" / "v2_executions.sql"
            if schema_file.exists():
                conn.executescript(schema_file.read_text(encoding="utf-8"))
            _V2_CONN_CACHE[key] = conn
            self._v2_conn = conn
            return conn

    # -------------------------------------------------------------------------
    # Action router
    # -------------------------------------------------------------------------

    async def on_action(self, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            # Prep actions
            "download_pdf": self._download_pdf,
            "extract_text": self._extract_text,
            "collect_corpus_signals": self._collect_corpus_signals,
            "save_prep_result": self._save_prep_result,
            # Expensive actions
            "unpack_expensive_results": self._unpack_expensive_results,
            "save_expensive_result": self._save_expensive_result,
            # Wrap actions
            "derive_terminology_tags": self._derive_terminology_tags,
            "prepend_frontmatter_v2": self._prepend_frontmatter_v2,
            "normalize_judge_decision": self._normalize_judge_decision,
            "set_repair_attempted": self._set_repair_attempted,
            "save_wrap_result": self._save_wrap_result,
            # Shared
            "mark_execution_failed": self._mark_execution_failed,
        }
        handler = handlers.get(action_name)
        if handler:
            return await handler(context)
        return await super().on_action(action_name, context)

    # -------------------------------------------------------------------------
    # Prep actions: download_pdf, extract_text
    # -------------------------------------------------------------------------

    async def _download_pdf(self, context: Dict[str, Any]) -> Dict[str, Any]:
        arxiv_id = self._norm(context.get("arxiv_id"))
        if not arxiv_id:
            raise ValueError("No arxiv_id in context for download_pdf")

        self._data_dir.mkdir(parents=True, exist_ok=True)
        safe_id = arxiv_id.replace("/", "_")
        pdf_path = self._data_dir / f"{safe_id}.pdf"

        # Fast path: skip semaphore entirely if PDF is cached
        if pdf_path.exists():
            logger.info("PDF already exists: %s", pdf_path)
            context["pdf_path"] = str(pdf_path)
            return context

        pdf_url = f"https://export.arxiv.org/pdf/{arxiv_id}"
        t0 = time.perf_counter()
        async with _get_download_sem():
            client = await _get_http_client()
            logger.info("Downloading PDF: %s", pdf_url)
            resp = await client.get(pdf_url)
            resp.raise_for_status()
            await asyncio.to_thread(pdf_path.write_bytes, resp.content)

        logger.info("download_pdf done arxiv_id=%s ms=%.0f bytes=%d",
                     arxiv_id, (time.perf_counter() - t0) * 1000, len(resp.content))
        context["pdf_path"] = str(pdf_path)
        return context

    async def _extract_text(self, context: Dict[str, Any]) -> Dict[str, Any]:
        arxiv_id = self._norm(context.get("arxiv_id"))
        if not arxiv_id:
            raise ValueError("No arxiv_id in context for extract_text")

        safe_id = arxiv_id.replace("/", "_")
        pdf_path = self._data_dir / f"{safe_id}.pdf"
        txt_path = self._data_dir / f"{safe_id}.txt"

        t0 = time.perf_counter()
        async with _get_extract_sem():
            full_text, ref_count = await asyncio.to_thread(
                _extract_pdf_text_sync, str(pdf_path), str(txt_path),
            )

        logger.info("extract_text done arxiv_id=%s ms=%.0f chars=%d refs=%d",
                     arxiv_id, (time.perf_counter() - t0) * 1000, len(full_text), ref_count)
        context["paper_text"] = full_text
        context["reference_count"] = ref_count
        return context

    # -------------------------------------------------------------------------
    # Prep result: save to v2 executions DB
    # -------------------------------------------------------------------------

    async def _save_prep_result(self, context: Dict[str, Any]) -> Dict[str, Any]:
        execution_id = self._norm(context.get("execution_id"))
        if not execution_id:
            logger.warning("No execution_id in context, skipping save_prep_result")
            return context

        prep_output = {
            "key_outcome": context.get("key_outcome"),
            "paper_text": context.get("paper_text"),
            "reference_count": context.get("reference_count"),
            "corpus_signals": context.get("corpus_signals"),
            "corpus_neighbors": context.get("corpus_neighbors"),
        }

        async with _get_v2_write_lock():
            conn = self._v2_db()
            conn.execute(
                """
                UPDATE executions
                SET status = 'prepped',
                    prep_output = ?,
                    updated_at = ?
                WHERE execution_id = ?
                """,
                (json.dumps(prep_output), _utc_now_iso(), execution_id),
            )
            conn.commit()

        logger.info("Prep result saved for execution %s", execution_id)
        return context

    # -------------------------------------------------------------------------
    # Analysis actions
    # -------------------------------------------------------------------------

    async def _unpack_expensive_results(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Unpack parallel expensive machine output into context fields.

        parallel_raw (from output_to_context) is:
        {
            "why_hypothesis_machine": {"content": "..."},
            "reproduction_machine": {"content": "..."},
            "open_questions_machine": {"content": "..."}
        }
        """
        parallel_raw = context.get("parallel_raw") or {}

        field_map = {
            "why_hypothesis_machine": "why_hypotheses",
            "reproduction_machine": "reproduction_notes",
            "open_questions_machine": "open_questions",
        }

        for machine_name, context_key in field_map.items():
            result = parallel_raw.get(machine_name) or {}
            if isinstance(result, dict):
                if result.get("_error"):
                    logger.error("%s failed: %s", machine_name, result["_error"])
                context[context_key] = result.get("content") or None
            else:
                context[context_key] = str(result) if result else None

        logger.info(
            "Unpacked expensive results: why=%s chars, repro=%s chars, open_q=%s chars",
            len(context.get("why_hypotheses") or ""),
            len(context.get("reproduction_notes") or ""),
            len(context.get("open_questions") or ""),
        )
        return context

    @staticmethod
    async def _normalize_judge_decision(context: Dict[str, Any]) -> Dict[str, Any]:
        raw = (context.get("judge_decision_raw") or "").strip().upper()
        match = re.search(r"\b(PASS|REPAIR|FAIL)\b", raw)
        context["judge_decision"] = match.group(1) if match else "REPAIR"
        return context

    @staticmethod
    async def _set_repair_attempted(context: Dict[str, Any]) -> Dict[str, Any]:
        context["repair_attempted"] = True
        return context

    async def _save_expensive_result(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Save expensive phase output to v2 DB, mark status=analyzed."""
        execution_id = self._norm(context.get("execution_id"))
        if not execution_id:
            logger.warning("No execution_id in context, skipping save_expensive_result")
            return context

        expensive_output = {
            "why_hypotheses": context.get("why_hypotheses"),
            "reproduction_notes": context.get("reproduction_notes"),
            "open_questions": context.get("open_questions"),
        }

        async with _get_v2_write_lock():
            conn = self._v2_db()
            conn.execute(
                """
                UPDATE executions
                SET status = 'analyzed',
                    expensive_output = ?,
                    updated_at = ?
                WHERE execution_id = ?
                """,
                (json.dumps(expensive_output), _utc_now_iso(), execution_id),
            )
            conn.commit()

        logger.info("Expensive result saved for execution %s", execution_id)
        return context

    async def _save_wrap_result(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Save final report to disk, mark status=done in v2 DB."""
        execution_id = self._norm(context.get("execution_id"))
        formatted_report = context.get("formatted_report")
        title = self._norm(context.get("title"))
        arxiv_id = self._norm(context.get("arxiv_id"))

        result_path = None
        if formatted_report:
            try:
                self._data_dir.mkdir(parents=True, exist_ok=True)
                safe_id = arxiv_id.replace("/", "_") if arxiv_id else "unknown"
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{safe_id}_{_slugify_title(title)}_{timestamp}.md"
                file_path = self._data_dir / filename
                file_path.write_text(str(formatted_report), encoding="utf-8")
                result_path = str(file_path)
                logger.info("Report written: %s", result_path)
            except Exception as exc:
                logger.exception("Failed writing report file")
                context["last_error"] = str(exc)

        if execution_id:
            async with _get_v2_write_lock():
                conn = self._v2_db()
                conn.execute(
                    """
                    UPDATE executions
                    SET status = 'done',
                        result_path = ?,
                        error = NULL,
                        updated_at = ?
                    WHERE execution_id = ?
                    """,
                    (result_path, _utc_now_iso(), execution_id),
                )
                conn.commit()

        context["result_path"] = result_path
        return context

    async def _mark_execution_failed(self, context: Dict[str, Any]) -> Dict[str, Any]:
        execution_id = self._norm(context.get("execution_id"))
        error = (
            context.get("last_error")
            or context.get("error")
            or "unknown error"
        )
        error_str = str(error)

        if execution_id:
            # Transient HTTP errors → reset to phase for re-pickup
            if _is_transient(error_str):
                # Fire per-model 429 gate if rate-limited
                if _is_rate_limit(error_str):
                    model = context.get("model") or context.get("last_model") or "unknown"
                    gate = get_rate_limit_gate(model)
                    if gate.is_set():
                        gate.clear()
                        loop = asyncio.get_event_loop()
                        loop.call_later(_RATE_LIMIT_COOLDOWN, gate.set)
                        logger.warning("429 on %s — gate closed for %ds", model, _RATE_LIMIT_COOLDOWN)

                # Determine reset status from what data we have
                async with _get_v2_write_lock():
                    conn = self._v2_db()
                    row = conn.execute(
                        "SELECT prep_output, expensive_output FROM executions WHERE execution_id = ?",
                        (execution_id,),
                    ).fetchone()
                    if row and row[1]:  # has expensive_output
                        reset_status = "analyzed"
                    elif row and row[0]:  # has prep_output
                        reset_status = "prepped"
                    else:
                        reset_status = "pending"
                    conn.execute(
                        "UPDATE executions SET status = ?, error = NULL, updated_at = ? WHERE execution_id = ?",
                        (reset_status, _utc_now_iso(), execution_id),
                    )
                    conn.commit()
                logger.info("Transient error for %s — reset to %s: %s", execution_id, reset_status, error_str[:120])
                return context

            # Permanent error → mark failed
            async with _get_v2_write_lock():
                conn = self._v2_db()
                conn.execute(
                    """
                    UPDATE executions
                    SET status = 'failed',
                        error = ?,
                        updated_at = ?
                    WHERE execution_id = ?
                    """,
                    (error_str, _utc_now_iso(), execution_id),
                )
                conn.commit()
            logger.warning("Execution %s marked failed: %s", execution_id, error_str)

        return context

    def _corpus_conn_for_thread(self) -> Optional[sqlite3.Connection]:
        """Return a per-thread read-only connection to the arxiv DB."""
        path = Path(self._db_path)
        if not path.exists():
            return None

        resolved = str(path.resolve())
        conn = getattr(_CORPUS_THREAD_LOCAL, "conn", None)
        cached_path = getattr(_CORPUS_THREAD_LOCAL, "path", None)

        if conn is not None and cached_path == resolved:
            return conn

        timeout_s = float(os.environ.get("RPA_V2_SQLITE_TIMEOUT_SECONDS", "30"))
        busy_timeout_ms = int(os.environ.get("RPA_V2_SQLITE_BUSY_TIMEOUT_MS", "10000"))

        conn = sqlite3.connect(resolved, check_same_thread=False, timeout=max(timeout_s, 1.0))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {max(busy_timeout_ms, 1)}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

        _CORPUS_THREAD_LOCAL.conn = conn
        _CORPUS_THREAD_LOCAL.path = resolved
        return conn

    def _collect_corpus_signals_sync(
        self,
        arxiv_id: str,
        title: str,
        abstract: str,
        neighbors_considered: int,
        neighbors_used: int,
    ) -> Dict[str, Any]:
        """Pure sync corpus signal collection. Runs in thread pool."""
        conn = self._corpus_conn_for_thread()
        if conn is None:
            return {
                "corpus_signals": {
                    "status": "db_unavailable",
                    "summary": "Corpus database unavailable; proceeding with paper-only analysis.",
                },
                "corpus_neighbors": [],
                "neighbors_considered": 0,
                "neighbors_used": 0,
            }
        current_paper_id = self._paper_id_for_arxiv(conn, arxiv_id)
        rows = self._search_neighbors(conn, title, abstract, current_paper_id, neighbors_considered)

        considered = len(rows)
        used_rows = rows[:neighbors_used]
        neighbors = [self._row_to_neighbor(r) for r in used_rows]

        fmr_vals = [float((r["fmr_score"] or 0.0)) for r in rows]
        cit_vals = [int((r["cited_by_count"] or 0)) for r in rows]
        h_vals = [int(r["max_h_index"]) for r in rows if r["max_h_index"] is not None]

        signals = {
            "status": "ok",
            "neighbor_query": self._build_fts_query(title, abstract),
            "avg_neighbor_fmr": round(self._safe_avg(fmr_vals), 4),
            "max_neighbor_fmr": round(max(fmr_vals), 4) if fmr_vals else 0.0,
            "avg_neighbor_citations": round(self._safe_avg(cit_vals), 2),
            "max_neighbor_citations": max(cit_vals) if cit_vals else 0,
            "max_neighbor_author_h_index": max(h_vals) if h_vals else None,
            "neighbor_titles": [n["title"] for n in neighbors[:5]],
            "summary": self._build_signal_summary(considered, neighbors, fmr_vals, cit_vals),
        }

        return {
            "corpus_signals": signals,
            "corpus_neighbors": neighbors,
            "neighbors_considered": considered,
            "neighbors_used": len(neighbors),
        }

    async def _collect_corpus_signals(self, context: Dict[str, Any]) -> Dict[str, Any]:
        title = self._norm(context.get("title"))
        abstract = self._norm(context.get("abstract"))
        arxiv_id = self._norm(context.get("arxiv_id"))

        t0 = time.perf_counter()
        async with _get_corpus_sem():
            result = await asyncio.to_thread(
                self._collect_corpus_signals_sync,
                arxiv_id, title, abstract,
                self._neighbors_considered, self._neighbors_used,
            )

        logger.info("collect_corpus_signals done arxiv_id=%s ms=%.0f",
                     arxiv_id, (time.perf_counter() - t0) * 1000)
        context.update(result)
        return context

    def _paper_id_for_arxiv(self, conn: sqlite3.Connection, arxiv_id: str) -> Optional[int]:
        if not arxiv_id:
            return None
        row = conn.execute(
            """
            SELECT id FROM papers
            WHERE arxiv_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (arxiv_id,),
        ).fetchone()
        return int(row[0]) if row else None

    def _search_neighbors(
        self,
        conn: sqlite3.Connection,
        title: str,
        abstract: str,
        current_paper_id: Optional[int],
        limit: int,
    ) -> List[sqlite3.Row]:
        query = self._build_fts_query(title, abstract)
        if not query:
            return []

        sql = """
        WITH latest_versions AS (
            SELECT arxiv_id, MAX(version) AS max_version
            FROM papers
            GROUP BY arxiv_id
        ),
        author_agg AS (
            SELECT
                pa.paper_id,
                MAX(a.h_index) AS max_h_index
            FROM paper_authors pa
            JOIN authors a ON a.openalex_id = pa.author_openalex_id
            GROUP BY pa.paper_id
        )
        SELECT
            p.id,
            p.arxiv_id,
            p.title,
            p.abstract,
            COALESCE(pr.fmr_score, 0.0) AS fmr_score,
            COALESCE(pc.cited_by_count, 0) AS cited_by_count,
            aa.max_h_index
        FROM papers_fts
        JOIN papers p ON p.id = papers_fts.rowid
        JOIN latest_versions lv
          ON lv.arxiv_id = p.arxiv_id
         AND lv.max_version = p.version
        LEFT JOIN paper_relevance pr ON pr.paper_id = p.id
        LEFT JOIN paper_citations pc ON pc.paper_id = p.id
        LEFT JOIN author_agg aa ON aa.paper_id = p.id
        WHERE papers_fts MATCH ?
          AND (? IS NULL OR p.id != ?)
        ORDER BY bm25(papers_fts) ASC,
                 COALESCE(pr.fmr_score, 0) DESC,
                 COALESCE(pc.cited_by_count, 0) DESC
        LIMIT ?
        """

        try:
            return list(conn.execute(sql, (query, current_paper_id, current_paper_id, limit)).fetchall())
        except sqlite3.Error as exc:
            logger.warning("FTS neighbor query failed (%s). Falling back to LIKE search.", exc)
            return self._search_neighbors_fallback(conn, title, abstract, current_paper_id, limit)

    def _search_neighbors_fallback(
        self,
        conn: sqlite3.Connection,
        title: str,
        abstract: str,
        current_paper_id: Optional[int],
        limit: int,
    ) -> List[sqlite3.Row]:
        keywords = self._keywords(title + " " + abstract, max_terms=8)
        if not keywords:
            return []
        like_clauses = " OR ".join(["(p.title LIKE ? OR p.abstract LIKE ?)"] * len(keywords))
        params: List[Any] = []
        for kw in keywords:
            pat = f"%{kw}%"
            params.extend([pat, pat])

        sql = f"""
        WITH latest_versions AS (
            SELECT arxiv_id, MAX(version) AS max_version
            FROM papers
            GROUP BY arxiv_id
        )
        SELECT
            p.id,
            p.arxiv_id,
            p.title,
            p.abstract,
            COALESCE(pr.fmr_score, 0.0) AS fmr_score,
            COALESCE(pc.cited_by_count, 0) AS cited_by_count,
            NULL AS max_h_index
        FROM papers p
        JOIN latest_versions lv
          ON lv.arxiv_id = p.arxiv_id
         AND lv.max_version = p.version
        LEFT JOIN paper_relevance pr ON pr.paper_id = p.id
        LEFT JOIN paper_citations pc ON pc.paper_id = p.id
        WHERE ({like_clauses})
          AND (? IS NULL OR p.id != ?)
        ORDER BY COALESCE(pr.fmr_score, 0) DESC,
                 COALESCE(pc.cited_by_count, 0) DESC
        LIMIT ?
        """
        params.extend([current_paper_id, current_paper_id, limit])
        try:
            return list(conn.execute(sql, tuple(params)).fetchall())
        except sqlite3.Error:
            return []

    @staticmethod
    def _row_to_neighbor(row: sqlite3.Row) -> Dict[str, Any]:
        abstract = (row["abstract"] or "").strip().replace("\n", " ")
        if len(abstract) > 320:
            abstract = abstract[:317].rstrip() + "..."
        return {
            "paper_id": int(row["id"]),
            "arxiv_id": row["arxiv_id"] or "",
            "title": row["title"] or "",
            "abstract": abstract,
            "fmr_score": float(row["fmr_score"] or 0.0),
            "cited_by_count": int(row["cited_by_count"] or 0),
            "max_h_index": row["max_h_index"],
        }

    def _build_signal_summary(
        self,
        considered: int,
        neighbors: List[Dict[str, Any]],
        fmr_vals: List[float],
        cit_vals: List[int],
    ) -> str:
        if considered == 0:
            return "No corpus neighbors found; rely primarily on paper-local evidence."
        avg_fmr = round(self._safe_avg(fmr_vals), 3)
        avg_cit = round(self._safe_avg(cit_vals), 1)
        top_titles = ", ".join(n["title"] for n in neighbors[:3] if n.get("title"))
        return (
            f"Found {considered} related papers (using {len(neighbors)}). "
            f"Average neighbor FMR={avg_fmr}, average citations={avg_cit}. "
            f"Top related titles: {top_titles}."
        )

    @staticmethod
    def _safe_avg(values: Iterable[float]) -> float:
        vals = list(values)
        return sum(vals) / len(vals) if vals else 0.0

    def _build_fts_query(self, title: str, abstract: str) -> str:
        title_terms = self._keywords(title, max_terms=8)
        abstract_terms = self._keywords(abstract, max_terms=10)
        terms = []
        seen = set()
        for term in title_terms + abstract_terms:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)

        # Quote all terms to avoid FTS5 parsing hyphenated tokens as column/expression syntax.
        # Example: unquoted `in-context` can raise `no such column: context`.
        quoted_terms = [self._quote_fts_term(t) for t in terms[:12]]
        quoted_terms = [t for t in quoted_terms if t]
        return " OR ".join(quoted_terms)

    @staticmethod
    def _quote_fts_term(term: str) -> str:
        t = (term or "").strip()
        if not t:
            return ""
        return '"' + t.replace('"', '""') + '"'

    def _keywords(self, text: str, max_terms: int = 12) -> List[str]:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", (text or "").lower())
        counter: Counter[str] = Counter()
        for tok in tokens:
            if tok in STOPWORDS:
                continue
            if tok.isdigit():
                continue
            counter[tok] += 1
        ranked = [t for t, _ in counter.most_common(max_terms)]
        return ranked

    async def _derive_terminology_tags(self, context: Dict[str, Any]) -> Dict[str, Any]:
        title = self._norm(context.get("title"))
        abstract = self._norm(context.get("abstract"))
        paper_text_raw = self._norm(context.get("paper_text"))
        neighbors = context.get("corpus_neighbors") or []

        paper_text = "\n".join([title, abstract, paper_text_raw]).lower()
        neighbor_text = "\n".join(
            [f"{n.get('title', '')}\n{n.get('abstract', '')}" for n in neighbors if isinstance(n, dict)]
        ).lower()
        combined = "\n".join([paper_text, neighbor_text])

        term_map = self._load_terminology_map()
        taxonomy = self._load_taxonomy_terms()

        scores: Dict[str, Dict[str, Any]] = {}

        # Score mapped canonical terms first.
        max_paper_hits = 1
        max_neighbor_hits = 1
        cache_hits: Dict[str, Tuple[int, int, bool]] = {}

        for canonical, aliases in term_map.items():
            paper_hits = 0
            neighbor_hits = 0
            all_aliases = sorted(set([canonical, *aliases]))
            for alias in all_aliases:
                paper_hits += self._count_phrase(paper_text, alias)
                neighbor_hits += self._count_phrase(neighbor_text, alias)
            taxonomy_hit = self._taxonomy_has_alias(taxonomy, canonical, all_aliases)
            cache_hits[canonical] = (paper_hits, neighbor_hits, taxonomy_hit)
            max_paper_hits = max(max_paper_hits, paper_hits)
            max_neighbor_hits = max(max_neighbor_hits, neighbor_hits)

        for canonical, (paper_hits, neighbor_hits, taxonomy_hit) in cache_hits.items():
            if paper_hits == 0 and neighbor_hits == 0 and not taxonomy_hit:
                continue
            paper_component = min(1.0, math.log1p(paper_hits) / math.log1p(max_paper_hits)) if paper_hits else 0.0
            neighbor_component = (
                min(1.0, math.log1p(neighbor_hits) / math.log1p(max_neighbor_hits)) if neighbor_hits else 0.0
            )
            weight = round(0.70 * paper_component + 0.20 * neighbor_component + 0.10 * (1.0 if taxonomy_hit else 0.0), 3)
            sources = []
            if paper_hits > 0:
                sources.append("paper_text")
            if neighbor_hits > 0:
                sources.append("corpus_neighbors")
            if taxonomy_hit:
                sources.append("taxonomy")
            scores[canonical] = {
                "weight": weight,
                "sources": sources,
            }

        # Backfill with salient paper keywords if map hits are sparse.
        if len(scores) < 8:
            for token in self._keywords(title + " " + abstract + " " + paper_text_raw, max_terms=20):
                tag = self._slugify(token)
                if tag in scores or len(tag) < 4:
                    continue
                scores[tag] = {
                    "weight": 0.28,
                    "sources": ["paper_text"],
                }
                if len(scores) >= 12:
                    break

        sorted_tags = sorted(scores.items(), key=lambda kv: kv[1].get("weight", 0), reverse=True)

        # Keep original ranking from research signal scoring.
        terminology_tags = [tag for tag, _ in sorted_tags[:15]]
        terminology_tag_meta = {tag: meta for tag, meta in sorted_tags[:15]}

        domain_scores = self._score_domain_tags(taxonomy, combined)
        domain_tags = [name for name, score in domain_scores[:4] if score > 0]

        context["terminology_tags"] = terminology_tags
        context["domain_tags"] = domain_tags
        context["terminology_tag_meta"] = terminology_tag_meta
        return context

    def _load_terminology_map(self) -> Dict[str, List[str]]:
        map_path = self._project_root / "config" / "terminology_map.yml"
        if not map_path.exists():
            return {}
        try:
            data = yaml.safe_load(map_path.read_text(encoding="utf-8")) or {}
            normalized: Dict[str, List[str]] = {}
            for canonical, aliases in data.items():
                c = self._slugify(str(canonical))
                if isinstance(aliases, list):
                    normalized[c] = [self._slugify(str(a)) for a in aliases if str(a).strip()]
                else:
                    normalized[c] = []
            return normalized
        except Exception as exc:
            logger.warning("Failed loading terminology map: %s", exc)
            return {}

    def _load_taxonomy_terms(self) -> Dict[str, List[str]]:
        taxonomy_dir = self._project_root / "queries" / "word_clouds"
        if not taxonomy_dir.exists():
            return {}

        taxonomy: Dict[str, List[str]] = {}
        for path in sorted(taxonomy_dir.glob("*.txt")):
            terms: List[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                terms.append(self._slugify(line))
            taxonomy[path.stem] = terms
        return taxonomy

    @staticmethod
    def _taxonomy_has_alias(taxonomy: Dict[str, List[str]], canonical: str, aliases: List[str]) -> bool:
        alias_set = set(aliases + [canonical])
        for terms in taxonomy.values():
            if alias_set.intersection(terms):
                return True
        return False

    def _score_domain_tags(self, taxonomy: Dict[str, List[str]], combined_text: str) -> List[Tuple[str, int]]:
        scored: List[Tuple[str, int]] = []
        for domain, terms in taxonomy.items():
            score = 0
            for term in terms:
                score += self._count_phrase(combined_text, term)
            scored.append((domain, score))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    def _count_phrase(self, text: str, phrase_slug: str) -> int:
        if not text or not phrase_slug:
            return 0
        tokens = [re.escape(t) for t in phrase_slug.split("-") if t]
        if not tokens:
            return 0
        pattern = r"\\b" + r"[-_\\s]+".join(tokens) + r"\\b"
        return len(re.findall(pattern, text, flags=re.IGNORECASE))

    async def _prepend_frontmatter_v2(self, context: Dict[str, Any]) -> Dict[str, Any]:
        report_body = self._norm(context.get("report_body"))
        if not report_body:
            context["frontmatter"] = ""
            context["formatted_report"] = report_body
            return context
        if report_body.lstrip().startswith("---\n"):
            context["frontmatter"] = ""
            context["formatted_report"] = report_body
            return context

        key_outcome = self._norm(context.get("key_outcome"))
        core_contribution = " ".join(self._sentences_from_markdown(key_outcome, max_sentences=2))

        frontmatter = {
            "ver": "rpa2",
            "title": self._norm(context.get("title")),
            "arxiv_id": self._norm(context.get("arxiv_id")),
            "source_url": self._norm(context.get("source_url")),
            "tags": context.get("terminology_tags") or [],
            "core_contribution": core_contribution,
        }

        fm_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        fm = f"---\n{fm_text}\n---\n\n"
        context["frontmatter"] = fm
        context["formatted_report"] = f"{fm}{report_body}"
        return context

    @staticmethod
    def _strip_markdown(text: str) -> str:
        cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        cleaned = re.sub(r"[`*_>#]", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _sentences_from_markdown(self, markdown: str, max_sentences: int = 2) -> List[str]:
        lines: List[str] = []
        for raw in markdown.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("|") or re.match(r"^[-:| ]+$", line):
                continue
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+[\.)]\s+", "", line)
            line = self._strip_markdown(line)
            if line:
                lines.append(line)

        text = " ".join(lines)
        if not text:
            return []

        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
        if not parts:
            parts = [text]

        deduped: List[str] = []
        seen = set()
        for p in parts:
            key = p.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(p)
            if len(deduped) >= max_sentences:
                break
        return deduped

    def _extract_key_results(self, key_outcome_text: str) -> List[str]:
        bullets: List[str] = []
        for raw in key_outcome_text.splitlines():
            line = raw.strip()
            if re.match(r"^[-*]\s+", line) or re.match(r"^\d+[\.)]\s+", line):
                line = re.sub(r"^[-*]\s+", "", line)
                line = re.sub(r"^\d+[\.)]\s+", "", line)
                cleaned = self._strip_markdown(line).rstrip(".")
                if len(cleaned) > 18:
                    bullets.append(cleaned)

        preferred = [
            b for b in bullets
            if re.search(r"\d|\bO\(|\blog\b|\bregret\b|\bsample\b|\bbound\b|\bepsilon\b|\bε\b", b, flags=re.IGNORECASE)
        ]
        ordered = preferred + [b for b in bullets if b not in preferred]

        if len(ordered) < 2:
            ordered.extend([s.rstrip(".") for s in self._sentences_from_markdown(key_outcome_text, max_sentences=3)])

        out: List[str] = []
        for item in ordered:
            if item and item not in out:
                out.append(item)
            if len(out) >= 3:
                break
        return out[:3]

    def _extract_confidence_map(self, limits_text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}

        # Table-style lines
        for raw in limits_text.splitlines():
            line = raw.strip()
            if "|" not in line:
                continue
            if re.match(r"^\|?\s*[-: ]+\|", line):
                continue
            cells = [self._strip_markdown(c).strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            label, value = cells[0], cells[1]
            if self._slugify(label) in {"claim", "claim-cluster", "claim-clusters", "confidence"}:
                continue
            conf = self._normalize_confidence_value(value)
            if not conf:
                continue
            key = self._confidence_label_key(label)
            if key:
                out[key] = conf

        # Bullet fallback
        if not out:
            for raw in limits_text.splitlines():
                line = self._strip_markdown(raw)
                if not line:
                    continue
                m = re.match(r"^(.+?)\s*[:\-–]\s*(.+)$", line)
                if not m:
                    continue
                key = self._confidence_label_key(m.group(1))
                conf = self._normalize_confidence_value(m.group(2))
                if key and conf:
                    out[key] = conf

        return out

    @staticmethod
    def _normalize_confidence_value(value: str) -> Optional[str]:
        lowered = value.lower()
        has_high = "high" in lowered
        has_medium = "medium" in lowered
        has_low = "low" in lowered
        if has_medium:
            return "medium"
        if has_high and not has_low:
            return "high"
        if has_low and not has_high:
            return "low"
        if has_high and has_low:
            return "medium"
        return None

    def _confidence_label_key(self, label: str) -> str:
        cleaned = self._strip_markdown(label)
        cleaned = re.sub(r"^\(?\d+\)?\s*", "", cleaned).strip()
        slug = self._slugify(cleaned)
        if not slug:
            return ""
        return "_".join([p for p in slug.split("-") if p][:5])[:48]

    def _extract_limitations(self, limits_text: str) -> List[str]:
        items: List[str] = []
        in_lim_block = False
        for raw in limits_text.splitlines():
            line = raw.strip()
            lowered = line.lower()
            if "major uncertainties" in lowered or "limitations" in lowered:
                in_lim_block = True
                continue
            if in_lim_block and ("confidence" in lowered or "next validation" in lowered or "next checks" in lowered):
                in_lim_block = False
            if not in_lim_block:
                continue
            if re.match(r"^[-*]\s+", line):
                cleaned = self._strip_markdown(re.sub(r"^[-*]\s+", "", line)).rstrip(".")
                if cleaned and cleaned not in items:
                    items.append(cleaned)
            if len(items) >= 3:
                break

        if len(items) < 2:
            for raw in limits_text.splitlines():
                line = raw.strip()
                if not re.match(r"^[-*]\s+", line):
                    continue
                cleaned = self._strip_markdown(re.sub(r"^[-*]\s+", "", line)).rstrip(".")
                if not cleaned:
                    continue
                if re.search(r"limit|uncertain|assum|unknown|risk|may\b", cleaned, flags=re.IGNORECASE):
                    if cleaned not in items:
                        items.append(cleaned)
                if len(items) >= 3:
                    break

        return items[:3]

    def _select_frontmatter_tags(self, context: Dict[str, Any]) -> List[str]:
        raw_tags = context.get("terminology_tags") or []
        meta = context.get("terminology_tag_meta") or {}

        filtered: List[str] = []
        for tag in raw_tags:
            tag_meta = meta.get(tag) or {}
            weight = float(tag_meta.get("weight") or 0.0)
            sources = set(tag_meta.get("sources") or [])
            if "paper_text" in sources and weight >= 0.2:
                filtered.append(tag)

        if not filtered:
            filtered = [
                tag for tag in raw_tags
                if "paper_text" in set((meta.get(tag) or {}).get("sources") or [])
            ]

        if not filtered:
            filtered = list(raw_tags)

        out: List[str] = []
        for tag in filtered:
            if tag not in out:
                out.append(tag)
            if len(out) >= 8:
                break
        return out

    @staticmethod
    def _slugify(text: str) -> str:
        lowered = text.strip().lower()
        lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
        lowered = re.sub(r"-+", "-", lowered).strip("-")
        return lowered

    @staticmethod
    def _norm(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()
