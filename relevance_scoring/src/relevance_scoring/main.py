"""
Local embedding-based FMR scoring.

Usage:
    python -m relevance_scoring.main --limit 200 --dry-run
    ./run.sh -- --limit 500 --since 2025-01-01
"""

import argparse
import asyncio
from pathlib import Path

from flatagents import FlatMachine, setup_logging, get_logger

from .hooks import ScoringHooks

setup_logging(level="INFO")
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local embedding-based FMR scoring"
    )
    parser.add_argument(
        "--db-path",
        default="../arxiv_crawler/data/arxiv.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--config-path",
        default="../relevance_scoring.yml",
        help="Path to relevance_scoring.yml",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO date or timestamp to filter target papers (inclusive)",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="ISO date or timestamp to filter target papers (inclusive)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max number of papers to score",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Embedding batch size",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score without writing to SQLite",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / "machine.yml"
    machine = FlatMachine(config_file=str(config_path), hooks=ScoringHooks())

    payload = {
        "db_path": args.db_path,
        "config_path": args.config_path,
        "since": args.since,
        "until": args.until,
        "limit": args.limit,
        "batch_size": args.batch_size,
        "dry_run": args.dry_run,
    }

    logger.info("Starting FMR scoring")
    result = await machine.execute(input=payload)
    logger.info("Scoring finished: %s", result)
    return result


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
