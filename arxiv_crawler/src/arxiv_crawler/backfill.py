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
from typing import List

from flatagents import FlatMachine, setup_logging, get_logger

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
    parser.add_argument("--dry-run", action="store_true", help="Skip writes to SQLite")
    parser.add_argument("--sleep-seconds", type=float, default=2.0, help="Sleep between slices")
    return parser.parse_args()


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def run_slice(
    config_path: Path,
    db_path: str,
    categories: List[str],
    since_iso: str,
    until_iso: str,
    max_results: int,
    progress_every: int,
    dry_run: bool,
) -> dict:
    machine = FlatMachine(config_file=str(config_path), hooks=CrawlerHooks())
    payload = {
        "db_path": db_path,
        "categories": categories,
        "max_results": max_results,
        "since": since_iso,
        "until": until_iso,
        "progress_every": progress_every,
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
        slice_end = min(current + timedelta(days=args.window_days), end_dt + timedelta(days=1))
        since_iso = current.isoformat()
        until_iso = (slice_end - timedelta(seconds=1)).isoformat()
        logger.info("Backfill slice: %s to %s", since_iso, until_iso)
        result = run_slice(
            config_path=config_path,
            db_path=args.db_path,
            categories=categories,
            since_iso=since_iso,
            until_iso=until_iso,
            max_results=args.max_results,
            progress_every=args.progress_every,
            dry_run=args.dry_run,
        )
        logger.info("Slice result: %s", result)
        current = slice_end
        if current <= end_dt:
            time.sleep(args.sleep_seconds)


if __name__ == "__main__":
    main()
