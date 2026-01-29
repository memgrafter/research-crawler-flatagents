"""
Distributed worker hooks for research_paper_analysis.

Combines SDK DistributedWorkerHooks for worker lifecycle with
custom paper_queue operations for the arxiv crawler database.

Uses the arxiv_crawler SQLite database with the worker_registry_migration.sql schema.
"""

from __future__ import annotations

import os
import socket
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flatagents import (
    DistributedWorkerHooks,
    SQLiteRegistrationBackend,
    SQLiteWorkBackend,
    get_logger,
)

from .hooks import JsonValidationHooks

logger = get_logger(__name__)

# Default DB path (relative to research_crawler root)
# Path: distributed_hooks.py -> research_paper_analysis -> src -> python -> research_paper_analysis -> research_crawler
DEFAULT_DB_PATH = str(
    Path(__file__).parent.parent.parent.parent.parent / 
    "arxiv_crawler" / "data" / "arxiv.sqlite"
)


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
            "complete_paper": self._complete_paper,
            "fail_paper": self._fail_paper,
            "list_stale_workers": self._list_stale_workers,
            "reap_worker": self._reap_worker,
        }
        
        # Delegate to custom handler if exists
        handler = paper_handlers.get(action)
        if handler:
            return await handler(context)
        
        # Delegate to SDK hooks for standard actions
        # (register_worker, deregister_worker, heartbeat, calculate_spawn, spawn_workers)
        sdk_actions = {
            "register_worker", "deregister_worker", "heartbeat",
            "calculate_spawn", "spawn_workers",
        }
        if action in sdk_actions:
            return await self._sdk_hooks.on_action(action, context)
        
        # Fall through to default behavior
        return context
    
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
            
            context["queue_depth"] = queue_depth
            context["active_workers"] = active_workers
            return context
    
    async def _calculate_spawn(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate how many workers to spawn based on queue depth and limits."""
        queue_depth = int(context.get("queue_depth", 0))
        active_workers = int(context.get("active_workers", 0))
        max_workers = int(context.get("max_workers", 3))
        
        # Workers needed = min(queue_depth, max_workers)
        workers_needed = min(queue_depth, max_workers)
        
        # Workers to spawn = max(workers_needed - active_workers, 0)
        workers_to_spawn = max(workers_needed - active_workers, 0)
        
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
        from flatagents.actions import launch_machine
        
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
    
    async def _complete_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Mark paper analysis as complete."""
        queue_id = context.get("queue_id")
        summary_path = context.get("summary_path")
        
        if not queue_id:
            raise ValueError("queue_id is required for complete_paper")
        
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
    
    async def _fail_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Mark paper as failed. Will retry or poison based on attempts."""
        queue_id = context.get("queue_id")
        error = context.get("error")
        
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
                
                if attempts >= max_retries:
                    # Poison the job
                    cursor.execute("""
                        UPDATE paper_queue
                        SET status = 'poisoned',
                            finished_at = ?,
                            error = ?,
                            attempts = ?,
                            claimed_by = NULL
                        WHERE id = ?
                    """, (now, error, attempts, queue_id))
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
                    """, (error, attempts, queue_id))
                
                conn.commit()
            
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
