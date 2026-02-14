#!/usr/bin/env -S uv run python
"""V2 runner: three-phase pipeline with continuous priority scheduling.

Phase 1 (prep):      download PDF, extract text, corpus signals, key_outcome  [cheap model]
Phase 2 (expensive): parallel why_hypothesis + reproduction + open_questions   [pony-alpha only]
Phase 3 (wrap):      limits, assembly, judge, repair, frontmatter             [cheap model]

Scheduling: continuous priority loop (expensive > wrap > prep).  Every time ANY
task completes, the freed slot is immediately given to the highest-priority work.
No batch boundaries.  Optional per-phase caps prevent one phase from eating all slots.

Usage:
    python run.py                           # Single pass, default settings
    python run.py --workers 200 --daemon    # Continuous until done / budget hit
    python run.py --prep-only --workers 50  # Only run prep phase
    python run.py --seed-only               # Only seed from arxiv DB
    python run.py --workers 200 --daemon --max-prep 150 --max-expensive 40 --max-wrap 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sqlite3
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("v2_runner")

# Suppress litellm's sync print() calls (Provider List spam) that block the event loop
# when piped through tee. Must be set before any litellm imports.
try:
    import litellm
    litellm.suppress_debug_info = True
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
CHECKPOINT_DIR = DATA_DIR / "checkpoints"
SCHEMA_FILE = PROJECT_ROOT / "schema" / "v2_executions.sql"

# Known req counts per phase
PREP_REQS = 1        # key_outcome only
EXPENSIVE_REQS = 3   # why + reproduction + open_questions (all pony-alpha)
WRAP_REQS = 4        # limits + assembler + judge + ~1 repair avg

FINAL_STATES = {"done", "failed", "no_work_final", "failed_incomplete"}

# Phase capacity caps (0 = uncapped, global --workers is the only gate).
# Set these when you want to prevent one phase from eating all slots.
# Intentionally allowed to sum > max_workers so no slots idle when a phase has no work.
_MAX_PREP = int(os.environ.get("RPA_V2_MAX_PREP", "0"))
_MAX_EXPENSIVE = int(os.environ.get("RPA_V2_MAX_EXPENSIVE", "0"))
_MAX_WRAP = int(os.environ.get("RPA_V2_MAX_WRAP", "0"))
# If prepped buffer falls below this watermark, prioritize prep over wrap
# so arcee capacity goes to key_outcome_writer first.
_PREPPED_LOW_WATERMARK = int(os.environ.get("RPA_V2_PREPPED_LOW_WATERMARK", "0"))

# Shutdown/drain behavior
# First Ctrl+C drains in-flight tasks (no new launches). 0 = wait indefinitely.
_SHUTDOWN_DRAIN_TIMEOUT_SECS = float(os.environ.get("RPA_V2_SHUTDOWN_DRAIN_TIMEOUT_SECS", "0"))
# Progress log cadence while draining.
_SHUTDOWN_PROGRESS_INTERVAL_SECS = float(os.environ.get("RPA_V2_SHUTDOWN_PROGRESS_INTERVAL_SECS", "5"))
# Second Ctrl+C/SIGTERM debounce before force-exit.
_SECOND_SIGNAL_DEBOUNCE_SECS = float(os.environ.get("RPA_V2_SECOND_SIGNAL_DEBOUNCE_SECS", "3"))

# Machine config paths
PREP_CONFIG = str(CONFIG_DIR / "prep_machine.yml")
EXPENSIVE_CONFIG = str(CONFIG_DIR / "expensive_machine.yml")
WRAP_CONFIG = str(CONFIG_DIR / "wrap_machine.yml")

# Process-wide SQLite connection singletons (reduces FD churn at high concurrency).
_DB_SINGLETON_LOCK = threading.Lock()
_V2_DB_CONN: Optional[sqlite3.Connection] = None
_V2_DB_PATH: Optional[str] = None
_ARXIV_DB_CONN: Optional[sqlite3.Connection] = None
_ARXIV_DB_PATH: Optional[str] = None

# DB-backed execution lease lock singleton
_LEASE_LOCK = None
_RUN_INSTANCE_ID = str(uuid.uuid4())

# DB-backed checkpoint persistence singleton
_CHECKPOINT_BACKEND = None


# ---------------------------------------------------------------------------
# V2 executions DB
# ---------------------------------------------------------------------------

def get_v2_db() -> sqlite3.Connection:
    global _V2_DB_CONN, _V2_DB_PATH

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = os.environ.get("V2_EXECUTIONS_DB_PATH", str(DATA_DIR / "v2_executions.sqlite"))

    if _V2_DB_CONN is not None and _V2_DB_PATH == db_path:
        return _V2_DB_CONN

    with _DB_SINGLETON_LOCK:
        if _V2_DB_CONN is not None and _V2_DB_PATH == db_path:
            return _V2_DB_CONN

        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        if SCHEMA_FILE.exists():
            conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        # Migrate: add expensive_output if missing (existing DBs)
        _migrate_v2_db(conn)

        _V2_DB_CONN = conn
        _V2_DB_PATH = db_path
        return _V2_DB_CONN


def _migrate_v2_db(conn: sqlite3.Connection) -> None:
    """Add columns introduced after initial schema."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(executions)").fetchall()}
    if "expensive_output" not in cols:
        conn.execute("ALTER TABLE executions ADD COLUMN expensive_output TEXT")
        conn.commit()
        logger.info("Migrated: added expensive_output column")


