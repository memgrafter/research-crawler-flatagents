"""Config-driven invoker with concurrency slot management.

Reads a config file with per-phase allocation limits, checks active machines
against limits via FlatMachines checkpoints, and launches paper machines into
free slots.

Usage:
    python -m research_paper_analysis_v3.invoker --config config/invoker.yml

The invoker uses a single-row SQLite lock table to prevent concurrent runs.
Lock becomes stale after 5 minutes — safe to force-clear if the process died.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from flatagents import get_logger

logger = get_logger(__name__)

# Lock stale threshold: 5 minutes
LOCK_STALE_SECONDS = 300

# Phase → (machine_name, v3_executions ready status, slot config key)
PHASE_CONFIG = {
    "prep": (
        "prep-pipeline",
        "pending",  # papers with status='pending' are ready for prep
        "prep_slots",
    ),
    "analyzer": (
        "analyzer-pipeline",
        "prepped",  # papers with status='prepped' are ready for analyzer
        "analyzer_slots",
    ),
}

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _ensure_invoker_schema(db_path: str) -> None:
    """Create the invoker lock table if it doesn't exist."""
    conn = _get_connection(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoker_lock (
                locked      INTEGER NOT NULL DEFAULT 0,
                pid         INTEGER,
                acquired_at TEXT
            )
            """
        )
        # Seed the row if empty
        count = conn.execute("SELECT COUNT(*) FROM invoker_lock").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO invoker_lock (locked, pid, acquired_at) VALUES (0, NULL, NULL)"
            )
        conn.commit()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Lock management
# ---------------------------------------------------------------------------

def acquire_lock(db_path: str, force: bool = False) -> bool:
    """Acquire the invoker lock.

    Returns True if lock acquired, False if held by another process.
    If force=True, clears stale locks (older than LOCK_STALE_SECONDS).
    """
    _ensure_invoker_schema(db_path)
    conn = _get_connection(db_path)
    try:
        row = conn.execute("SELECT locked, pid, acquired_at FROM invoker_lock").fetchone()
        locked = bool(row[0])

        if force and locked and _is_stale(row[2]):
            logger.info(
                "Clearing stale lock (pid=%s, acquired_at=%s)", row[1], row[2]
            )
            locked = False

        if not locked:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE invoker_lock SET locked = 1, pid = ?, acquired_at = ?",
                (None, now),
            )
            conn.commit()
            logger.info("Invoker lock acquired")
            return True

        logger.warning("Invoker lock held by pid=%s since %s", row[1], row[2])
        return False
    finally:
        conn.close()


def release_lock(db_path: str) -> None:
    """Release the invoker lock."""
    _ensure_invoker_schema(db_path)
    conn = _get_connection(db_path)
    try:
        conn.execute(
            "UPDATE invoker_lock SET locked = 0, pid = NULL, acquired_at = NULL"
        )
        conn.commit()
        logger.info("Invoker lock released")
    finally:
        conn.close()


def _is_stale(acquired_at: Optional[str]) -> bool:
    """Check if a lock timestamp is older than LOCK_STALE_SECONDS."""
    if not acquired_at:
        return True
    try:
        acquired = datetime.fromisoformat(acquired_at)
        delta = (datetime.now(timezone.utc) - acquired).total_seconds()
        return delta > LOCK_STALE_SECONDS
    except (ValueError, TypeError):
        return True  # bad timestamp → treat as stale

# ---------------------------------------------------------------------------
# Active machine counting (via FlatMachines checkpoints)
# ---------------------------------------------------------------------------

def get_active_machine_count(db_path: str, machine_name: str) -> int:
    """Count machines currently running for a given machine_name.

    Reads from machine_latest + machine_checkpoints: counts executions
    where the latest event is not 'machine_end' (i.e., still active).
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM machine_latest ml
            JOIN machine_checkpoints mc ON ml.latest_key = mc.checkpoint_key
            WHERE mc.machine_name = ?
              AND mc.event != 'machine_end'
            """,
            (machine_name,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Paper queries (via v3_executions)
# ---------------------------------------------------------------------------

def get_papers_ready_for_phase(db_path: str, status: str) -> List[str]:
    """Get paper_ids ready for a phase (by v3_executions status)."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT paper_id FROM v3_executions WHERE status = ?",
            (status,),
        ).fetchall()
        return [row[0] for row in rows]
    except sqlite3.OperationalError:
        # v3_executions doesn't exist yet
        return []
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Machine launching
# ---------------------------------------------------------------------------

