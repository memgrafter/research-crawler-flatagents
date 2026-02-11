"""
Time-sliced backfill runner for the arXiv crawler.

Usage:
    python -m arxiv_crawler.backfill --start-date 2025-12-01 --end-date 2025-12-31
"""

import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from flatmachines import FlatMachine, setup_logging, get_logger

from .hooks import CrawlerHooks
from .main import parse_categories

setup_logging(level="INFO")
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Time-sliced arXiv backfill")
    parser.add_argument("--db-path", default="data/arxiv.sqlite", help="SQLite database path")
    parser.add_argument(
        "--categories",
        default="cs.CL,cs.AI,cs.LG,stat.ML,cs.IR,cs.RO,cs.SE,cs.HC",
        help="Comma-separated arXiv categories",
    )
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--window-days", type=int, default=1, help="Days per slice")
    parser.add_argument("--max-results", type=int, default=500, help="Max results per slice")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=500,
        help="Log progress every N entries (0 to disable)",
    )
    parser.add_argument(
        "--throttle-seconds",
        type=float,
        default=None,
        help="Optional delay between API requests (seconds)",
    )
    parser.add_argument(
        "--use-submitted-date",
        action="store_true",
        help="Filter using submittedDate ranges instead of updated-at ranges",
    )
    parser.add_argument(
        "--slice-retries",
        type=int,
        default=3,
        help="Retry a slice on failure before aborting",
    )
    parser.add_argument(
        "--slice-retry-sleep",
        type=float,
        default=60.0,
        help="Base sleep between slice retries (seconds)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip writes to SQLite")
    parser.add_argument("--sleep-seconds", type=float, default=2.0, help="Sleep between slices")
    return parser.parse_args()


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def run_slice(
    config_path: Path,
    db_path: str,
    categories: List[str],
    since_iso: Optional[str],
    until_iso: Optional[str],
    submitted_since: Optional[str],
    submitted_until: Optional[str],
    max_results: int,
    progress_every: int,
    throttle_seconds: Optional[float],
    dry_run: bool,
) -> dict:
    machine = FlatMachine(config_file=str(config_path), hooks=CrawlerHooks())
    payload = {
        "db_path": db_path,
        "categories": categories,
        "max_results": max_results,
        "since": since_iso,
        "until": until_iso,
        "submitted_since": submitted_since,
        "submitted_until": submitted_until,
        "progress_every": progress_every,
        "throttle_seconds": throttle_seconds,
        "dry_run": dry_run,
    }
    return asyncio.run(machine.execute(input=payload))


def main() -> None:
    args = parse_args()
    start_dt = parse_date(args.start_date)
    end_dt = parse_date(args.end_date)
    if end_dt < start_dt:
        raise ValueError("end-date must be after start-date")

    config_path = Path(__file__).parent.parent.parent / "config" / "machine.yml"
    categories = parse_categories(args.categories)

    current = start_dt
    while current <= end_dt:
        logger.info("Processing date: %s", current.strftime("%Y-%m-%d"))
        slice_end = min(current + timedelta(days=args.window_days), end_dt + timedelta(days=1))
        since_iso = current.isoformat()
        until_iso = (slice_end - timedelta(seconds=1)).isoformat()
        if args.use_submitted_date:
            submitted_since = since_iso
            submitted_until = until_iso
            since_iso = None
            until_iso = None
        else:
            submitted_since = None
            submitted_until = None
        logger.info("Backfill slice: %s to %s", current.isoformat(), (slice_end - timedelta(seconds=1)).isoformat())
        attempt = 0
        while True:
            try:
                result = run_slice(
                    config_path=config_path,
                    db_path=args.db_path,
                    categories=categories,
                    since_iso=since_iso,
                    until_iso=until_iso,
                    submitted_since=submitted_since,
                    submitted_until=submitted_until,
                    max_results=args.max_results,
                    progress_every=args.progress_every,
                    throttle_seconds=args.throttle_seconds,
                    dry_run=args.dry_run,
                )
                break
            except Exception as exc:  # pylint: disable=broad-except
                attempt += 1
                if attempt > args.slice_retries:
                    raise
                sleep_for = args.slice_retry_sleep * attempt
                logger.warning("Slice failed (%s); retrying in %.1fs", exc, sleep_for)
                time.sleep(sleep_for)
        logger.info("Slice result: %s", result)
        current = slice_end
        if current <= end_dt:
            time.sleep(args.sleep_seconds)


if __name__ == "__main__":
    main()
