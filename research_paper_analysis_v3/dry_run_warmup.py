#!/usr/bin/env -S uv run python
"""Dry-run: test the KV cache warmup with the Attention Is All You Need paper."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"

async def main():
    from flatagents import FlatAgent

    # Load the warmup agent
    agent_path = CONFIG_DIR / "agents" / "kv_cache_warmup.flatagent.yml"
    profiles_path = CONFIG_DIR / "profiles.yml"

    print(f"Loading agent: {agent_path}")
    print(f"Profiles:     {profiles_path}")

    agent = FlatAgent(
        str(agent_path),
        profiles_file=str(profiles_path),
    )

    # Load the cleaned paper text
    paper_text = (PROJECT_ROOT / "data" / "1706.03762-clean.txt").read_text()
    print(f"\nPaper text: {len(paper_text)} chars (~{len(paper_text)//4} tokens)")

    # Fire the warmup call
    print("\nSending warmup request...")
    import time
    t0 = time.time()

    try:
        resp = await agent.call(
            title="Attention Is All You Need",
            abstract=paper_text[:500] + "...",  # first paragraph as abstract proxy
            paper_text=paper_text,
        )

        elapsed = time.time() - t0
        print(f"\nWarmup complete in {elapsed:.1f}s")
        print(f"Response: {resp.content[:200]}")

        if resp.usage:
            u = resp.usage
            print(f"\nUsage:")
            print(f"  Input tokens:    {u.input_tokens}")
            print(f"  Output tokens:   {u.output_tokens}")
            print(f"  Cache read:      {u.cache_read_tokens}")
            print(f"  Cache write:     {u.cache_write_tokens}")
            if hasattr(u, 'cost') and u.cost:
                c = u.cost
                print(f"  Cost:            ${c.total:.6f} (input: {c.input}, output: {c.output})")

    except Exception as e:
        elapsed = time.time() - t0
        print(f"\nError after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