def get_arxiv_db() -> Optional[sqlite3.Connection]:
    global _ARXIV_DB_CONN, _ARXIV_DB_PATH

    db_path = os.environ.get(
        "ARXIV_DB_PATH",
        str(PROJECT_ROOT.parent / "arxiv_crawler" / "data" / "arxiv.sqlite"),
    )
    if not Path(db_path).exists():
        logger.warning("Arxiv DB not found: %s", db_path)
        return None

    if _ARXIV_DB_CONN is not None and _ARXIV_DB_PATH == db_path:
        return _ARXIV_DB_CONN

    with _DB_SINGLETON_LOCK:
        if _ARXIV_DB_CONN is not None and _ARXIV_DB_PATH == db_path:
            return _ARXIV_DB_CONN

        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")

        _ARXIV_DB_CONN = conn
        _ARXIV_DB_PATH = db_path
        return _ARXIV_DB_CONN


def get_lease_lock():
    """Return process-wide DB-backed execution lease lock."""
    global _LEASE_LOCK
    if _LEASE_LOCK is not None:
        return _LEASE_LOCK

    from research_paper_analysis_v2.sqlite_lease_lock import SQLiteLeaseLock

    db_path = os.environ.get("V2_EXECUTIONS_DB_PATH", str(DATA_DIR / "v2_executions.sqlite"))
    owner_id = f"{socket.gethostname()}:{os.getpid()}:{_RUN_INSTANCE_ID}"
    ttl = int(os.environ.get("RPA_V2_LEASE_TTL_SECONDS", "300"))
    renew = int(os.environ.get("RPA_V2_LEASE_RENEW_SECONDS", "100"))

    _LEASE_LOCK = SQLiteLeaseLock(
        db_path=db_path,
        owner_id=owner_id,
        phase="machine",
        ttl_seconds=ttl,
        renew_interval_seconds=renew,
    )
    logger.info("Using DB lease lock owner=%s ttl=%ss renew=%ss", owner_id, ttl, renew)
    return _LEASE_LOCK


def get_checkpoint_backend():
    """Return process-wide DB-backed checkpoint backend."""
    global _CHECKPOINT_BACKEND
    if _CHECKPOINT_BACKEND is not None:
        return _CHECKPOINT_BACKEND

    from research_paper_analysis_v2.sqlite_checkpoint_backend import SQLiteCheckpointBackend

    db_path = os.environ.get("V2_EXECUTIONS_DB_PATH", str(DATA_DIR / "v2_executions.sqlite"))
    _CHECKPOINT_BACKEND = SQLiteCheckpointBackend(db_path=db_path)
    logger.info("Using DB checkpoint backend: %s", db_path)
    return _CHECKPOINT_BACKEND


async def migrate_file_checkpoints_to_db(force: bool = False) -> Dict[str, int]:
    """Migrate file checkpoints (data/checkpoints) into DB-backed checkpoint tables.

    Safe and idempotent: existing rows are upserted by key, files are not deleted.
    """
    stats = {
        "dirs_seen": 0,
        "latest_found": 0,
        "migrated": 0,
        "missing_snapshot": 0,
        "read_errors": 0,
        "skipped_existing_db": 0,
    }

    conn = get_v2_db()
    db_latest_count = conn.execute("SELECT COUNT(*) FROM machine_latest").fetchone()[0]
    if db_latest_count > 0 and not force:
        stats["skipped_existing_db"] = int(db_latest_count)
        return stats

    if not CHECKPOINT_DIR.exists():
        return stats

    backend = get_checkpoint_backend()

    for latest_file in CHECKPOINT_DIR.glob("*/latest"):
        stats["dirs_seen"] += 1
        execution_id = latest_file.parent.name
        try:
            latest_key = latest_file.read_text(encoding="utf-8").strip()
            if not latest_key:
                continue
            stats["latest_found"] += 1

            snapshot_path = CHECKPOINT_DIR / latest_key
            if not snapshot_path.exists():
                stats["missing_snapshot"] += 1
                continue

            payload = snapshot_path.read_bytes()
            await backend.save(latest_key, payload)
            await backend.save(f"{execution_id}/latest", latest_key.encode("utf-8"))
            stats["migrated"] += 1
        except Exception:
            stats["read_errors"] += 1

    return stats


# ---------------------------------------------------------------------------
# Seed: arxiv DB → v2 executions
# ---------------------------------------------------------------------------

