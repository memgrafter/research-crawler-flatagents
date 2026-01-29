#!/usr/bin/env python3
"""
Run the paper analysis parallelization checker.

Checks queue depth vs active workers and spawns new workers.

Usage:
    python run_checker.py                      # One-shot check
    python run_checker.py --daemon             # Daemon mode (polls for events)
    python run_checker.py --max-workers 5
"""

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from flatagents import FlatMachine

CONFIG_DIR = Path(__file__).parent.parent / "config"
DB_PATH = Path(__file__).parent.parent.parent / "arxiv_crawler" / "data" / "arxiv.sqlite"


async def run_scaling_check(max_workers: int) -> dict:
    """Run a single scaling check and spawn workers if needed."""
    config_path = CONFIG_DIR / "parallelization_checker.yml"
    machine = FlatMachine(config_file=str(config_path))
    
    result = await machine.execute(input={
        "max_workers": max_workers,
    })
    
    return result


async def daemon_mode(db_path: Path, max_workers: int, poll_interval: float = 0.1):
    """Run as daemon, polling for scaling events."""
    conn = sqlite3.connect(str(db_path))
    last_id = 0
    idle_count = 0
    idle_exit_threshold = 50  # Exit after 50 consecutive idle checks (~5 seconds)
    
    # Get current max id to avoid processing old events
    cursor = conn.execute("SELECT MAX(id) FROM scaling_events")
    row = cursor.fetchone()
    if row and row[0]:
        last_id = row[0]
    
    print(f"Scale daemon started (poll={poll_interval}s, max_workers={max_workers})")
    print(f"Watching for scaling events (starting from id={last_id})...")
    
    while True:
        try:
            # Check for new events
            cursor = conn.execute(
                "SELECT MAX(id) FROM scaling_events WHERE id > ?", 
                (last_id,)
            )
            row = cursor.fetchone()
            
            if row and row[0]:
                last_id = row[0]
                idle_count = 0  # Reset idle counter
                print(f"\n📡 Scaling event detected (id={last_id}), checking pool...")
                
                result = await run_scaling_check(max_workers)
                
                spawned = result.get("spawned", 0)
                queue_depth = result.get("queue_depth", 0)
                active_workers = result.get("active_workers", 0)
                
                print(f"   Queue: {queue_depth}, Active: {active_workers}, Spawned: {spawned}")
                
                # Exit if queue empty and no workers active
                if queue_depth == 0 and active_workers == 0:
                    print("\n✅ All work complete (queue=0, workers=0). Daemon exiting.")
                    break
            else:
                idle_count += 1
                
                # Periodically check if all work is done even without events
                if idle_count >= idle_exit_threshold:
                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM paper_queue WHERE status IN ('pending', 'processing')"
                    )
                    pending = cursor.fetchone()[0]
                    
                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM worker_registry WHERE status = 'active'"
                    )
                    active = cursor.fetchone()[0]
                    
                    if pending == 0 and active == 0:
                        print("\n✅ All work complete. Daemon exiting.")
                        break
                    
                    idle_count = 0  # Reset and keep checking
            
            await asyncio.sleep(poll_interval)
            
        except KeyboardInterrupt:
            print("\nDaemon stopped.")
            break
        except Exception as e:
            print(f"Error in daemon loop: {e}")
            await asyncio.sleep(1)  # Back off on error


async def main():
    parser = argparse.ArgumentParser(description="Run paper analysis parallelization checker")
    parser.add_argument("--max-workers", "-m", type=int, default=3, help="Maximum workers")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run as polling daemon")
    parser.add_argument("--poll-interval", "-p", type=float, default=0.1, help="Poll interval in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.daemon:
        await daemon_mode(DB_PATH, args.max_workers, args.poll_interval)
    else:
        print(f"Running parallelization checker (max_workers={args.max_workers})")
        
        result = await run_scaling_check(args.max_workers)
        
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
