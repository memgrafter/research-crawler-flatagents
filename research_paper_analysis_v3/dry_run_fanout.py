#!/usr/bin/env -S uv run python
"""Dry-run: test warmup + fan-out to verify KV cache sharing."""

import asyncio
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"

# Minimal prompt templates for each mode (same system, same paper, different suffix)
SYSTEM = """You are a technical research analyst for ML papers. Be precise and evidence-aware.
Never use markdown code fences."""

MODE_PROMPTS = {
    "narrative_lead": "Write 'What This Paper Did' in 2-3 paragraphs.",
    "method_results": "Write the 'Method' section with concrete details.",
    "why_mechanism": "Write 2 mechanisms for 'Why It Works', each with claim, mechanism, assumption, break condition.",
    "reproduction": "Write 'What's Specified' and 'What's Missing' for reproduction.",
    "open_questions": "Write 3 open questions with basis and why unresolved.",
    "limits_confidence": "Write 'Failure Modes & Limitations', 'Confidence Assessment', and exactly 3 'Next Checks'.",
}

async def call_mode(agent, paper_text, mode, max_tokens=4000):
    """Send a mode-specific request with the same paper prefix."""
    user_msg = f"""Paper title: Attention Is All You Need

Abstract:
{paper_text[:500]}...

Paper text:
{paper_text}

{MODE_PROMPTS[mode]}
"""
    agent.max_tokens = max_tokens
    t0 = time.time()
    resp = await agent.call(
        title="Attention Is All You Need",
        abstract=paper_text[:500] + "...",
        paper_text=paper_text,
        mode=mode,
    )
    elapsed = time.time() - t0
    return {
        "mode": mode,
        "elapsed": elapsed,
        "input_tokens": resp.usage.input_tokens if resp.usage else "?",
        "cache_read": resp.usage.cache_read_tokens if resp.usage else "?",
        "output_tokens": resp.usage.output_tokens if resp.usage else "?",
        "content_length": len(resp.content or ""),
    }

async def main():
    from flatagents import FlatAgent

    agent_path = CONFIG_DIR / "agents" / "analyzer.flatagent.yml"
    profiles_path = CONFIG_DIR / "profiles.yml"

    # Warmup first
    warmup_agent = FlatAgent(
        str(CONFIG_DIR / "agents" / "kv_cache_warmup.flatagent.yml"),
        profiles_file=str(profiles_path),
    )
    paper_text = (PROJECT_ROOT / "data" / "1706.03762-clean.txt").read_text()

    print("=== WARMUP ===")
    t0 = time.time()
    resp = await warmup_agent.call(
        title="Attention Is All You Need",
        abstract=paper_text[:500] + "...",
        paper_text=paper_text,
    )
    print(f"Warmup: {time.time()-t0:.1f}s | input={resp.usage.input_tokens} cache_read={resp.usage.cache_read_tokens} output={resp.usage.output_tokens}")

    # Now fan-out
    analyzer = FlatAgent(str(agent_path), profiles_file=str(profiles_path))

    print("\n=== FAN-OUT (sequential to measure each) ===")
    results = []
    for mode in MODE_PROMPTS:
        r = await call_mode(analyzer, paper_text, mode, max_tokens=4000)
        results.append(r)
        print(f"  {mode:20s} {r['elapsed']:6.1f}s | input={r['input_tokens']:>6} cache_read={r['cache_read']:>5} output={r['output_tokens']:>5} chars={r['content_length']:>5}")

    print(f"\n=== SUMMARY ===")
    total_input = sum(r["input_tokens"] for r in results if isinstance(r["input_tokens"], int))
    total_cache = sum(r["cache_read"] for r in results if isinstance(r["cache_read"], int))
    print(f"Total fan-out input: {total_input} tokens")
    print(f"Total cache read:   {total_cache} tokens ({total_cache/total_input*100:.1f}% cached)")

if __name__ == "__main__":
    asyncio.run(main())
