#!/usr/bin/env python
"""
Run the stale worker reaper.

Finds workers that have missed heartbeats and releases their papers.

Usage:
    python run_reaper.py
    python run_reaper.py --threshold 120
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from flatmachines import FlatMachine
from research_paper_analysis.hooks import configure_log_file

CONFIG_DIR = Path(__file__).parent.parent / "config"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Run stale worker reaper")
    parser.add_argument("--threshold", "-t", type=int, default=120, help="Stale threshold in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    configure_log_file(arxiv_id="reaper")

    config_path = CONFIG_DIR / "stale_worker_reaper.yml"

    logger.info(f"Running stale worker reaper (threshold={args.threshold}s)")

    machine = FlatMachine(config_file=str(config_path))

    result = await machine.execute(input={})

    reaped = result.get("reaped_count", 0)
    logger.info(f"Reaper complete! Cleaned up {reaped} stale worker(s).")

    return result


if __name__ == "__main__":
    asyncio.run(main())
