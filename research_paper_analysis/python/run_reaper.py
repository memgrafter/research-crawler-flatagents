#!/usr/bin/env .venv/bin/python
"""
Run the stale worker reaper.

Finds workers that have missed heartbeats and releases their papers.

Usage:
    python run_reaper.py
    python run_reaper.py --threshold 120
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from flatagents import FlatMachine

CONFIG_DIR = Path(__file__).parent.parent / "config"


async def main():
    parser = argparse.ArgumentParser(description="Run stale worker reaper")
    parser.add_argument("--threshold", "-t", type=int, default=120, help="Stale threshold in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    config_path = CONFIG_DIR / "stale_worker_reaper.yml"
    
    print(f"Running stale worker reaper (threshold={args.threshold}s)")
    
    machine = FlatMachine(config_file=str(config_path))
    
    result = await machine.execute(input={
        "stale_threshold_seconds": args.threshold,
    })
    
    reaped = result.get("reaped_count", 0)
    print(f"\n✅ Reaper complete! Cleaned up {reaped} stale worker(s).")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
