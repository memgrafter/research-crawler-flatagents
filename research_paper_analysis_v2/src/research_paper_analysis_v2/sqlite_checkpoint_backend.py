from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flatmachines.persistence import PersistenceBackend
from flatagents import get_logger

logger = get_logger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteCheckpointBackend(PersistenceBackend):
    """SQLite-backed checkpoint persistence.

    Stores snapshot blobs and latest pointers in the v2 executions DB.
    Keys follow FlatMachine conventions:
      - "<execution_id>/step_000001_state_exit.json"
      - "<execution_id>/latest"
    """

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._ensure_schema()
        self._op_lock = asyncio.Lock()
        # Execution IDs that reached machine_end in this process; suppress latest-pointer rewrite.
        self._terminalized_execution_ids: set[str] = set()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS machine_checkpoints (
                checkpoint_key TEXT PRIMARY KEY,
                execution_id   TEXT NOT NULL,
                machine_name   TEXT,
                event          TEXT,
                current_state  TEXT,
                snapshot_json  BLOB NOT NULL,
                created_at     TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_mc_execution_created
              ON machine_checkpoints(execution_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS machine_latest (
                execution_id TEXT PRIMARY KEY,
                latest_key   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key or ".." in key or key.startswith("/"):
            raise ValueError(f"Invalid checkpoint key: {key}")

    @staticmethod
    def _execution_id_from_key(key: str) -> str:
        return key.split("/", 1)[0]

    async def save(self, key: str, value: bytes) -> None:
        self._validate_key(key)
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("Checkpoint value must be bytes")

        execution_id = self._execution_id_from_key(key)
        now = _utc_now_iso()

        async with self._op_lock:
            if key.endswith("/latest"):
                # CheckpointManager writes latest pointer after each snapshot save.
                # If this execution just terminalized (machine_end), suppress pointer persistence.
                if execution_id in self._terminalized_execution_ids:
                    self._conn.execute("DELETE FROM machine_latest WHERE execution_id = ?", (execution_id,))
                    self._conn.commit()
                    return

                latest_key = bytes(value).decode("utf-8").strip()
                self._validate_key(latest_key)
                self._conn.execute(
                    """
                    INSERT INTO machine_latest (execution_id, latest_key, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        latest_key = excluded.latest_key,
                        updated_at = excluded.updated_at
                    """,
                    (execution_id, latest_key, now),
                )
                self._conn.commit()
                return

            machine_name = None
            event = None
            current_state = None
            try:
                snap = json.loads(bytes(value).decode("utf-8"))
                machine_name = snap.get("machine_name")
                event = snap.get("event")
                current_state = snap.get("current_state")
            except Exception:
                # Preserve raw bytes even if payload is not JSON.
                pass

            if event == "machine_end":
                # Terminal policy: drop checkpoint history and latest pointer for this execution.
                self._conn.execute("DELETE FROM machine_checkpoints WHERE execution_id = ?", (execution_id,))
                self._conn.execute("DELETE FROM machine_latest WHERE execution_id = ?", (execution_id,))
                self._conn.commit()
                self._terminalized_execution_ids.add(execution_id)
                return

            # New non-terminal checkpoint for this execution: allow latest pointer updates again.
            self._terminalized_execution_ids.discard(execution_id)

            self._conn.execute(
                """
                INSERT INTO machine_checkpoints (
                    checkpoint_key, execution_id, machine_name, event, current_state, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    execution_id = excluded.execution_id,
                    machine_name = excluded.machine_name,
                    event = excluded.event,
                    current_state = excluded.current_state,
                    snapshot_json = excluded.snapshot_json,
                    created_at = excluded.created_at
                """,
                (key, execution_id, machine_name, event, current_state, sqlite3.Binary(bytes(value)), now),
            )
            self._conn.commit()

    async def load(self, key: str) -> Optional[bytes]:
        self._validate_key(key)
        execution_id = self._execution_id_from_key(key)

        async with self._op_lock:
            if key.endswith("/latest"):
                row = self._conn.execute(
                    "SELECT latest_key FROM machine_latest WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                if not row:
                    return None
                return str(row["latest_key"]).encode("utf-8")

            row = self._conn.execute(
                "SELECT snapshot_json FROM machine_checkpoints WHERE checkpoint_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None

            payload = row["snapshot_json"]
            if payload is None:
                return None
            return bytes(payload)

    async def delete(self, key: str) -> None:
        self._validate_key(key)
        execution_id = self._execution_id_from_key(key)

        async with self._op_lock:
            if key.endswith("/latest"):
                self._conn.execute("DELETE FROM machine_latest WHERE execution_id = ?", (execution_id,))
                self._conn.commit()
                return

            self._conn.execute("DELETE FROM machine_checkpoints WHERE checkpoint_key = ?", (key,))
            self._conn.execute(
                "DELETE FROM machine_latest WHERE execution_id = ? AND latest_key = ?",
                (execution_id, key),
            )
            self._conn.commit()
