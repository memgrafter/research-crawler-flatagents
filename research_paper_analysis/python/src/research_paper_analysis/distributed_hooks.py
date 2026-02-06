"""
Distributed worker hooks for research_paper_analysis.

Combines SDK DistributedWorkerHooks for worker lifecycle with
custom paper_queue operations for the arxiv crawler database.

Uses the arxiv_crawler SQLite database with the worker_registry_migration.sql schema.
"""

from __future__ import annotations

import os
import re
import socket
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pypdf import PdfReader

from flatmachines import (
    DistributedWorkerHooks,
    SQLiteRegistrationBackend,
    SQLiteWorkBackend,
)
from flatagents import get_logger

from .hooks import JsonValidationHooks

logger = get_logger(__name__)

# Default DB path (relative to research_crawler root)
# Path: distributed_hooks.py -> research_paper_analysis -> src -> python -> research_paper_analysis -> research_crawler
DEFAULT_DB_PATH = str(
    Path(__file__).parent.parent.parent.parent.parent / 
    "arxiv_crawler" / "data" / "arxiv.sqlite"
)

# Data directory for reports (same location as main.py uses)
DATA_DIR = Path(__file__).parent.parent.parent.parent.parent / "research_paper_analysis" / "data"


def slugify_title(value: str) -> str:
    """Create a filesystem-safe slug from a title."""
    cleaned = re.sub(r'[^A-Za-z0-9]+', '-', value).strip('-').lower()
    return cleaned or "paper"


