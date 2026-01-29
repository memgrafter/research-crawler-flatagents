#!/usr/bin/env python3
"""
Run the paper analysis parallelization checker.

Checks queue depth vs active workers and spawns new workers.

Usage:
    python run_checker.py
    python run_checker.py --max-workers 5
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
    parser = argparse.ArgumentParser(description="Run paper analysis parallelization checker")
    parser.add_argument("--max-workers", "-m", type=int, default=3, help="Maximum workers")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    config_path = CONFIG_DIR / "parallelization_checker.yml"
    
    print(f"Running parallelization checker (max_workers={args.max_workers})")
    
    machine = FlatMachine(config_file=str(config_path))
    
    result = await machine.execute(input={
        "max_workers": args.max_workers,
    })
    
    spawned = result.get("spawned", 0)
    queue_depth = result.get("queue_depth", 0)
    active_workers = result.get("active_workers", 0)
    
    print(f"\n✅ Checker complete!")
    print(f"   Queue depth: {queue_depth}")
    print(f"   Active workers: {active_workers}")
    print(f"   Spawned: {spawned}")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
