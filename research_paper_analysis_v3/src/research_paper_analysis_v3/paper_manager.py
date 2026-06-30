"""Paper Machine Manager — listener_os pattern for v3.

Implements the core orchestration: paper machines that park on wait_for,
sub-machines that signal via send_and_notify, OS-activated dispatcher.

Paper machine phases (tracked in FlatMachines checkpoints):
  pending → ranked → extracting → extracted → digesting → done

Signal channels:
  paper/{arxiv_id} — per-paper wakeup when sub-machine completes
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flatmachines import (
    FileTrigger,
    LoggingHooks,
    SQLiteCheckpointBackend,
    SQLiteSignalBackend,
    send_and_notify,
)
from flatagents import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Per-paper signal channel prefix
# ---------------------------------------------------------------------------
PAPER_CHANNEL_PREFIX = "paper/"

# ---------------------------------------------------------------------------
# Paper state transitions (stored in checkpoint current_state)
# ---------------------------------------------------------------------------
VALID_STATES = (
    "pending",
    "ranked",
    "extracting",
    "extracted",
    "digesting",
    "done",
    "failed",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paper_channel(arxiv_id: str) -> str:
    return f"{PAPER_CHANNEL_PREFIX}{arxiv_id}"


def _get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get a SQLite connection to the v3 database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _ensure_migration(db_path: str) -> None:
    """Add generated columns to machine_checkpoints for queryable paper metadata.

    FlatMachines SDK auto-creates the base tables via _ensure_schema().
    We add generated columns so fmr_score, docling_json_path, etc. are indexable.
    Idempotent — safe to call multiple times. Skips if table doesn't exist yet
    (SDK creates tables lazily on first machine run).
    """
    conn = _get_db_connection(db_path)
    try:
        # Check if table exists; SDK creates it lazily
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='machine_checkpoints'"
        ).fetchone()
        if not exists:
            logger.debug("machine_checkpoints not yet created by SDK — migration deferred")
            return

        # Generated columns for queryable paper metadata.
        # VIRTUAL (not STORED) — SQLite won't allow adding STORED columns
        # to tables that already have rows (SDK writes checkpoints during execution).
        try:
            conn.execute(
                "ALTER TABLE machine_checkpoints "
                "ADD COLUMN fmr_score REAL "
                "GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.fmr_score')) VIRTUAL"
            )
            logger.info("Added generated column: fmr_score")
        except sqlite3.OperationalError as e:
            logger.debug("fmr_score column: %s", e)

        try:
            conn.execute(
                "ALTER TABLE machine_checkpoints "
                "ADD COLUMN docling_json_path TEXT "
                "GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.docling_json_path')) VIRTUAL"
            )
            logger.info("Added generated column: docling_json_path")
        except sqlite3.OperationalError as e:
            logger.debug("docling_json_path column: %s", e)

        try:
            conn.execute(
                "ALTER TABLE machine_checkpoints "
                "ADD COLUMN digest_path TEXT "
                "GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.digest_path')) VIRTUAL"
            )
            logger.info("Added generated column: digest_path")
        except sqlite3.OperationalError as e:
            logger.debug("digest_path column: %s", e)

        # Indexes for efficient queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fmr ON machine_checkpoints(fmr_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_docling ON machine_checkpoints(docling_json_path)")
        logger.info("v3 DB migration complete")
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Paper Manager Hooks
# ---------------------------------------------------------------------------

class PaperManagerHooks(LoggingHooks):
    """Hooks for the paper machine manager.

    Uses the listener_os pattern:
    - Paper machines park on wait_for when waiting for sub-machine completion
    - Sub-machines signal via send_and_notify when they complete
    - OS-activated dispatcher resumes parked machines
    """

    def __init__(
        self,
        db_path: str,
        trigger_base: Optional[str] = None,
        project_root: Optional[Path] = None,
    ):
        super().__init__()
        self._db_path = db_path
        self._project_root = project_root or Path(db_path).parent.parent
        self._trigger_base = trigger_base or str(self._project_root / "data" / "trigger")

        # Migration runs lazily on first query — SDK creates tables
        # when FlatMachine is constructed, which happens after this __init__
        self._migration_done = False

        # Signal/trigger backends for send_and_notify
        self._signal_backend = SQLiteSignalBackend(db_path=self._db_path)
        self._trigger_backend = FileTrigger(base_path=self._trigger_base)

    # ------------------------------------------------------------------
    # Action router
    # ------------------------------------------------------------------

    async def on_action(
        self, state_name: str, action_name: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        handlers = {
            "check_current_phase_status": self._check_current_phase_status,
            "mark_phase_failed": self._mark_phase_failed,
            "send_completion_signal": self._send_completion_signal,
        }
        handler = handlers.get(action_name)
        if handler:
            return await handler(context)
        return await super().on_action(state_name, action_name, context)

    # ------------------------------------------------------------------
    # Phase status checking (reads from FlatMachines checkpoints)
    # ------------------------------------------------------------------

    async def _check_current_phase_status(
        self, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check if the current phase sub-machine has completed.

        Reads paper state from FlatMachines checkpoints (machine_latest + machine_checkpoints).
        """
        arxiv_id = self._norm(context.get("paper_id"))
        if not arxiv_id:
            logger.warning("No paper_id in context for check_current_phase_status")
            return context

        # Ensure migration has run (lazy, after SDK creates tables)
        if not self._migration_done:
            _ensure_migration(self._db_path)
            self._migration_done = True

        # Read latest checkpoint for this execution (paper machine)
        conn = _get_db_connection(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT mc.current_state, mc.snapshot_json
                FROM machine_latest ml
                JOIN machine_checkpoints mc ON ml.latest_key = mc.checkpoint_key
                WHERE ml.execution_id = ?
                """,
                (arxiv_id,),  # execution_id == paper_id for paper machines
            ).fetchone()
        finally:
            conn.close()

        if not row:
            logger.warning("No checkpoint found for paper %s", arxiv_id)
            return context

        # Update context with paper state from checkpoint
        context["phase"] = row["current_state"]

        # Parse snapshot_json to extract phase-specific fields
        import json
        try:
            snapshot = json.loads(row["snapshot_json"])
            if "fmr_score" in snapshot:
                context["fmr_score"] = float(snapshot["fmr_score"])
                context["ranking_complete"] = True
            if "docling_json_path" in snapshot:
                context["docling_json_path"] = snapshot["docling_json_path"]
                context["extraction_complete"] = True
            if "digest_path" in snapshot:
                context["digest_path"] = snapshot["digest_path"]
                context["digest_complete"] = True
        except (json.JSONDecodeError, TypeError):
            logger.debug("Could not parse snapshot_json for paper %s", arxiv_id)

        logger.info(
            "Paper %s: state=%s, fmr_score=%s, docling=%s, digest=%s",
            arxiv_id,
            row["current_state"],
            context.get("fmr_score"),
            bool(context.get("docling_json_path")),
            bool(context.get("digest_path")),
        )
        return context

    # ------------------------------------------------------------------
    # Phase failure handling (writes to FlatMachines checkpoints)
    # ------------------------------------------------------------------

    async def _mark_phase_failed(
        self, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Mark the current phase as failed and signal any waiting machines.

        The paper machine's checkpoint is updated by FlatMachines automatically
        when it transitions to the 'failed' state. We just need to signal
        any waiting machines via the signal system.
        """
        arxiv_id = self._norm(context.get("paper_id"))
        if not arxiv_id:
            logger.warning("No paper_id in context for mark_phase_failed")
            return context

        error = self._norm(context.get("last_error")) or "unknown"
        logger.error("Paper %s failed: %s", arxiv_id, error)

        # Signal the paper machine to resume (with failure data)
        await self._send_completion_signal(
            channel=_paper_channel(arxiv_id),
            data={"phase_complete": False, "error": error},
        )

        return context

    # ------------------------------------------------------------------
    # Sub-machine completion signaling
    # ------------------------------------------------------------------

    async def _send_completion_signal(
        self, channel: str, data: Dict[str, Any]
    ) -> None:
        """Send a signal to resume the paper machine via OS-activated dispatcher."""
        try:
            signal_id = await send_and_notify(
                signal_backend=self._signal_backend,
                trigger_backend=self._trigger_backend,
                channel=channel,
                data=data,
            )
            logger.info("Sent completion signal %s on channel %s", signal_id, channel)
        except Exception as exc:
            logger.error("Failed to send signal on channel %s: %s", channel, exc)

    # ------------------------------------------------------------------
    # Sub-machine hooks (called by ranking/extraction/digest machines)
    # ------------------------------------------------------------------

    async def on_submachine_complete(
        self, arxiv_id: str, phase: str, data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Called when a sub-machine completes a phase.

        Signals the paper machine to resume. The paper machine's checkpoint
        is updated by FlatMachines automatically when it resumes and checks
        the signal data.
        """
        logger.info("Sub-machine complete: paper=%s phase=%s", arxiv_id, phase)

        # Signal the paper machine to resume
        await self._send_completion_signal(
            channel=_paper_channel(arxiv_id),
            data={"phase_complete": True, **data} if data else {"phase_complete": True},
        )

    # ------------------------------------------------------------------
    # Paper state queries (reads from FlatMachines checkpoints)
    # ------------------------------------------------------------------

    def get_papers_by_state(self, state: str) -> list:
        """Get all papers in a given state by querying FlatMachines checkpoints."""
        if not self._migration_done:
            _ensure_migration(self._db_path)
            self._migration_done = True

        conn = _get_db_connection(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT ml.execution_id AS paper_id, mc.current_state
                FROM machine_latest ml
                JOIN machine_checkpoints mc ON ml.latest_key = mc.checkpoint_key
                WHERE mc.current_state = ?
                """,
                (state,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_papers_ready_for_phase(self, phase: str) -> list:
        """Get papers ready for a specific phase.

        Routing logic:
        - ranking: state == 'pending'
        - extraction: state == 'ranked' and fmr_score >= 0.60
        - digest: state == 'extracted'
        """
        if phase == "ranking":
            return self.get_papers_by_state("pending")
        elif phase == "extraction":
            # Ensure migration has run (needs generated fmr_score column)
            if not self._migration_done:
                _ensure_migration(self._db_path)
                self._migration_done = True

            # Use generated column fmr_score for efficient indexed query
            conn = _get_db_connection(self._db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT ml.execution_id AS paper_id, mc.current_state, mc.fmr_score
                    FROM machine_latest ml
                    JOIN machine_checkpoints mc ON ml.latest_key = mc.checkpoint_key
                    WHERE mc.current_state = 'ranked'
                      AND mc.fmr_score >= 0.6
                    """,
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        elif phase == "digest":
            return self.get_papers_by_state("extracted")
        else:
            raise ValueError(f"Unknown phase: {phase}")

    # ------------------------------------------------------------------
    # v3_executions queries (scheduler-facing)
    # ------------------------------------------------------------------

    def get_executions_by_status(self, status: str) -> list:
        """Get all executions with a given status from v3_executions."""
        conn = _get_db_connection(self._db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM v3_executions WHERE status = ?",
                (status,),
            ).fetchall()
            return [{k: row[k] for k in row.keys()} for row in rows]
        except sqlite3.OperationalError:
            # Table doesn't exist yet (no analyzer runs)
            return []
        finally:
            conn.close()

    def get_done_executions(self) -> list:
        """Get all completed executions with result paths."""
        return self.get_executions_by_status("done")

    def get_failed_executions(self) -> list:
        """Get all failed executions with error details."""
        return self.get_executions_by_status("failed")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(value: Any) -> Optional[str]:
        """Normalize to string, returning None for empty values."""
        if value is None:
            return None
        s = str(value).strip()
        return s if s else None
