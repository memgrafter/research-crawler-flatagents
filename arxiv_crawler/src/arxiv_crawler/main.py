"""
arXiv research crawler for LLM-focused papers.

Usage:
    python -m arxiv_crawler.main --max-results 100
    ./run.sh -- --max-results 50 --dry-run
"""

import argparse
import asyncio
from pathlib import Path
from typing import List

from flatagents import FlatMachine, setup_logging, get_logger

from .hooks import CrawlerHooks

setup_logging(level="INFO")
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="arXiv LLM research crawler")
    parser.add_argument(
        "--db-path",
        default="data/arxiv.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--categories",
        default="cs.CL,cs.AI,cs.LG,stat.ML,cs.IR,cs.RO,cs.SE,cs.HC",
        help="Comma-separated arXiv categories",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=100,
        help="Max number of entries to fetch",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp to filter updated papers (e.g., 2025-01-01T00:00:00Z)",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="ISO timestamp to exclude updates after this time (e.g., 2025-12-31T23:59:59Z)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=500,
        help="Log progress every N entries (0 to disable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse without writing to SQLite",
    )
    return parser.parse_args()


def parse_categories(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def run(args: argparse.Namespace) -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / "machine.yml"
    machine = FlatMachine(config_file=str(config_path), hooks=CrawlerHooks())

    payload = {
        "db_path": args.db_path,
        "categories": parse_categories(args.categories),
        "max_results": args.max_results,
        "since": args.since,
        "until": args.until,
        "progress_every": args.progress_every,
        "dry_run": args.dry_run,
    }

    logger.info("Starting crawler run")
    result = await machine.execute(input=payload)
    logger.info("Crawler finished: %s", result)
    return result


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
