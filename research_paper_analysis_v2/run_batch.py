#!/usr/bin/env -S uv run python
"""Lean batch scheduler runner for research_paper_analysis_v2."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from flatmachines import FlatMachine


async def run_scheduler_once(max_workers: int) -> dict:
    config_path = Path(__file__).parent / "config" / "batch_scheduler.yml"
    machine = FlatMachine(config_file=str(config_path))
    result = await machine.execute(input={"max_workers": max_workers})
    return result


async def run_daemon(max_workers: int, poll_interval: float) -> None:
    while True:
        result = await run_scheduler_once(max_workers)

        queue_depth = int(result.get("queue_depth") or 0)
        active_workers = int(result.get("active_workers") or 0)
        spawned_count = int(result.get("spawned_count") or 0)

        print(f"Queue: {queue_depth}, Active: {active_workers}, Spawned: {spawned_count}")

        if queue_depth == 0 and active_workers == 0 and spawned_count == 0:
            print("All work complete. Daemon exiting.")
            return

        await asyncio.sleep(poll_interval)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run v2 batch scheduler")
    parser.add_argument("-w", "--workers", type=int, default=3, help="Max concurrent workers")
    parser.add_argument("-d", "--daemon", action="store_true", help="Run polling daemon mode")
    parser.add_argument("-p", "--poll-interval", type=float, default=2.0, help="Daemon poll interval in seconds")
    args = parser.parse_args()

    if args.daemon:
        await run_daemon(args.workers, args.poll_interval)
        return

    result = await run_scheduler_once(args.workers)
    queue_depth = int(result.get("queue_depth") or 0)
    active_workers = int(result.get("active_workers") or 0)
    spawned_count = int(result.get("spawned_count") or 0)
    print(f"Queue: {queue_depth}, Active: {active_workers}, Spawned: {spawned_count}")


if __name__ == "__main__":
    asyncio.run(main())
