#!/usr/bin/env -S uv run python
"""
Run the paper analysis parallelization checker.

Checks queue depth vs active workers and spawns new workers.

Usage:
    python run_checker.py                      # One-shot check
    python run_checker.py --daemon             # Daemon mode (polls for events)
    python run_checker.py --max-workers 5
"""

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from flatmachines import FlatMachine
from research_paper_analysis.hooks import configure_log_file, SYSTEM_LOG_DIR

CONFIG_DIR = Path(__file__).parent.parent / "config"
DB_PATH = Path(__file__).parent.parent.parent / "arxiv_crawler" / "data" / "arxiv.sqlite"

logger = logging.getLogger(__name__)


def _parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_header_json(error_text: str) -> dict:
    marker = "llm_error_headers="
    if marker not in error_text:
        return {}

    start = error_text.find(marker) + len(marker)
    header_text = error_text[start:]
    if " | " in header_text:
        header_text = header_text.split(" | ", 1)[0]
    header_text = header_text.strip()
    if not header_text:
        return {}

    try:
        parsed = json.loads(header_text)
    except json.JSONDecodeError:
        return {}

    if isinstance(parsed, dict):
        return {str(k).lower(): str(v) for k, v in parsed.items()}
    return {}


def _parse_status_code(error_text: str) -> int | None:
    match = re.search(r"status_code=(\d{3})", error_text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b([4-5]\d{2})\b", error_text)
    if match:
        return int(match.group(1))
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _delay_from_headers(headers: dict) -> int | None:
    retry_after = _parse_retry_after(headers.get("retry-after"))
    if retry_after:
        return retry_after

    reset_keys = (
        "x-ratelimit-reset-requests-minute",
        "x-ratelimit-reset-tokens-minute",
        "x-ratelimit-reset-requests-hour",
        "x-ratelimit-reset-tokens-hour",
        "x-ratelimit-reset-requests-day",
        "x-ratelimit-reset-tokens-day",
    )
    for key in reset_keys:
        value = _parse_retry_after(headers.get(key))
        if value:
            return value

    if headers.get("x-ratelimit-remaining-requests-minute") == "0" or headers.get(
        "x-ratelimit-remaining-tokens-minute"
    ) == "0":
        return 60
    if headers.get("x-ratelimit-remaining-requests-hour") == "0" or headers.get(
        "x-ratelimit-remaining-tokens-hour"
    ) == "0":
        return 3600
    if headers.get("x-ratelimit-remaining-requests-day") == "0" or headers.get(
        "x-ratelimit-remaining-tokens-day"
    ) == "0":
        return 86400

    return None


def _rate_limit_delay(conn: sqlite3.Connection) -> int | None:
    cursor = conn.execute(
        """
        SELECT error, COALESCE(finished_at, started_at, enqueued_at)
        FROM paper_queue
        WHERE error IS NOT NULL AND error != ''
        ORDER BY COALESCE(finished_at, started_at, enqueued_at) DESC
        LIMIT 50
        """
    )
    rows = cursor.fetchall()
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=10)

    for error_text, timestamp in rows:
        if not error_text:
            continue
        parsed_time = _parse_timestamp(timestamp)
        if parsed_time and (now - parsed_time) > window:
            continue

        status_code = _parse_status_code(error_text)
        headers = _extract_header_json(error_text)
        delay = _delay_from_headers(headers) if headers else None

        if delay:
            return delay
        if status_code == 429:
            return 60

    return None


async def run_scaling_check(max_workers: int) -> dict:
    """Run a single scaling check and spawn workers if needed."""
    config_path = CONFIG_DIR / "parallelization_checker.yml"
    machine = FlatMachine(config_file=str(config_path))

    result = await machine.execute(input={
        "max_workers": max_workers,
    })

    return result


async def run_reaper_check(stale_threshold_seconds: int) -> dict:
    """Run a stale worker reaper pass."""
    config_path = CONFIG_DIR / "stale_worker_reaper.yml"
    machine = FlatMachine(config_file=str(config_path))

    result = await machine.execute(input={})

    return result


