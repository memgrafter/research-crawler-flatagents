"""
Reverse citation enrichment using OpenAlex.

Usage:
    python -m reverse_citation_enrichment.main --limit 200 --dry-run
    ./run.sh -- --limit 500 --since 2025-01-01
"""

import argparse
import asyncio
from pathlib import Path

from flatagents import FlatMachine, setup_logging, get_logger

from .hooks import CitationHooks

setup_logging(level="INFO")
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reverse citation enrichment using OpenAlex"
    )
    parser.add_argument(
        "--db-path",
        default="../arxiv_crawler/data/arxiv.sqlite",
        help="SQLite database path",
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
        help="Max number of papers to enrich",
    )
    parser.add_argument(
        "--cooldown-days",
        type=int,
        default=30,
        help="Skip papers enriched in the last N days",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Max OR-batched IDs per request (OpenAlex supports up to 100)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Max pages per cited-by query",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Concurrent OpenAlex requests (rate-limited to 10 req/sec)",
    )
    parser.add_argument(
        "--use-doi",
        action="store_true",
        help="Attempt DOI-based lookups before title search (optional)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse without writing to SQLite",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Minimum FMR score from paper_relevance (e.g., 0.5)",
    )
    parser.add_argument(
        "--no-authors-only",
        action="store_true",
        help="Only process papers that haven't been author-enriched yet",
    )
    parser.add_argument(
        "--request-timeout-sec",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds",
    )
    parser.add_argument(
        "--resolve-timeout-sec",
        type=float,
        default=45.0,
        help="Per-paper timeout for OpenAlex resolution",
    )
    parser.add_argument(
        "--citations-timeout-sec",
        type=float,
        default=120.0,
        help="Per-paper timeout for citation fetch",
    )
    parser.add_argument(
        "--progress-log-sec",
        type=int,
        default=60,
        help="Emit progress logs at least every N seconds",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / "machine.yml"
    machine = FlatMachine(config_file=str(config_path), hooks=CitationHooks())

    payload = {
        "db_path": args.db_path,
        "since": args.since,
        "until": args.until,
        "limit": args.limit,
        "cooldown_days": args.cooldown_days,
        "batch_size": args.batch_size,
        "max_pages": args.max_pages,
        "concurrency": args.concurrency,
        "use_doi": args.use_doi,
        "dry_run": args.dry_run,
        "min_score": args.min_score,
        "no_authors_only": args.no_authors_only,
        "request_timeout_sec": args.request_timeout_sec,
        "resolve_timeout_sec": args.resolve_timeout_sec,
        "citations_timeout_sec": args.citations_timeout_sec,
        "progress_log_sec": args.progress_log_sec,
    }

    logger.info("Starting reverse citation enrichment")
    result = await machine.execute(input=payload)
    logger.info("Enrichment finished: %s", result)
    return result


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