def seed(limit: int = 500) -> int:
    """Seed v2 executions from arxiv DB pending papers. Returns count seeded."""
    arxiv_conn = get_arxiv_db()
    if not arxiv_conn:
        logger.warning("Cannot seed: arxiv DB unavailable")
        return 0

    v2_conn = get_v2_db()
    now = datetime.now(timezone.utc).isoformat()

    rows = arxiv_conn.execute(
        """
        WITH latest_versions AS (
            SELECT arxiv_id, MAX(version) AS max_version
            FROM papers
            GROUP BY arxiv_id
        )
        SELECT p.id AS paper_id, p.arxiv_id, p.title, p.authors, p.abstract
        FROM paper_queue q
        JOIN papers p ON p.id = q.paper_id
        JOIN latest_versions lv
          ON lv.arxiv_id = p.arxiv_id
         AND lv.max_version = p.version
        WHERE q.status = 'pending'
        ORDER BY q.priority DESC, q.enqueued_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    seeded = 0
    for row in rows:
        execution_id = str(uuid.uuid4())
        try:
            v2_conn.execute(
                """
                INSERT OR IGNORE INTO executions
                (execution_id, arxiv_id, paper_id, title, authors, abstract, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    execution_id,
                    row["arxiv_id"],
                    row["paper_id"],
                    row["title"],
                    row["authors"] or "",
                    row["abstract"] or "",
                    now,
                    now,
                ),
            )
            if v2_conn.total_changes:
                seeded += 1
        except sqlite3.IntegrityError:
            pass

    v2_conn.commit()

    if seeded:
        logger.info("Seeded %d new executions from arxiv DB", seeded)
    return seeded


# ---------------------------------------------------------------------------
# Daily usage tracking
# ---------------------------------------------------------------------------

def get_daily_usage(conn: sqlite3.Connection) -> Dict[str, int]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT total_calls, cheap_calls, expensive_calls FROM daily_usage WHERE date = ?",
        (today,),
    ).fetchone()
    if row:
        return {
            "total": row["total_calls"],
            "cheap": row["cheap_calls"],
            "expensive": row["expensive_calls"],
        }
    return {"total": 0, "cheap": 0, "expensive": 0}


