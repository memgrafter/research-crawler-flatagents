from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from flatmachines.locking import ExecutionLock
from flatagents import get_logger

logger = get_logger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteLeaseLock(ExecutionLock):
    """DB-backed execution lock with renewable leases.

    Replaces file locks with rows in v2 executions SQLite DB.
    """

    def __init__(
        self,
        db_path: str,
        owner_id: str,
        phase: str = "machine",
        ttl_seconds: int = 300,
        renew_interval_seconds: int = 100,
    ):
        self.db_path = str(Path(db_path).resolve())
        self.owner_id = owner_id
        self.phase = phase
        self.ttl_seconds = max(int(ttl_seconds), 30)
        self.renew_interval_seconds = max(int(renew_interval_seconds), 5)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._ensure_schema()

        self._op_lock = asyncio.Lock()
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self._heartbeat_stops: Dict[str, asyncio.Event] = {}

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_leases (
                execution_id   TEXT PRIMARY KEY,
                owner_id       TEXT NOT NULL,
                phase          TEXT NOT NULL,
                lease_until    INTEGER NOT NULL,
                fencing_token  INTEGER NOT NULL DEFAULT 1,
                acquired_at    TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_execution_leases_until
              ON execution_leases(lease_until);
            """
        )
        self._conn.commit()

    async def acquire(self, key: str) -> bool:
        now = int(time.time())
        lease_until = now + self.ttl_seconds

        async with self._op_lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """
                    INSERT INTO execution_leases (
                        execution_id, owner_id, phase, lease_until,
                        fencing_token, acquired_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        owner_id = excluded.owner_id,
                        phase = excluded.phase,
                        lease_until = excluded.lease_until,
                        fencing_token = execution_leases.fencing_token + 1,
                        updated_at = excluded.updated_at
                    WHERE execution_leases.lease_until < ?
                       OR execution_leases.owner_id = excluded.owner_id
                    """,
                    (
                        key,
                        self.owner_id,
                        self.phase,
                        lease_until,
                        _utc_now_iso(),
                        _utc_now_iso(),
                        now,
                    ),
                )
                changed = int(self._conn.execute("SELECT changes()").fetchone()[0])
                self._conn.commit()
                acquired = changed > 0
            except Exception:
                self._conn.rollback()
                logger.exception("Lease acquire failed for %s", key)
                return False

        if acquired and key not in self._heartbeat_tasks:
            stop_event = asyncio.Event()
            self._heartbeat_stops[key] = stop_event
            self._heartbeat_tasks[key] = asyncio.create_task(self._heartbeat_loop(key, stop_event))
        return acquired

    async def release(self, key: str) -> None:
        task = self._heartbeat_tasks.pop(key, None)
        stop_event = self._heartbeat_stops.pop(key, None)
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        async with self._op_lock:
            try:
                self._conn.execute(
                    "DELETE FROM execution_leases WHERE execution_id = ? AND owner_id = ?",
                    (key, self.owner_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                logger.exception("Lease release failed for %s", key)

    async def _heartbeat_loop(self, key: str, stop_event: asyncio.Event) -> None:
        try:
            while not stop_event.is_set():
                await asyncio.sleep(self.renew_interval_seconds)
                if stop_event.is_set():
                    break
                ok = await self._renew(key)
                if not ok:
                    logger.warning("Lease heartbeat lost for %s (owner=%s)", key, self.owner_id)
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Lease heartbeat loop failed for %s", key)

    async def _renew(self, key: str) -> bool:
        lease_until = int(time.time()) + self.ttl_seconds
        async with self._op_lock:
            try:
                cur = self._conn.execute(
                    """
                    UPDATE execution_leases
                    SET lease_until = ?,
                        updated_at = ?
                    WHERE execution_id = ?
                      AND owner_id = ?
                    """,
                    (lease_until, _utc_now_iso(), key, self.owner_id),
                )
                self._conn.commit()
                return (cur.rowcount or 0) > 0
            except Exception:
                self._conn.rollback()
                logger.exception("Lease renew failed for %s", key)
                return False
