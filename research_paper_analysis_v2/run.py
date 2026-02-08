#!/usr/bin/env -S uv run python
"""V2 runner: three-phase pipeline with budget-aware scheduling.

Phase 1 (prep):      download PDF, extract text, corpus signals, key_outcome  [cheap model]
Phase 2 (expensive): parallel why_hypothesis + reproduction + open_questions   [pony-alpha only]
Phase 3 (wrap):      limits, assembly, judge, repair, frontmatter             [cheap model]

Budget strategy: maximize expensive model utilization.
- Expensive gets priority for worker slots when prepped papers are available.
- Wrap clears analyzed papers quickly (all cheap).
- Prep builds buffer during cheap-only windows.

Usage:
    python run.py                           # One pass, default settings
    python run.py --workers 5 --daemon      # Daemon mode, poll until done
    python run.py --prep-only --workers 10  # Only run prep phase (fill buffer)
    python run.py --seed-only               # Only seed from arxiv DB, no execution
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("v2_runner")

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

# Machine config paths
PREP_CONFIG = str(CONFIG_DIR / "prep_machine.yml")
EXPENSIVE_CONFIG = str(CONFIG_DIR / "expensive_machine.yml")
WRAP_CONFIG = str(CONFIG_DIR / "wrap_machine.yml")


# ---------------------------------------------------------------------------
# V2 executions DB
# ---------------------------------------------------------------------------

def get_v2_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = os.environ.get("V2_EXECUTIONS_DB_PATH", str(DATA_DIR / "v2_executions.sqlite"))
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    if SCHEMA_FILE.exists():
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    # Migrate: add expensive_output if missing (existing DBs)
    _migrate_v2_db(conn)
    return conn


def _migrate_v2_db(conn: sqlite3.Connection) -> None:
    """Add columns introduced after initial schema."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(executions)").fetchall()}
    if "expensive_output" not in cols:
        conn.execute("ALTER TABLE executions ADD COLUMN expensive_output TEXT")
        conn.commit()
        logger.info("Migrated: added expensive_output column")


def get_arxiv_db() -> Optional[sqlite3.Connection]:
    db_path = os.environ.get(
        "ARXIV_DB_PATH",
        str(PROJECT_ROOT.parent / "arxiv_crawler" / "data" / "arxiv.sqlite"),
    )
    if not Path(db_path).exists():
        logger.warning("Arxiv DB not found: %s", db_path)
        return None
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


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
    arxiv_conn.close()

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