class DistributedPaperAnalysisHooks(JsonValidationHooks):
    """
    Distributed hooks for paper analysis workers.
    
    Extends JsonValidationHooks (for analyzer) and composes SDK
    DistributedWorkerHooks (for worker lifecycle).
    
    Has custom paper_queue operations since paper_queue schema
    differs from the generic SDK work pool schema.
    """
    
    def __init__(self, db_path: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path or os.environ.get("ARXIV_DB_PATH", DEFAULT_DB_PATH)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        
        # Compose SDK hooks for worker lifecycle (uses separate tables)
        self._sdk_hooks = DistributedWorkerHooks(
            registration=SQLiteRegistrationBackend(db_path=self.db_path),
            work=SQLiteWorkBackend(db_path=self.db_path),
        )
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    async def on_action(self, action: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Route action names to handler methods."""
        
        # Actions with custom paper_queue logic
        paper_handlers = {
            "get_pool_state": self._get_pool_state,
            "calculate_spawn": self._calculate_spawn,
            "spawn_workers": self._spawn_workers,
            "claim_paper": self._claim_paper,
            "prepare_paper": self._prepare_paper,
            "complete_paper": self._complete_paper,
            "fail_paper": self._fail_paper,
            "deregister_worker": self._deregister_worker,
            "list_stale_workers": self._list_stale_workers,
            "reap_worker": self._reap_worker,
            "reap_workers": self._reap_workers,
        }
        
        # Delegate to custom handler if exists
        handler = paper_handlers.get(action)
        if handler:
            return await handler(context)
        
        # Delegate to SDK hooks for standard actions
        # (register_worker, heartbeat, calculate_spawn, spawn_workers)
        # Note: deregister_worker is handled locally to add scaling trigger
        sdk_actions = {
            "register_worker", "heartbeat",
            "calculate_spawn", "spawn_workers",
        }
        if action in sdk_actions:
            return await self._sdk_hooks.on_action(action, context)

        # Fall through to default behavior
        return await super().on_action(action, context)
    
    # -------------------------------------------------------------------------
    # Pool State (custom for paper_queue schema)
    # -------------------------------------------------------------------------
    
    async def _get_pool_state(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Get queue depth and active worker count."""
        async with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Count pending papers
            cursor.execute(
                "SELECT COUNT(*) FROM paper_queue WHERE status = 'pending'"
            )
            queue_depth = cursor.fetchone()[0]
            
            # Count active workers
            cursor.execute(
                "SELECT COUNT(*) FROM worker_registry WHERE status = 'active'"
            )
            active_workers = cursor.fetchone()[0]

            # Count suspended workers
            cursor.execute(
                "SELECT COUNT(*) FROM worker_registry WHERE status = 'suspended'"
            )
            suspended_workers = cursor.fetchone()[0]
            
            context["queue_depth"] = queue_depth
            context["active_workers"] = active_workers
            context["suspended_workers"] = suspended_workers
            return context
    
    async def _calculate_spawn(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate how many workers to spawn based on queue depth and limits."""
        queue_depth = int(context.get("queue_depth", 0))
        active_workers = int(context.get("active_workers", 0))
        max_workers = int(context.get("max_workers", 3))
        suspended_workers = int(context.get("suspended_workers", 0))

        effective_max = max(max_workers - suspended_workers, 0)
        
        # Workers needed = min(queue_depth, effective_max)
        workers_needed = min(queue_depth, effective_max)
        
        # Workers to spawn = max(workers_needed - active_workers, 0)
        workers_to_spawn = max(workers_needed - active_workers, 0)
        
        context["effective_max_workers"] = effective_max
        context["workers_needed"] = workers_needed
        context["workers_to_spawn"] = workers_to_spawn
        # Provide a real list for foreach iteration (Jinja range() yields string)
        context["worker_indices"] = list(range(workers_to_spawn))
        return context
    
    async def _spawn_workers(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Spawn worker subprocesses for paper analysis.
        
        Uses absolute path resolution for the worker config.
        """
        import uuid
        from flatmachines.actions import launch_machine
        
        workers_to_spawn = int(context.get("workers_to_spawn", 0))
        
        # Resolve worker config path (relative to research_paper_analysis/config/)
        # distributed_hooks.py -> research_paper_analysis -> src -> python -> research_paper_analysis
        config_dir = Path(__file__).parent.parent.parent.parent / "config"
        worker_config = config_dir / "paper_analysis_worker.yml"
        
        if not worker_config.exists():
            raise ValueError(f"Worker config not found: {worker_config}")
        
        spawned_ids = []
        for i in range(workers_to_spawn):
            worker_id = f"paper-worker-{uuid.uuid4().hex[:8]}"
            
            # Pre-register worker so get_pool_state sees it immediately,
            # closing the race window between spawn and register_worker.
            # The worker's own register_worker does INSERT OR REPLACE,
            # so this is safe — it just updates with real PID/host.
            async with self._lock:
                conn = self._get_conn()
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO worker_registry
                    (worker_id, status, last_heartbeat, started_at)
                    VALUES (?, 'active', ?, ?)
                    """,
                    (worker_id, now, now),
                )
                conn.commit()
            
            # Launch worker in subprocess
            launch_machine(
                machine_config=str(worker_config),
                input_data={
                    "worker_id": worker_id,
                },
            )
            
            spawned_ids.append(worker_id)
            logger.info(f"Spawned paper analysis worker: {worker_id}")
        
        context["spawned_ids"] = spawned_ids
        context["spawned_count"] = len(spawned_ids)
        return context
    
    # -------------------------------------------------------------------------
    # Paper Queue Operations (custom schema)
    # -------------------------------------------------------------------------
    
    async def _claim_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically claim next pending paper."""
        worker_id = context.get("worker_id")
        if not worker_id:
            raise ValueError("worker_id is required for claim_paper")
        
        async with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            
            # Find and claim next pending paper (atomic with transaction)
            cursor.execute("""
                UPDATE paper_queue
                SET status = 'processing',
                    worker = ?,
                    claimed_by = ?,
                    claimed_at = ?,
                    started_at = ?
                WHERE id = (
                    SELECT id FROM paper_queue 
                    WHERE status = 'pending'
                    ORDER BY priority DESC, enqueued_at ASC
                    LIMIT 1
                )
                RETURNING id, paper_id
            """, (worker_id, worker_id, now, now))
            
            row = cursor.fetchone()
            conn.commit()
            
            if row:
                queue_id = row[0]
                paper_id = row[1]
                
                # Get paper details
                cursor.execute("""
                    SELECT arxiv_id, title, authors, abstract
                    FROM papers WHERE id = ?
                """, (paper_id,))
                paper = cursor.fetchone()
                
                if paper:
                    context["queue_id"] = queue_id
                    context["paper_id"] = paper_id
                    context["paper"] = {
                        "arxiv_id": paper[0],
                        "title": paper[1],
                        "authors": paper[2],
                        "abstract": paper[3],
                    }
                    return context
            
            context["queue_id"] = None
            context["paper_id"] = None
            context["paper"] = None
            return context
    
    async def _prepare_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Download and parse PDF to prepare full input for analysis."""
        paper = context.get("paper") or {}
        arxiv_id = paper.get("arxiv_id")
        
        if not arxiv_id:
            context["prepared"] = False
            context["error"] = "No arxiv_id in paper context"
            return context
        
        try:
            # Download PDF
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            pdf_path = DATA_DIR / f"{arxiv_id.replace('/', '_')}.pdf"
            txt_path = DATA_DIR / f"{arxiv_id.replace('/', '_')}.txt"
            
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            
            if not pdf_path.exists():
                logger.info(f"Downloading paper: {pdf_url}")
                response = httpx.get(pdf_url, follow_redirects=True, timeout=60.0)
                response.raise_for_status()
                pdf_path.write_bytes(response.content)
            
            # Extract text from PDF
            if txt_path.exists():
                full_text = txt_path.read_text()
            else:
                logger.info(f"Extracting text from: {pdf_path}")
                reader = PdfReader(pdf_path)
                pages = []
                for i, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    pages.append(f"[PAGE {i+1}]\n{text}")
                full_text = "\n\n".join(pages)
                txt_path.write_text(full_text)
            
            # Parse sections (simplified version of main.py logic)
            section_pattern = r'\n(\d+(?:\.\d+)?)\s+([A-Z][^\n]{3,60})\n'
            section_matches = list(re.finditer(section_pattern, full_text))
            
            sections = []
            for i, match in enumerate(section_matches[:6]):  # First 6 sections
                section_num = match.group(1)
                section_title = match.group(2).strip()
                start_pos = match.end()
                
                if i + 1 < len(section_matches):
                    end_pos = section_matches[i + 1].start()
                else:
                    ref_match = re.search(r'\nReferences\s*\n', full_text[start_pos:])
                    end_pos = start_pos + ref_match.start() if ref_match else len(full_text)
                
                content = full_text[start_pos:end_pos].strip()[:3000]
                sections.append(f"=== {section_num} {section_title} ===\n{content}")
            
            section_text = "\n\n".join(sections)
            
            # Extract references count
            ref_match = re.search(r'\nReferences\s*\n(.*)', full_text, re.DOTALL | re.IGNORECASE)
            references = []
            if ref_match:
                ref_text = ref_match.group(1)
                refs = re.split(r'\n\s*\[?\d+\]?\s*', ref_text)
                references = [r.strip()[:200] for r in refs if len(r.strip()) > 20][:40]
            
            # Update context with prepared data
            context["section_text"] = section_text
            context["reference_count"] = len(references)
            context["references_sample"] = references[:10]
            context["prepared"] = True
            
            logger.info(f"Prepared paper {arxiv_id}: {len(sections)} sections, {len(references)} refs")
            
        except Exception as e:
            logger.error(f"Failed to prepare paper {arxiv_id}: {e}")
            context["prepared"] = False
            context["error"] = str(e)
        
        return context
    
    async def _complete_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Mark paper analysis as complete and write report to disk."""
        queue_id = context.get("queue_id")
        paper = context.get("paper") or {}
        analysis_result = context.get("analysis_result") or {}
        
        if not queue_id:
            raise ValueError("queue_id is required for complete_paper")
        
        # Write report to disk if we have formatted_report from machine.yml
        summary_path = context.get("summary_path")
        formatted_report = analysis_result.get("formatted_report")
        
        if not summary_path and formatted_report:
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                
                title = paper.get("title") or analysis_result.get("title") or "Untitled"
                arxiv_id = paper.get("arxiv_id", "unknown")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                title_slug = slugify_title(title)
                arxiv_prefix = arxiv_id.replace("/", "_")
                filename = f"{arxiv_prefix}_{title_slug}_{timestamp}.md"
                file_path = DATA_DIR / filename
                
                file_path.write_text(formatted_report)
                
                summary_path = str(file_path)
                context["summary_path"] = summary_path
                logger.info(f"Wrote report to {summary_path}")
                
            except Exception as e:
                logger.error(f"Failed to write report file: {e}")

        async with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            
            cursor.execute("""
                UPDATE paper_queue
                SET status = 'done',
                    finished_at = ?,
                    summary_path = ?
                WHERE id = ?
            """, (now, summary_path, queue_id))
            conn.commit()
            
            return context

    @staticmethod
    def _collect_error_details(context: Dict[str, Any]) -> str:
        parts: List[str] = []

        def add_part(value: Optional[str]) -> None:
            if value and value not in parts:
                parts.append(value)

        # flatmachines stores exceptions in last_error/last_error_type
        raw_error = context.get("error") or context.get("last_error")
        if raw_error:
            add_part(str(raw_error))
        last_error_type = context.get("last_error_type")
        if last_error_type:
            add_part(f"error_type={last_error_type}")

        analysis_result = context.get("analysis_result") or {}
        if not isinstance(analysis_result, dict):
            analysis_result = {}

        for key in ("error", "json_error"):
            value = analysis_result.get(key)
            if value:
                add_part(str(value))

        llm_error = analysis_result.get("llm_error") or context.get("llm_error")
        if llm_error:
            add_part(str(llm_error))

        llm_error_type = analysis_result.get("llm_error_type") or context.get("llm_error_type")
        if llm_error_type:
            add_part(f"llm_error_type={llm_error_type}")

        status_code = analysis_result.get("llm_error_status_code")
        if status_code is None:
            status_code = context.get("llm_error_status_code")
        if status_code is not None:
            add_part(f"status_code={status_code}")

        llm_headers = analysis_result.get("llm_error_headers") or context.get("llm_error_headers")
        if llm_headers:
            try:
                header_text = json.dumps(llm_headers, sort_keys=True)
            except TypeError:
                header_text = str(llm_headers)
            add_part(f"llm_error_headers={header_text}")

        return " | ".join(parts)

    @staticmethod
    def _is_retryable_failure(error_text: str, context: Dict[str, Any]) -> bool:
        if context.get("rate_limit_cause"):
            return True

        status_code = context.get("llm_error_status_code")
        if status_code is None:
            analysis_result = context.get("analysis_result") or {}
            if isinstance(analysis_result, dict):
                status_code = analysis_result.get("llm_error_status_code")
        if isinstance(status_code, int):
            if status_code >= 500 or status_code in {401, 403, 408, 429}:
                return True

        if not error_text:
            return False

        lowered = error_text.lower()
        status_codes = {
            int(code) for code in re.findall(r"\b([4-5]\d{2})\b", lowered)
        }
        if any(code >= 500 for code in status_codes):
            return True
        if status_codes.intersection({401, 403, 408, 429}):
            return True

        retryable_phrases = (
            "rate limit",
            "too many requests",
            "overloaded",
            "service unavailable",
            "gateway timeout",
            "bad gateway",
            "internal server error",
            "timeout",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "authentication",
        )
        return any(phrase in lowered for phrase in retryable_phrases)
    
    async def _fail_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Mark paper as failed. Will retry or poison based on attempts."""
        queue_id = context.get("queue_id")
        error_details = self._collect_error_details(context)
        if not error_details:
            error_details = str(context.get("error") or context.get("last_error") or "unknown error")
        retryable_failure = self._is_retryable_failure(error_details, context)
        
        if not queue_id:
            raise ValueError("queue_id is required for fail_paper")
        
        async with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            
            # Get current attempts and max_retries
            cursor.execute("""
                SELECT attempts, max_retries FROM paper_queue WHERE id = ?
            """, (queue_id,))
            row = cursor.fetchone()
            
            if row:
                attempts = (row[0] or 0) + 1
                max_retries = row[1] or 3
                
                if attempts >= max_retries and not retryable_failure:
                    # Poison the job
                    cursor.execute("""
                        UPDATE paper_queue
                        SET status = 'poisoned',
                            finished_at = ?,
                            error = ?,
                            attempts = ?,
                            claimed_by = NULL
                        WHERE id = ?
                    """, (now, error_details, attempts, queue_id))
                else:
                    # Return to pending for retry
                    cursor.execute("""
                        UPDATE paper_queue
                        SET status = 'pending',
                            error = ?,
                            attempts = ?,
                            claimed_by = NULL,
                            claimed_at = NULL
                        WHERE id = ?
                    """, (error_details, attempts, queue_id))
                
                conn.commit()
            
            return context
    
    async def _deregister_worker(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Deregister worker and trigger scaling event."""
        worker_id = context.get("worker_id")
        
        # Call SDK deregister
        context = await self._sdk_hooks.on_action("deregister_worker", context)
        
        # Insert scaling event to trigger worker replenishment
        async with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO scaling_events (event_type, worker_id) VALUES (?, ?)",
                ("worker_done", worker_id)
            )
            conn.commit()
            logger.info(f"Triggered scaling event for worker {worker_id}")
        
        return context
    
    # -------------------------------------------------------------------------
    # Stale Worker Reaper (custom for paper_queue release)
    # -------------------------------------------------------------------------
    
    async def _list_stale_workers(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Find workers past heartbeat threshold."""
        threshold_seconds = context.get("stale_threshold_seconds", 60)
        
        async with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Find workers whose last_heartbeat is older than threshold
            cursor.execute("""
                SELECT worker_id, host, last_heartbeat
                FROM worker_registry
                WHERE status = 'active'
                AND datetime(last_heartbeat) < datetime('now', ? || ' seconds')
            """, (-threshold_seconds,))
            
            workers = [
                {
                    "worker_id": row[0],
                    "host": row[1],
                    "last_heartbeat": row[2],
                }
                for row in cursor.fetchall()
            ]
        
        context["workers"] = workers
        context["stale_workers"] = workers
        context["stale_count"] = len(workers)
        return context
    
    async def _reap_worker(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Mark worker as lost and release their papers."""
        worker = context.get("worker")
        if not worker:
            raise ValueError("worker is required for reap_worker")
        
        worker_id = worker.get("worker_id")
        
        async with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Mark worker as lost
            cursor.execute("""
                UPDATE worker_registry SET status = 'lost' WHERE worker_id = ?
            """, (worker_id,))
            
            # Release papers claimed by this worker
            cursor.execute("""
                UPDATE paper_queue
                SET status = 'pending',
                    claimed_by = NULL,
                    claimed_at = NULL,
                    worker = NULL
                WHERE claimed_by = ? AND status = 'processing'
            """, (worker_id,))
            
            released = cursor.rowcount
            conn.commit()
        
        context["reaped_worker_id"] = worker_id
        context["papers_released"] = released
        return context

    async def _reap_workers(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Reap all stale workers listed in context."""
        workers = context.get("stale_workers") or []
        reaped_ids: List[str] = []
        released_total = 0

        for worker in workers:
            if not worker:
                continue
            result = await self._reap_worker({"worker": worker})
            worker_id = result.get("reaped_worker_id")
            if worker_id:
                reaped_ids.append(worker_id)
            released_total += int(result.get("papers_released") or 0)

        context["reaped_workers"] = reaped_ids
        context["reaped_count"] = len(reaped_ids)
        context["released_total"] = released_total
        return context