async def daemon_mode(
    db_path: Path,
    max_workers: int,
    poll_interval: float = 0.1,
    reap_interval: float = 30.0,
    reap_threshold: int = 120,
):
    """Run as daemon, polling for scaling events."""
    conn = sqlite3.connect(str(db_path))
    last_id = 0
    idle_count = 0
    idle_exit_threshold = 50  # Exit after 50 consecutive idle checks (~5 seconds)
    last_reap = time.monotonic() - reap_interval
    scale_pause_until = 0.0

    # Get current max id to avoid processing old events
    cursor = conn.execute("SELECT MAX(id) FROM scaling_events")
    row = cursor.fetchone()
    if row and row[0]:
        last_id = row[0]

    logger.info(
        "Scale daemon started (poll=%.1fs, max_workers=%d, reap_interval=%.0fs, reap_threshold=%ds)",
        poll_interval, max_workers, reap_interval, reap_threshold,
    )
    logger.info("Watching for scaling events (starting from id=%d)", last_id)

    while True:
        try:
            if reap_interval > 0 and (time.monotonic() - last_reap) >= reap_interval:
                try:
                    result = await run_reaper_check(reap_threshold)
                    reaped = result.get("reaped_count", 0)
                    if reaped:
                        logger.info("Reaped %d stale worker(s)", reaped)
                except Exception as e:
                    logger.error("Error running reaper: %s", e)
                last_reap = time.monotonic()

            now = time.monotonic()
            if now >= scale_pause_until:
                delay = _rate_limit_delay(conn)
                if delay:
                    scale_pause_until = now + delay
                    logger.warning("Rate limit headers detected; pausing scaling for %ds", delay)

            # Check for new events
            cursor = conn.execute(
                "SELECT MAX(id) FROM scaling_events WHERE id > ?",
                (last_id,),
            )
            row = cursor.fetchone()

            if row and row[0]:
                last_id = row[0]
                idle_count = 0  # Reset idle counter
                logger.info("Scaling event detected (id=%d), checking pool...", last_id)

                pause_remaining = scale_pause_until - time.monotonic()
                if pause_remaining > 0:
                    logger.warning("Scaling paused for %.0fs due to rate limits", pause_remaining)
                else:
                    result = await run_scaling_check(max_workers)

                    spawned = result.get("spawned", 0)
                    queue_depth = result.get("queue_depth", 0)
                    active_workers = result.get("active_workers", 0)

                    logger.info("Queue: %s, Active: %s, Spawned: %s", queue_depth, active_workers, spawned)

                # Exit if queue empty and no workers active
                if pause_remaining <= 0 and queue_depth == 0 and active_workers == 0:
                    logger.info("All work complete (queue=0, workers=0). Daemon exiting.")
                    break
            else:
                idle_count += 1

                # Periodically check if all work is done even without events
                if idle_count >= idle_exit_threshold:
                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM paper_queue WHERE status IN ('pending', 'processing')"
                    )
                    pending = cursor.fetchone()[0]

                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM worker_registry WHERE status = 'active'"
                    )
                    active = cursor.fetchone()[0]

                    if pending == 0 and active == 0:
                        logger.info("All work complete. Daemon exiting.")
                        break

                    if pending > 0:
                        pause_remaining = scale_pause_until - time.monotonic()
                        if pause_remaining > 0:
                            logger.warning("Scaling paused for %.0fs due to rate limits", pause_remaining)
                        else:
                            result = await run_scaling_check(max_workers)
                            spawned = result.get("spawned", 0)
                            queue_depth = result.get("queue_depth", pending)
                            active_workers = result.get("active_workers", active)
                            logger.info("Queue: %s, Active: %s, Spawned: %s", queue_depth, active_workers, spawned)

                    idle_count = 0  # Reset and keep checking

            await asyncio.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Daemon stopped.")
            break
        except Exception as e:
            logger.error("Error in daemon loop: %s", e)
            await asyncio.sleep(1)  # Back off on error


async def main():
    parser = argparse.ArgumentParser(description="Run paper analysis parallelization checker")
    parser.add_argument("--max-workers", "-m", type=int, default=3, help="Maximum workers")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run as polling daemon")
    parser.add_argument("--poll-interval", "-p", type=float, default=0.1, help="Poll interval in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--reap-interval",
        type=float,
        default=30.0,
        help="Seconds between stale worker reaper runs (default: 30)",
    )
    parser.add_argument(
        "--reap-threshold",
        type=int,
        default=120,
        help="Stale worker heartbeat threshold in seconds (default: 120)",
    )
    args = parser.parse_args()

    configure_log_file(arxiv_id="checker", log_dir=SYSTEM_LOG_DIR)

    if args.daemon:
        await daemon_mode(
            DB_PATH,
            args.max_workers,
            args.poll_interval,
            args.reap_interval,
            args.reap_threshold,
        )
    else:
        logger.info("Running parallelization checker (max_workers=%d)", args.max_workers)

        result = await run_scaling_check(args.max_workers)

        spawned = result.get("spawned", 0)
        queue_depth = result.get("queue_depth", 0)
        active_workers = result.get("active_workers", 0)

        logger.info("Checker complete! Queue: %s, Active: %s, Spawned: %s", queue_depth, active_workers, spawned)

        return result


if __name__ == "__main__":
    asyncio.run(main())
