from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

    _ALLOWED_TERMINAL_POLICIES = {
        "keep_all",
        "purge_all",
        "keep_failed",
        "keep_wrap_failed",
    }

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._ensure_schema()
        self._op_lock = asyncio.Lock()

        # Terminal retention policy:
        # - keep_all: retain all terminal snapshots/history (legacy behavior)
        # - purge_all: purge all terminal snapshots/history
        # - keep_failed: retain terminal snapshot only for failed executions
        # - keep_wrap_failed: retain terminal snapshot only for wrap-pipeline failures
        self._terminal_checkpoint_policy = self._resolve_terminal_checkpoint_policy()

        # Execution IDs whose terminal snapshot was purged in this process. Used to
        # suppress subsequent "/latest" pointer persistence for that terminal event.
        self._terminalized_execution_ids: set[str] = set()

        logger.info("Checkpoint terminal retention policy: %s", self._terminal_checkpoint_policy)

    def _resolve_terminal_checkpoint_policy(self) -> str:
        raw_policy = os.environ.get("RPA_V2_TERMINAL_CHECKPOINT_POLICY", "").strip().lower()
        aliases = {
            "all": "keep_all",
            "keep": "keep_all",
            "purge": "purge_all",
            "none": "purge_all",
            "failed": "keep_failed",
            "wrap_failed": "keep_wrap_failed",
            "wrapping_failed": "keep_wrap_failed",
        }

        if raw_policy:
            policy = aliases.get(raw_policy, raw_policy)
            if policy in self._ALLOWED_TERMINAL_POLICIES:
                return policy
            logger.warning(
                "Unknown RPA_V2_TERMINAL_CHECKPOINT_POLICY=%r; defaulting to keep_wrap_failed",
                raw_policy,
            )
            return "keep_wrap_failed"

        # Backward compatibility with old boolean flag.
        legacy_purge = (
            os.environ.get("RPA_V2_PURGE_TERMINAL_CHECKPOINTS", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if legacy_purge:
            return "purge_all"

        # New default: keep only wrap failures at terminal state to control DB growth
        # while preserving the key warm-start/debugging use-case.
        return "keep_wrap_failed"

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

    @staticmethod
    def _snapshot_has_error(snapshot: Optional[dict[str, Any]]) -> bool:
        if not isinstance(snapshot, dict):
            return False

        output = snapshot.get("output")
        if isinstance(output, dict):
            err = output.get("error")
            if isinstance(err, str):
                if err.strip():
                    return True
            elif err is not None:
                return True

        context = snapshot.get("context")
        if isinstance(context, dict):
            err = context.get("last_error")
            if isinstance(err, str):
                if err.strip():
                    return True
            elif err is not None:
                return True

        return False

    def _should_keep_terminal_snapshot(self, machine_name: Optional[str], snapshot: Optional[dict[str, Any]]) -> bool:
        policy = self._terminal_checkpoint_policy
        if policy == "keep_all":
            return True
        if policy == "purge_all":
            return False

        is_failed = self._snapshot_has_error(snapshot)
        if policy == "keep_failed":
            return is_failed
        if policy == "keep_wrap_failed":
            return is_failed and machine_name == "wrap-pipeline"

        return False

    def _upsert_checkpoint_row(
        self,
        key: str,
        execution_id: str,
        machine_name: Optional[str],
        event: Optional[str],
        current_state: Optional[str],
        value: bytes,
        now: str,
    ) -> None:
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

    async def save(self, key: str, value: bytes) -> None:
        self._validate_key(key)
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("Checkpoint value must be bytes")

        execution_id = self._execution_id_from_key(key)
        now = _utc_now_iso()

        async with self._op_lock:
            if key.endswith("/latest"):
                # CheckpointManager writes latest pointer after each snapshot save.
                # Suppress pointer persistence when terminal snapshot was purged.
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
            snapshot: Optional[dict[str, Any]] = None
            try:
                snapshot = json.loads(bytes(value).decode("utf-8"))
                if isinstance(snapshot, dict):
                    machine_name = snapshot.get("machine_name")
                    event = snapshot.get("event")
                    current_state = snapshot.get("current_state")
            except Exception:
                # Preserve raw bytes even if payload is not JSON.
                snapshot = None

            if event is None and "machine_end" in key:
                event = "machine_end"

            if event == "machine_end" and self._terminal_checkpoint_policy != "keep_all":
                keep_terminal = self._should_keep_terminal_snapshot(machine_name, snapshot)

                # Always clear previous history at termination for non-keep_all policies.
                self._conn.execute("DELETE FROM machine_checkpoints WHERE execution_id = ?", (execution_id,))
                self._conn.execute("DELETE FROM machine_latest WHERE execution_id = ?", (execution_id,))

                if not keep_terminal:
                    self._conn.commit()
                    self._terminalized_execution_ids.add(execution_id)
                    return

                # Keep exactly one terminal snapshot row.
                self._terminalized_execution_ids.discard(execution_id)
                self._upsert_checkpoint_row(
                    key=key,
                    execution_id=execution_id,
                    machine_name=machine_name,
                    event=event,
                    current_state=current_state,
                    value=bytes(value),
                    now=now,
                )
                self._conn.commit()
                return

            # Any non-purged checkpoint allows latest pointer updates.
            self._terminalized_execution_ids.discard(execution_id)

            self._upsert_checkpoint_row(
                key=key,
                execution_id=execution_id,
                machine_name=machine_name,
                event=event,
                current_state=current_state,
                value=bytes(value),
                now=now,
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