def increment_daily_usage(conn: sqlite3.Connection, cheap: int = 0, expensive: int = 0) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = cheap + expensive
    conn.execute(
        """
        INSERT INTO daily_usage (date, total_calls, cheap_calls, expensive_calls)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            total_calls = total_calls + ?,
            cheap_calls = cheap_calls + ?,
            expensive_calls = expensive_calls + ?
        """,
        (today, total, cheap, expensive, total, cheap, expensive),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Checkpoint scanning
# ---------------------------------------------------------------------------

def reset_orphaned_executions() -> Dict[str, int]:
    """Reset executions stuck in transient statuses with no checkpoint record.

    These are executions that were claimed (status changed to prepping/analyzing/
    wrapping) but the machine never checkpointed — typically from a Ctrl+C or crash.
    Safe for single-daemon use; in multi-daemon setups use --reset-orphans only when
    you know no other daemons are running.
    """
    conn = get_v2_db()
    stats: Dict[str, int] = {}

    for transient, fallback in [
        ("prepping", "pending"),
        ("analyzing", "prepped"),
        ("wrapping", "analyzed"),
    ]:
        # Find executions in this transient status that have NO checkpoint record
        cur = conn.execute(
            """
            UPDATE executions
            SET status = ?, updated_at = ?
            WHERE status = ?
              AND execution_id NOT IN (
                  SELECT DISTINCT ml.execution_id
                  FROM machine_latest ml
              )
            """,
            (fallback, datetime.now(timezone.utc).isoformat(), transient),
        )
        conn.commit()
        count = cur.rowcount or 0
        stats[transient] = count
        if count:
            logger.info("Reset %d orphaned '%s' → '%s'", count, transient, fallback)

    return stats


def find_incomplete_executions(machine_name: str) -> List[str]:
    """Find incomplete executions by querying DB-backed checkpoint tables."""
    conn = get_v2_db()
    try:
        rows = conn.execute(
            """
            SELECT mc.execution_id, mc.event, mc.current_state
            FROM machine_latest ml
            JOIN machine_checkpoints mc ON mc.checkpoint_key = ml.latest_key
            WHERE mc.machine_name = ?
            """,
            (machine_name,),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Checkpoint query failed for machine=%s: %s", machine_name, exc)
        return []

    incomplete: List[str] = []
    for row in rows:
        event = row["event"] or ""
        current_state = row["current_state"] or ""
        if event == "machine_end":
            continue
        if current_state in FINAL_STATES:
            continue
        incomplete.append(row["execution_id"])
    return incomplete


# ---------------------------------------------------------------------------
# Claim executions from v2 DB
# ---------------------------------------------------------------------------

def claim_for_prep(conn: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    """Atomically claim pending → prepping."""
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        UPDATE executions
        SET status = 'prepping', updated_at = ?
        WHERE execution_id IN (
            SELECT execution_id FROM executions
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        )
        RETURNING execution_id, arxiv_id, paper_id, title, authors, abstract
        """,
        (now, limit),
    ).fetchall()
    conn.commit()
    return [dict(r) for r in rows]


def claim_for_expensive(conn: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    """Atomically claim prepped → analyzing. Returns dicts with prep_output."""
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        UPDATE executions
        SET status = 'analyzing', updated_at = ?
        WHERE execution_id IN (
            SELECT execution_id FROM executions
            WHERE status = 'prepped'
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        )
        RETURNING execution_id, arxiv_id, paper_id, title, authors, abstract, prep_output
        """,
        (now, limit),
    ).fetchall()
    conn.commit()
    return [dict(r) for r in rows]


def claim_for_wrap(conn: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    """Atomically claim analyzed → wrapping. Returns dicts with prep_output + expensive_output."""
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        UPDATE executions
        SET status = 'wrapping', updated_at = ?
        WHERE execution_id IN (
            SELECT execution_id FROM executions
            WHERE status = 'analyzed'
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        )
        RETURNING execution_id, arxiv_id, paper_id, title, authors, abstract, prep_output, expensive_output
        """,
        (now, limit),
    ).fetchall()
    conn.commit()
    return [dict(r) for r in rows]


def release_stale(conn: sqlite3.Connection, status: str, fallback_status: str, max_age_minutes: int = 60) -> int:
    """Release executions stuck in a transient status back to a safe state."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    cur = conn.execute(
        """
        UPDATE executions
        SET status = ?, updated_at = ?
        WHERE status = ? AND updated_at < ?
        """,
        (fallback_status, datetime.now(timezone.utc).isoformat(), status, cutoff),
    )
    conn.commit()
    released = cur.rowcount or 0
    if released:
        logger.warning("Released %d stale '%s' executions back to '%s'", released, status, fallback_status)
    return released


# ---------------------------------------------------------------------------
# Machine execution
# ---------------------------------------------------------------------------

async def run_prep(execution: Dict[str, Any]) -> bool:
    """Run prep_machine for one execution. Returns True on success."""
    from research_paper_analysis_v2.lease_flatmachine import LeaseFlatMachine

    execution_id = execution["execution_id"]
    logger.info("PREP %s: %s", execution["arxiv_id"], execution["title"][:60])

    try:
        machine = LeaseFlatMachine(
            config_file=PREP_CONFIG,
            persistence=get_checkpoint_backend(),
            lock=get_lease_lock(),
            _execution_id=execution_id,
        )
        result = await machine.execute(input={
            "execution_id": execution_id,
            "arxiv_id": execution["arxiv_id"],
            "paper_id": execution["paper_id"],
            "title": execution["title"],
            "authors": execution["authors"],
            "abstract": execution["abstract"],
        })

        if result.get("error"):
            logger.error("PREP FAILED %s: %s", execution["arxiv_id"], result["error"])
            return False

        logger.info("PREP DONE %s", execution["arxiv_id"])
        return True

    except Exception as exc:
        logger.exception("PREP EXCEPTION %s: %s", execution["arxiv_id"], exc)
        _mark_failed_in_db(execution_id, str(exc))
        return False


async def run_expensive(execution: Dict[str, Any]) -> bool:
    """Run expensive_machine for one execution. Returns True on success."""
    from research_paper_analysis_v2.lease_flatmachine import LeaseFlatMachine

    execution_id = execution["execution_id"]
    arxiv_id = execution["arxiv_id"]

    prep_output = _parse_json_field(execution.get("prep_output"), "prep_output")
    if not prep_output:
        logger.error("EXPENSIVE SKIP %s: no prep_output", arxiv_id)
        return False

    source_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    logger.info("EXPENSIVE %s: %s", arxiv_id, execution["title"][:60])

    try:
        machine = LeaseFlatMachine(
            config_file=EXPENSIVE_CONFIG,
            persistence=get_checkpoint_backend(),
            lock=get_lease_lock(),
            _execution_id=execution_id,
        )
        result = await machine.execute(input={
            "execution_id": execution_id,
            "arxiv_id": arxiv_id,
            "paper_id": execution["paper_id"],
            "source_url": source_url,
            "title": execution["title"],
            "authors": execution["authors"],
            "abstract": execution["abstract"],
            "paper_text": prep_output.get("paper_text") or prep_output.get("section_text", ""),
            "reference_count": prep_output.get("reference_count", 0),
            "key_outcome": prep_output.get("key_outcome", ""),
            "corpus_signals": prep_output.get("corpus_signals"),
            "corpus_neighbors": prep_output.get("corpus_neighbors"),
        })

        if result.get("error"):
            logger.error("EXPENSIVE FAILED %s: %s", arxiv_id, result["error"])
            return False

        logger.info("EXPENSIVE DONE %s", arxiv_id)
        return True

    except Exception as exc:
        logger.exception("EXPENSIVE EXCEPTION %s: %s", arxiv_id, exc)
        _mark_failed_in_db(execution_id, str(exc))
        return False


async def run_wrap(execution: Dict[str, Any]) -> bool:
    """Run wrap_machine for one execution. Returns True on success."""
    from research_paper_analysis_v2.lease_flatmachine import LeaseFlatMachine

    execution_id = execution["execution_id"]
    arxiv_id = execution["arxiv_id"]

    prep_output = _parse_json_field(execution.get("prep_output"), "prep_output")
    expensive_output = _parse_json_field(execution.get("expensive_output"), "expensive_output")
    if not prep_output or not expensive_output:
        logger.error("WRAP SKIP %s: missing prep_output or expensive_output", arxiv_id)
        return False

    source_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    logger.info("WRAP %s: %s", arxiv_id, execution["title"][:60])

    try:
        machine = LeaseFlatMachine(
            config_file=WRAP_CONFIG,
            persistence=get_checkpoint_backend(),
            lock=get_lease_lock(),
            _execution_id=execution_id,
        )
        result = await machine.execute(input={
            "execution_id": execution_id,
            "arxiv_id": arxiv_id,
            "paper_id": execution["paper_id"],
            "source_url": source_url,
            "title": execution["title"],
            "authors": execution["authors"],
            "abstract": execution["abstract"],
            # From prep
            "paper_text": prep_output.get("paper_text") or prep_output.get("section_text", ""),
            "reference_count": prep_output.get("reference_count", 0),
            "key_outcome": prep_output.get("key_outcome", ""),
            "corpus_signals": prep_output.get("corpus_signals"),
            "corpus_neighbors": prep_output.get("corpus_neighbors"),
            # From expensive
            "why_hypotheses": expensive_output.get("why_hypotheses", ""),
            "reproduction_notes": expensive_output.get("reproduction_notes", ""),
            "open_questions": expensive_output.get("open_questions", ""),
        })

        if result.get("error"):
            logger.error("WRAP FAILED %s: %s", arxiv_id, result["error"])
            return False

        logger.info("WRAP DONE %s", arxiv_id)
        return True

    except Exception as exc:
        logger.exception("WRAP EXCEPTION %s: %s", arxiv_id, exc)
        _mark_failed_in_db(execution_id, str(exc))
        return False


async def resume_machine(execution_id: str, machine_name: str, config_file: str) -> bool:
    """Resume an incomplete machine execution from checkpoint."""
    from research_paper_analysis_v2.lease_flatmachine import LeaseFlatMachine

    logger.info("RESUME %s (%s)", execution_id, machine_name)

    try:
        machine = LeaseFlatMachine(
            config_file=config_file,
            persistence=get_checkpoint_backend(),
            lock=get_lease_lock(),
        )
        result = await machine.execute(resume_from=execution_id)

        if result.get("error"):
            logger.error("RESUME FAILED %s: %s", execution_id, result["error"])
            return False

        logger.info("RESUME DONE %s", execution_id)
        return True

    except Exception as exc:
        logger.exception("RESUME EXCEPTION %s: %s", execution_id, exc)
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_field(raw: Any, field_name: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON text field from the DB into a dict."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.error("Invalid JSON in %s", field_name)
        return None


def _mark_failed_in_db(execution_id: str, error: str) -> None:
    """Mark an execution as failed directly (for exception paths)."""
    try:
        conn = get_v2_db()
        conn.execute(
            "UPDATE executions SET status = 'failed', error = ?, updated_at = ? WHERE execution_id = ?",
            (error, datetime.now(timezone.utc).isoformat(), execution_id),
        )
        conn.commit()
    except Exception as mark_exc:
        logger.exception(
            "Failed to mark execution %s as failed (original error: %s): %s",
            execution_id,
            error,
            mark_exc,
        )


# ---------------------------------------------------------------------------
# Main run logic — continuous scheduler
# ---------------------------------------------------------------------------

def _pick_next(
    conn: sqlite3.Connection,
    prep_only: bool,
    prep_sem: Optional[asyncio.Semaphore],
    expensive_sem: Optional[asyncio.Semaphore],
    wrap_sem: Optional[asyncio.Semaphore],
    resuming: set[str],
):
    """Return (coroutine, phase_sem, stat_key) for the highest-priority available work.

    Returns (None, None, None) when there is nothing to launch.
    Claims are limit=1 so the caller re-evaluates priority after every launch.

    ``resuming`` tracks execution IDs already launched for resume so we don't
    duplicate — find_incomplete_executions is read-only and returns the same
    IDs until the task actually updates the checkpoint.

    Priority:
      1. Resume incomplete machines (expensive first; prep/wrap order is dynamic)
      2. New expensive (highest-value model usage)
      3. New prep vs wrap based on prepped-buffer watermark
    """
    # Per-model 429 gates:
    # - prep + wrap use cheap model
    # - expensive uses reasoning model
    from research_paper_analysis_v2.hooks import (
        get_analyzing_wrap_watermark,
        is_cheap_model_rate_limited,
        is_expensive_model_rate_limited,
    )
    cheap_gated = False  # temporary bypass: do not gate cheap-model phases on 429
    #cheap_gated = is_cheap_model_rate_limited()
    expensive_gated = is_expensive_model_rate_limited()

    # Keep a minimum prepped buffer so prep can feed expensive (pony-alpha).
    prefer_prep_over_wrap = False
    if not prep_only and _PREPPED_LOW_WATERMARK > 0:
        prepped_count = conn.execute(
            "SELECT COUNT(*) FROM executions WHERE status = 'prepped'"
        ).fetchone()[0]
        prefer_prep_over_wrap = prepped_count < _PREPPED_LOW_WATERMARK

    # Dynamic escape hatch:
    # analyzing watermark is normally 300, temporarily 100 for 60s after
    # the known pony high-demand message appears.
    analyzing_wrap_watermark = get_analyzing_wrap_watermark()
    if not prep_only and analyzing_wrap_watermark > 0:
        analyzing_count = conn.execute(
            "SELECT COUNT(*) FROM executions WHERE status = 'analyzing'"
        ).fetchone()[0]
        if analyzing_count >= analyzing_wrap_watermark:
            prefer_prep_over_wrap = False

    # 1. Resume incomplete
    resume_order = [
        ("expensive-pipeline", EXPENSIVE_CONFIG, expensive_sem),
    ]
    if prefer_prep_over_wrap:
        resume_order.extend([
            ("prep-pipeline", PREP_CONFIG, prep_sem),
            ("wrap-pipeline", WRAP_CONFIG, wrap_sem),
        ])
    else:
        resume_order.extend([
            ("wrap-pipeline", WRAP_CONFIG, wrap_sem),
            ("prep-pipeline", PREP_CONFIG, prep_sem),
        ])

    for machine_name, config_file, sem in resume_order:
        if prep_only and machine_name != "prep-pipeline":
            continue
        # Strict gate: while prepped buffer is low, do not spend arcee on wrap.
        if prefer_prep_over_wrap and machine_name == "wrap-pipeline":
            continue
        # Skip resumes for phases whose model is currently rate-limited.
        if machine_name in {"prep-pipeline", "wrap-pipeline"} and cheap_gated:
            continue
        if machine_name == "expensive-pipeline" and expensive_gated:
            continue
        for exec_id in find_incomplete_executions(machine_name):
            if exec_id not in resuming:
                resuming.add(exec_id)
                return (resume_machine(exec_id, machine_name, config_file), sem, "resumed")

    if not prep_only:
        # 2. New expensive (only when expensive model is open)
        if not expensive_gated and (expensive_sem is None or expensive_sem._value > 0):
            claimed = claim_for_expensive(conn, 1)
            if claimed:
                resuming.add(claimed[0]["execution_id"])
                return (run_expensive(claimed[0]), expensive_sem, "expensive")

        # 3. New prep when prepped buffer is low (before wrap)
        if (
            prefer_prep_over_wrap
            and not cheap_gated
            and (prep_sem is None or prep_sem._value > 0)
        ):
            claimed = claim_for_prep(conn, 1)
            if claimed:
                resuming.add(claimed[0]["execution_id"])
                return (run_prep(claimed[0]), prep_sem, "prep")

        # 4. New wrap (strictly blocked while prepped buffer is low)
        if not prefer_prep_over_wrap and not cheap_gated:
            if wrap_sem is None or wrap_sem._value > 0:
                claimed = claim_for_wrap(conn, 1)
                if claimed:
                    resuming.add(claimed[0]["execution_id"])
                    return (run_wrap(claimed[0]), wrap_sem, "wrap")

    # 5. New prep (only when cheap model is open)
    if not cheap_gated and (prep_sem is None or prep_sem._value > 0):
        claimed = claim_for_prep(conn, 1)
        if claimed:
            resuming.add(claimed[0]["execution_id"])
            return (run_prep(claimed[0]), prep_sem, "prep")

    return (None, None, None)


async def run_continuous(
    max_workers: int = 3,
    daily_budget: int = 1000,
    prep_only: bool = False,
    poll_interval: float = 5.0,
    seed_limit: int = 500,
    stale_interval: float = 120.0,
    single_pass: bool = False,
    seed_enabled: bool = False,
) -> Dict[str, int]:
    """Continuous priority scheduler.  Replaces run_once + run_daemon.

    Every time ANY task completes, the freed slot is immediately given to the
    highest-priority available work.  No batch boundaries.

    Args:
        max_workers:    Global concurrency ceiling.
        daily_budget:   Stop after this many estimated LLM requests.
        prep_only:      Only run prep phase.
        poll_interval:  Seconds to sleep when no work is available.
        seed_limit:     Max papers to seed per housekeeping sweep.
        stale_interval: Seconds between stale-release sweeps.
        single_pass:    If True, stop as soon as no new work can be launched
                        and all active tasks finish (replaces old run_once).
        seed_enabled:   If True, run startup + periodic seeding from arxiv DB.
    """
    # Size the default thread pool to match worker count.  litellm.acompletion()
    # uses loop.run_in_executor(None, ...) for sync setup before each LLM call;
    # the default pool is only min(32, cpu+4) which starves 200 workers.
    thread_workers = max(max_workers + 4, 32)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=thread_workers))
    logger.info("Thread pool sized to %d (worker count %d + headroom)", thread_workers, max_workers)

    conn = get_v2_db()
    stats: Dict[str, int] = {
        "prep": 0, "expensive": 0, "wrap": 0, "resumed": 0, "errors": 0,
    }

    global_sem = asyncio.Semaphore(max_workers)
    prep_sem = asyncio.Semaphore(_MAX_PREP) if _MAX_PREP > 0 else None
    expensive_sem = asyncio.Semaphore(_MAX_EXPENSIVE) if _MAX_EXPENSIVE > 0 else None
    wrap_sem = asyncio.Semaphore(_MAX_WRAP) if _MAX_WRAP > 0 else None

    active: set[asyncio.Task] = set()
    resuming: set[str] = set()
    last_stale_check = 0.0

    # Graceful shutdown: set event on SIGINT/SIGTERM so the main loop exits cleanly.
    shutdown_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    async def _guarded(coro, phase_sem):
        """Acquire global sem (+ optional phase sem), then run the coroutine."""
        try:
            async with global_sem:
                if phase_sem is not None:
                    async with phase_sem:
                        return await coro
                return await coro
        except asyncio.CancelledError:
            coro.close()
            raise

    def _on_done(task: asyncio.Task) -> None:
        active.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            stats["errors"] += 1
            logger.error("Task failed: %s", exc)
        # Track daily usage per completed task
        phase = getattr(task, "phase", None)
        if phase == "prep":
            increment_daily_usage(conn, cheap=PREP_REQS)
        elif phase == "expensive":
            increment_daily_usage(conn, expensive=EXPENSIVE_REQS)
        elif phase == "wrap":
            increment_daily_usage(conn, cheap=WRAP_REQS)
        elif phase == "resumed":
            increment_daily_usage(conn, cheap=1)  # conservative estimate

    # --- Main loop ---
    while True:
        loop_time = asyncio.get_event_loop().time()

        # Periodic housekeeping: release stale claims, optional re-seed, log status
        if loop_time - last_stale_check > stale_interval:
            release_stale(conn, "prepping", "pending", max_age_minutes=60)
            release_stale(conn, "analyzing", "prepped", max_age_minutes=120)
            release_stale(conn, "wrapping", "analyzed", max_age_minutes=60)
            if seed_enabled:
                seed(limit=seed_limit)

            status_counts = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM executions GROUP BY status"
            ).fetchall():
                status_counts[row["status"]] = row["cnt"]
            usage = get_daily_usage(conn)
            logger.info(
                "Status: pending=%d prepping=%d prepped=%d analyzing=%d "
                "analyzed=%d wrapping=%d done=%d failed=%d | budget=%d/%d | active_tasks=%d",
                status_counts.get("pending", 0),
                status_counts.get("prepping", 0),
                status_counts.get("prepped", 0),
                status_counts.get("analyzing", 0),
                status_counts.get("analyzed", 0),
                status_counts.get("wrapping", 0),
                status_counts.get("done", 0),
                status_counts.get("failed", 0),
                usage["total"], daily_budget,
                len(active),
            )
            last_stale_check = loop_time

        # Shutdown gate
        if shutdown_event.is_set():
            logger.info("Shutdown requested. Draining %d active tasks.", len(active))
            break

        # Budget gate
        usage = get_daily_usage(conn)
        if usage["total"] >= daily_budget:
            logger.info("Budget exhausted (%d/%d). Draining %d active tasks.",
                        usage["total"], daily_budget, len(active))
            break

        # Fill free slots with highest-priority work, one at a time
        launched_this_cycle = 0
        while len(active) < max_workers:
            coro, phase_sem, stat_key = _pick_next(conn, prep_only, prep_sem, expensive_sem, wrap_sem, resuming)
            if coro is None:
                break
            task = asyncio.create_task(_guarded(coro, phase_sem))
            task.phase = stat_key  # type: ignore[attr-defined]
            task.add_done_callback(_on_done)
            active.add(task)
            stats[stat_key] += 1
            launched_this_cycle += 1

        # Decide how to wait
        if active:
            if launched_this_cycle == 0 and single_pass:
                # single_pass: nothing new to launch, drain and exit
                break
            # Wait for at least one task to finish, then loop back to fill the slot
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        else:
            # Nothing running, nothing to launch
            remaining = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE status NOT IN ('done', 'failed')"
            ).fetchone()[0]
            if remaining == 0:
                logger.info("All work complete.")
                break
            if single_pass:
                break
            logger.info("No work available. Waiting %.0fs...", poll_interval)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass  # normal — poll interval elapsed

    # Drain remaining active tasks
    if active:
        if shutdown_event.is_set():
            logger.info(
                "Shutdown drain started for %d active tasks (timeout=%ss, progress_every=%ss)",
                len(active),
                "infinite" if _SHUTDOWN_DRAIN_TIMEOUT_SECS <= 0 else f"{_SHUTDOWN_DRAIN_TIMEOUT_SECS:.0f}",
                max(_SHUTDOWN_PROGRESS_INTERVAL_SECS, 1.0),
            )

            # Second signal handler: debounce for 3s, then force-exit.
            force_exit_scheduled = False

            def _schedule_force_exit(code: int) -> None:
                nonlocal force_exit_scheduled
                if force_exit_scheduled:
                    return
                force_exit_scheduled = True
                logger.warning(
                    "Second shutdown signal received. Force-exit in %.0fs...",
                    _SECOND_SIGNAL_DEBOUNCE_SECS,
                )
                loop.call_later(_SECOND_SIGNAL_DEBOUNCE_SECS, lambda: os._exit(code))

            loop.add_signal_handler(signal.SIGINT, lambda: _schedule_force_exit(130))
            loop.add_signal_handler(signal.SIGTERM, lambda: _schedule_force_exit(143))

            deadline = None
            if _SHUTDOWN_DRAIN_TIMEOUT_SECS > 0:
                deadline = loop.time() + _SHUTDOWN_DRAIN_TIMEOUT_SECS

            # Drain without cancelling tasks on first signal.
            while active:
                remaining = len(active)
                logger.info("Shutdown drain: %d active tasks remaining...", remaining)

                wait_timeout = max(_SHUTDOWN_PROGRESS_INTERVAL_SECS, 1.0)
                if deadline is not None:
                    left = deadline - loop.time()
                    if left <= 0:
                        logger.warning(
                            "Drain timed out after %.0fs with %d tasks still active. Cancelling remaining.",
                            _SHUTDOWN_DRAIN_TIMEOUT_SECS,
                            len(active),
                        )
                        for t in list(active):
                            t.cancel()
                        await asyncio.gather(*active, return_exceptions=True)
                        break
                    wait_timeout = min(wait_timeout, left)

                await asyncio.wait(active, timeout=wait_timeout, return_when=asyncio.FIRST_COMPLETED)
        else:
            logger.info("Draining %d active tasks...", len(active))
            await asyncio.gather(*active, return_exceptions=True)

    logger.info(
        "Scheduler exit: prep=%d expensive=%d wrap=%d resumed=%d errors=%d",
        stats["prep"], stats["expensive"], stats["wrap"], stats["resumed"], stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="V2 paper analysis runner")
    parser.add_argument("-w", "--workers", type=int, default=3, help="Max concurrent workers")
    parser.add_argument("-b", "--budget", type=int, default=1000, help="Daily request budget")
    parser.add_argument("-d", "--daemon", action="store_true", help="Run continuously until all work done / budget hit")
    parser.add_argument("-p", "--poll-interval", type=float, default=5.0, help="Seconds to sleep when no work available")
    parser.add_argument("--prep-only", action="store_true", help="Only run prep phase")
    parser.add_argument("--seed", action="store_true", help="Enable startup + periodic seeding from arxiv DB")
    parser.add_argument("--seed-only", action="store_true", help="Only seed from arxiv DB")
    parser.add_argument("--migrate-checkpoints-only", action="store_true", help="Only migrate file checkpoints into DB tables")
    parser.add_argument("--force-checkpoint-migration", action="store_true", help="Force checkpoint migration even if DB already has latest rows")
    parser.add_argument("--seed-limit", type=int, default=500, help="Max papers to seed per pass (used with --seed or --seed-only)")
    parser.add_argument("--max-prep", type=int, default=0, help="Max concurrent prep tasks (0=uncapped, env: RPA_V2_MAX_PREP)")
    parser.add_argument("--max-expensive", type=int, default=0, help="Max concurrent expensive tasks (0=uncapped, env: RPA_V2_MAX_EXPENSIVE)")
    parser.add_argument("--max-wrap", type=int, default=0, help="Max concurrent wrap tasks (0=uncapped, env: RPA_V2_MAX_WRAP)")
    parser.add_argument("--max-downloads", type=int, default=0, help="Max concurrent PDF downloads (0=use hooks default, env: RPA_V2_PREP_DOWNLOAD_CONCURRENCY)")
    parser.add_argument("--max-extractions", type=int, default=0, help="Max concurrent PDF extractions (0=use hooks default, env: RPA_V2_PREP_EXTRACT_CONCURRENCY)")
    parser.add_argument("--max-corpus-queries", type=int, default=0, help="Max concurrent corpus signal queries (0=use hooks default, env: RPA_V2_PREP_CORPUS_CONCURRENCY)")
    parser.add_argument("--reset-orphans", action="store_true", help="Reset claimed-but-never-checkpointed executions on startup (safe for single-daemon)")
    args = parser.parse_args()

    # CLI flags override env vars for phase caps
    global _MAX_PREP, _MAX_EXPENSIVE, _MAX_WRAP
    if args.max_prep > 0:
        _MAX_PREP = args.max_prep
    if args.max_expensive > 0:
        _MAX_EXPENSIVE = args.max_expensive
    if args.max_wrap > 0:
        _MAX_WRAP = args.max_wrap

    # CLI flags override hooks I/O concurrency defaults
    import research_paper_analysis_v2.hooks as _hooks
    if args.max_downloads > 0:
        _hooks.PREP_DOWNLOAD_CONCURRENCY = args.max_downloads
    if args.max_extractions > 0:
        _hooks.PREP_EXTRACT_CONCURRENCY = args.max_extractions
    if args.max_corpus_queries > 0:
        _hooks.PREP_CORPUS_CONCURRENCY = args.max_corpus_queries

    mig = await migrate_file_checkpoints_to_db(force=args.force_checkpoint_migration)
    if any(mig.values()):
        logger.info(
            "Checkpoint migration: dirs=%d latest=%d migrated=%d missing_snapshot=%d read_errors=%d skipped_existing_db=%d",
            mig["dirs_seen"],
            mig["latest_found"],
            mig["migrated"],
            mig["missing_snapshot"],
            mig["read_errors"],
            mig["skipped_existing_db"],
        )

    if args.migrate_checkpoints_only:
        print(
            "Checkpoint migration: "
            f"dirs={mig['dirs_seen']} latest={mig['latest_found']} migrated={mig['migrated']} "
            f"missing_snapshot={mig['missing_snapshot']} read_errors={mig['read_errors']} "
            f"skipped_existing_db={mig['skipped_existing_db']}"
        )
        return

    if args.reset_orphans:
        orphan_stats = reset_orphaned_executions()
        total_reset = sum(orphan_stats.values())
        if total_reset:
            logger.info("Reset orphans: %s", orphan_stats)
        else:
            logger.info("No orphaned executions found.")

    if args.seed_only:
        count = seed(limit=args.seed_limit)
        print(f"Seeded {count} executions")
        return

    if args.seed:
        count = seed(limit=args.seed_limit)
        logger.info("Startup seeding enabled; seeded %d executions", count)

    stats = await run_continuous(
        max_workers=args.workers,
        daily_budget=args.budget,
        prep_only=args.prep_only,
        poll_interval=args.poll_interval,
        seed_limit=args.seed_limit,
        single_pass=not args.daemon,
        seed_enabled=args.seed,
    )
    print(f"Prep: {stats['prep']}  Expensive: {stats['expensive']}  Wrap: {stats['wrap']}  "
          f"Resumed: {stats['resumed']}  Errors: {stats['errors']}")


if __name__ == "__main__":
    asyncio.run(main())
