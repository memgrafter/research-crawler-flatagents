"""
Local embedding-based FMR 2024 scoring.

Usage:
    python -m relevance_scoring_2024.main --limit 200 --dry-run
    ./run.sh -- --limit 500 --year-prefix 24
"""

import argparse
import asyncio
from pathlib import Path

from flatmachines import FlatMachine, setup_logging, get_logger

from .hooks import ScoringHooks

setup_logging(level="INFO")
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local embedding-based FMR 2024 scoring"
    )
    parser.add_argument(
        "--db-path",
        default="../arxiv_crawler/data/arxiv.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--config-path",
        default="../relevance_scoring_2024.yml",
        help="Path to relevance_scoring_2024.yml",
    )
    parser.add_argument(
        "--year-prefix",
        default="24",
        help="arXiv ID year prefix to score (default: 24)",
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
        "--rescore-existing",
        action="store_true",
        help="Recompute fmr_2024 even when already present",
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
        "year_prefix": args.year_prefix,
        "since": args.since,
        "until": args.until,
        "limit": args.limit,
        "batch_size": args.batch_size,
        "rescore_existing": args.rescore_existing,
        "dry_run": args.dry_run,
    }

    logger.info("Starting FMR 2024 scoring")
    result = await machine.execute(input=payload)
    logger.info("Scoring finished: %s", result)
    return result


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
