#!/usr/bin/env .venv/bin/python
"""
Run a single paper analysis worker.

Claims one paper, analyzes it, then exits.

Usage:
    python run_worker.py
    python run_worker.py --worker-id my-worker-123
"""

import argparse
import asyncio
import uuid
import sys
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from flatagents import FlatMachine

CONFIG_DIR = Path(__file__).parent.parent / "config"


async def main():
    parser = argparse.ArgumentParser(description="Run a single paper analysis worker")
    parser.add_argument("--worker-id", "-w", default=None, help="Worker ID (auto-generated if not provided)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    worker_id = args.worker_id or f"worker-{uuid.uuid4().hex[:8]}"
    config_path = CONFIG_DIR / "paper_analysis_worker.yml"
    
    print(f"Starting worker {worker_id}")
    
    machine = FlatMachine(config_file=str(config_path))
    
    result = await machine.execute(input={
        "worker_id": worker_id,
    })
    
    status = result.get("status", "completed")
    paper_id = result.get("paper_id")
    
    if paper_id:
        print(f"\n✅ Worker complete! Processed paper {paper_id}")
    else:
        print(f"\n⚠️ Worker complete: {status}")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