def launch_paper_machine(
    paper_id: str,
    phase: str,
    project_root: Path,
    db_path: str,
) -> bool:
    """Launch a prep or analyzer machine for a single paper.

    Returns True if launched successfully, False on error.
    """
    from flatmachines import FlatMachine, HooksRegistry
    from flatmachines.persistence import SQLiteCheckpointBackend
    from research_paper_analysis_v3.hooks import V3Hooks

    machine_config = {
        "prep": "config/prep_machine.yml",
        "analyzer": "config/analyzer_machine.yml",
    }.get(phase)

    if not machine_config:
        logger.error("Unknown phase: %s", phase)
        return False

    registry = HooksRegistry()
    registry.register(
        "v3-hooks",
        lambda: V3Hooks(
            project_root=project_root,
            data_dir=project_root / "data",
            db_path=db_path,
        ),
    )

    persistence = SQLiteCheckpointBackend(db_path=db_path)

    input_map = {
        "prep": {
            "arxiv_id": paper_id,
            "paper_id": paper_id,
        },
        "analyzer": {
            "arxiv_id": paper_id,
            "execution_id": paper_id,
            "paper_id": paper_id,
        },
    }.get(phase, {})

    # Read paper title from v3_executions if available (not required)
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT result_path FROM v3_executions WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
    finally:
        conn.close()

    if phase == "analyzer" and row and row[0]:
        # For analyzer, we need cleaned_paper_text from prep result
        docling_path = project_root / "data" / "papers_docling_json" / f"{paper_id}.json"
        if docling_path.exists():
            input_map["docling_json_path"] = str(docling_path)

    try:
        machine = FlatMachine(
            config_file=machine_config,
            hooks_registry=registry,
            persistence=persistence,
        )

        import asyncio

        async def _run():
            return await machine.execute(input=input_map)

        result = asyncio.run(_run())
        logger.info(
            "Launched %s for paper %s: status=%s",
            phase,
            paper_id,
            result.get("status", result.get("quality_gate_decision", "unknown")),
        )
        return True
    except Exception as exc:
        logger.error("Failed to launch %s for paper %s: %s", phase, paper_id, exc)
        return False

# ---------------------------------------------------------------------------
# Main invoker loop
# ---------------------------------------------------------------------------

def invoke(config_path: str, db_path: str, project_root: Path) -> Dict[str, int]:
    """Run the invoker: check slots, launch machines into free ones.

    Returns a dict of phase → count launched.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    launched = {}

    for phase, (machine_name, ready_status, slot_key) in PHASE_CONFIG.items():
        max_slots = int(config.get(slot_key, 1))
        active = get_active_machine_count(db_path, machine_name)
        free = max(0, max_slots - active)

        if free <= 0:
            logger.info(
                "Phase %s: %d/%d slots active, no room", active, max_slots
            )
            continue

        ready_papers = get_papers_ready_for_phase(db_path, ready_status)
        to_launch = min(free, len(ready_papers))

        if to_launch <= 0:
            logger.info("Phase %s: no papers ready", phase)
            continue

        count = 0
        for paper_id in ready_papers[:to_launch]:
            if launch_paper_machine(paper_id, phase, project_root, db_path):
                count += 1

        launched[phase] = count
        logger.info(
            "Phase %s: launched %d/%d papers (active=%d/%d)",
            phase,
            count,
            to_launch,
            active,
            max_slots,
        )

    return launched

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Config-driven invoker")
    parser.add_argument(
        "--config",
        default="config/invoker.yml",
        help="Path to invoker config (default: config/invoker.yml)",
    )
    parser.add_argument(
        "--db-path",
        default="data/v3_papers.sqlite",
        help="Path to v3 database (default: data/v3_papers.sqlite)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force-clear stale lock",
    )
    args = parser.parse_args()

    project_root = Path.cwd()
    db_path = str(project_root / args.db_path)

    if not acquire_lock(db_path, force=args.force):
        logger.error("Could not acquire invoker lock")
        raise SystemExit(1)

    try:
        launched = invoke(args.config, db_path, project_root)
        total = sum(launched.values())
        logger.info("Invoker complete: %d papers launched across %d phases", total, len(launched))
    finally:
        release_lock(db_path)


if __name__ == "__main__":
    main()