def find_incomplete_executions(machine_name: str) -> List[str]:
    """Scan checkpoint dir for incomplete executions of a given machine."""
    if not CHECKPOINT_DIR.exists():
        return []

    incomplete = []
    for latest_file in CHECKPOINT_DIR.glob("*/latest"):
        execution_id = latest_file.parent.name
        try:
            key = latest_file.read_text(encoding="utf-8").strip()
            snapshot_path = CHECKPOINT_DIR / key
            if not snapshot_path.exists():
                continue
            snapshot = json.loads(snapshot_path.read_bytes())
            if snapshot.get("machine_name") != machine_name:
                continue
            if snapshot.get("event") == "machine_end":
                continue
            current_state = snapshot.get("current_state", "")
            if current_state in FINAL_STATES:
                continue
            incomplete.append(execution_id)
        except Exception as exc:
            logger.warning("Error reading checkpoint %s: %s", execution_id, exc)
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
            ORDER BY created_at ASC
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
            ORDER BY created_at ASC
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
            ORDER BY created_at ASC
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
    from flatmachines import FlatMachine, LocalFileBackend

    execution_id = execution["execution_id"]
    logger.info("PREP %s: %s", execution["arxiv_id"], execution["title"][:60])

    try:
        machine = FlatMachine(
            config_file=PREP_CONFIG,
            persistence=LocalFileBackend(base_dir=str(CHECKPOINT_DIR)),
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
    from flatmachines import FlatMachine, LocalFileBackend

    execution_id = execution["execution_id"]
    arxiv_id = execution["arxiv_id"]

    prep_output = _parse_json_field(execution.get("prep_output"), "prep_output")
    if not prep_output:
        logger.error("EXPENSIVE SKIP %s: no prep_output", arxiv_id)
        return False

    source_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    logger.info("EXPENSIVE %s: %s", arxiv_id, execution["title"][:60])

    try:
        machine = FlatMachine(
            config_file=EXPENSIVE_CONFIG,
            persistence=LocalFileBackend(base_dir=str(CHECKPOINT_DIR)),
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
    from flatmachines import FlatMachine, LocalFileBackend

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
        machine = FlatMachine(
            config_file=WRAP_CONFIG,
            persistence=LocalFileBackend(base_dir=str(CHECKPOINT_DIR)),
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
    from flatmachines import FlatMachine, LocalFileBackend

    logger.info("RESUME %s (%s)", execution_id, machine_name)

    try:
        machine = FlatMachine(
            config_file=config_file,
            persistence=LocalFileBackend(base_dir=str(CHECKPOINT_DIR)),
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
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main run logic
# ---------------------------------------------------------------------------

async def run_once(
    max_workers: int = 3,
    daily_budget: int = 1000,
    prep_only: bool = False,
    min_buffer: int = 20,
) -> Dict[str, int]:
    """Run one pass: resume incomplete, then schedule new work.

    Priority order (maximize expensive model utilization):
    1. Resume incomplete machines (any phase)
    2. Start expensive work (pony-alpha — highest priority for new work)
    3. Start wrap work (cheap, clears pipeline)
    4. Start prep work (cheap, builds buffer)
    """
    conn = get_v2_db()
    stats = {
        "prep_started": 0, "prep_resumed": 0,
        "expensive_started": 0, "expensive_resumed": 0,
        "wrap_started": 0, "wrap_resumed": 0,
    }

    # Release stale claims
    release_stale(conn, "prepping", "pending", max_age_minutes=60)
    release_stale(conn, "analyzing", "prepped", max_age_minutes=120)
    release_stale(conn, "wrapping", "analyzed", max_age_minutes=60)

    usage = get_daily_usage(conn)
    remaining_budget = max(daily_budget - usage["total"], 0)

    if remaining_budget <= 0:
        logger.info("Daily budget exhausted (%d/%d)", usage["total"], daily_budget)
        return stats

    # Count current state
    status_counts = {}
    for row in conn.execute("SELECT status, COUNT(*) as cnt FROM executions GROUP BY status").fetchall():
        status_counts[row["status"]] = row["cnt"]

    pending = status_counts.get("pending", 0)
    prepped = status_counts.get("prepped", 0)
    analyzed = status_counts.get("analyzed", 0)
    prepping = status_counts.get("prepping", 0)
    analyzing = status_counts.get("analyzing", 0)
    wrapping = status_counts.get("wrapping", 0)

    logger.info(
        "Status: pending=%d prepping=%d prepped=%d analyzing=%d analyzed=%d wrapping=%d | budget=%d/%d",
        pending, prepping, prepped, analyzing, analyzed, wrapping, remaining_budget, daily_budget,
    )

    sem = asyncio.Semaphore(max_workers)
    tasks: List[asyncio.Task] = []
    budget_used = 0

    async def run_with_sem(coro):
        async with sem:
            return await coro

    # 1. Resume incomplete machines (depth-first completion)
    for machine_name, config_file in [
        ("expensive-pipeline", EXPENSIVE_CONFIG),
        ("wrap-pipeline", WRAP_CONFIG),
        ("prep-pipeline", PREP_CONFIG),
    ]:
        stat_key = machine_name.split("-")[0] + "_resumed"
        for exec_id in find_incomplete_executions(machine_name)[:max_workers]:
            if len(tasks) >= max_workers:
                break
            task = asyncio.create_task(run_with_sem(
                resume_machine(exec_id, machine_name, config_file)
            ))
            tasks.append(task)
            stats[stat_key] += 1

    if prep_only:
        # Skip expensive and wrap, only do prep
        pass
    else:
        # 2. Expensive work — highest priority for new slots (pure pony-alpha)
        expensive_slots = min(
            max(max_workers - len(tasks), 0),
            prepped,
            max((remaining_budget - budget_used) // EXPENSIVE_REQS, 0),
        )
        if expensive_slots > 0:
            claimed = claim_for_expensive(conn, expensive_slots)
            for ex in claimed:
                task = asyncio.create_task(run_with_sem(run_expensive(ex)))
                tasks.append(task)
                stats["expensive_started"] += 1
                budget_used += EXPENSIVE_REQS

        # 3. Wrap work — clear analyzed backlog (all cheap, fast)
        wrap_slots = min(
            max(max_workers - len(tasks), 0),
            analyzed,
            max((remaining_budget - budget_used) // WRAP_REQS, 0),
        )
        if wrap_slots > 0:
            claimed = claim_for_wrap(conn, wrap_slots)
            for ex in claimed:
                task = asyncio.create_task(run_with_sem(run_wrap(ex)))
                tasks.append(task)
                stats["wrap_started"] += 1
                budget_used += WRAP_REQS

    # 4. Prep work — fill buffer with remaining slots
    prep_slots = min(
        max(max_workers - len(tasks), 0),
        pending,
        max((remaining_budget - budget_used) // PREP_REQS, 0),
    )
    if prep_slots > 0:
        claimed = claim_for_prep(conn, prep_slots)
        for ex in claimed:
            task = asyncio.create_task(run_with_sem(run_prep(ex)))
            tasks.append(task)
            stats["prep_started"] += 1
            budget_used += PREP_REQS

    # Wait for all
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Task %d failed: %s", i, result)

    # Update daily usage estimate
    total_cheap = (
        (stats["prep_started"] + stats["prep_resumed"]) * PREP_REQS
        + (stats["wrap_started"] + stats["wrap_resumed"]) * WRAP_REQS
    )
    total_expensive = (stats["expensive_started"] + stats["expensive_resumed"]) * EXPENSIVE_REQS
    increment_daily_usage(conn, cheap=total_cheap, expensive=total_expensive)

    return stats


async def run_daemon(
    max_workers: int = 3,
    daily_budget: int = 1000,
    poll_interval: float = 10.0,
    prep_only: bool = False,
    min_buffer: int = 20,
) -> None:
    """Daemon loop: seed → run → sleep → repeat."""
    while True:
        seed()

        stats = await run_once(
            max_workers=max_workers,
            daily_budget=daily_budget,
            prep_only=prep_only,
            min_buffer=min_buffer,
        )

        total_work = sum(stats.values())
        logger.info(
            "Pass complete: prep=%d/%d expensive=%d/%d wrap=%d/%d",
            stats["prep_started"], stats["prep_resumed"],
            stats["expensive_started"], stats["expensive_resumed"],
            stats["wrap_started"], stats["wrap_resumed"],
        )

        # Check if all work is done
        conn = get_v2_db()
        active = conn.execute(
            "SELECT COUNT(*) FROM executions WHERE status NOT IN ('done', 'failed')"
        ).fetchone()[0]
        usage = get_daily_usage(conn)

        if active == 0:
            logger.info("All work complete. Daemon exiting.")
            return

        if usage["total"] >= daily_budget:
            logger.info("Daily budget exhausted (%d/%d). Daemon exiting.", usage["total"], daily_budget)
            return

        if total_work == 0:
            logger.info("No work done this pass. Waiting %ds...", poll_interval)

        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="V2 paper analysis runner")
    parser.add_argument("-w", "--workers", type=int, default=3, help="Max concurrent workers")
    parser.add_argument("-b", "--budget", type=int, default=1000, help="Daily request budget")
    parser.add_argument("-d", "--daemon", action="store_true", help="Run in daemon mode")
    parser.add_argument("-p", "--poll-interval", type=float, default=10.0, help="Daemon poll interval (seconds)")
    parser.add_argument("--prep-only", action="store_true", help="Only run prep phase")
    parser.add_argument("--seed-only", action="store_true", help="Only seed from arxiv DB")
    parser.add_argument("--min-buffer", type=int, default=20, help="Min prepped papers before prioritizing analysis")
    parser.add_argument("--seed-limit", type=int, default=500, help="Max papers to seed per pass")
    args = parser.parse_args()

    if args.seed_only:
        count = seed(limit=args.seed_limit)
        print(f"Seeded {count} executions")
        return

    # Always seed first
    seed(limit=args.seed_limit)

    if args.daemon:
        await run_daemon(
            max_workers=args.workers,
            daily_budget=args.budget,
            poll_interval=args.poll_interval,
            prep_only=args.prep_only,
            min_buffer=args.min_buffer,
        )
    else:
        stats = await run_once(
            max_workers=args.workers,
            daily_budget=args.budget,
            prep_only=args.prep_only,
            min_buffer=args.min_buffer,
        )
        print(f"Prep: {stats['prep_started']} new, {stats['prep_resumed']} resumed")
        print(f"Expensive: {stats['expensive_started']} new, {stats['expensive_resumed']} resumed")
        print(f"Wrap: {stats['wrap_started']} new, {stats['wrap_resumed']} resumed")


if __name__ == "__main__":
    asyncio.run(main())
