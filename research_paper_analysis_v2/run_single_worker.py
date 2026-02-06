#!/usr/bin/env -S uv run python
"""Run one v2 worker against the existing queue/database schema."""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from flatmachines import FlatMachine


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run one v2 paper worker")
    parser.add_argument(
        "--worker-id",
        "-w",
        default=None,
        help="Worker ID (auto-generated if omitted)",
    )
    parser.add_argument(
        "--launch-only",
        action="store_true",
        help="Use launcher machine (fire-and-forget semantics)",
    )
    args = parser.parse_args()

    worker_id = args.worker_id or f"paper-worker-v2-{uuid.uuid4().hex[:8]}"
    config_name = "single_worker_launcher.yml" if args.launch_only else "paper_analysis_worker.yml"
    config_path = Path(__file__).parent / "config" / config_name

    machine = FlatMachine(config_file=str(config_path))
    result = await machine.execute(input={"worker_id": worker_id})

    print(f"Worker: {result.get('worker_id', worker_id)}")
    print(f"Status: {result.get('status', 'completed')}")

    if not args.launch_only:
        paper_id = result.get("paper_id")
        queue_id = result.get("queue_id")
        if paper_id is not None:
            print(f"Paper ID: {paper_id} (queue {queue_id})")


if __name__ == "__main__":
    asyncio.run(main())
