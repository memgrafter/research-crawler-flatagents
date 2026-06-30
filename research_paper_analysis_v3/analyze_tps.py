#!/usr/bin/env python3
"""Parse llama-server timing logs and report prefill TPS, generation TPS, and token counts.

Usage:
    python analyze_tps.py <log_file> [--papers "id1:id2,id3:id4"]

By default, treats each contiguous block of tasks as a separate paper run.
Use --papers to explicitly map task ID ranges to paper names.

Examples:
    python analyze_tps.py server.log
    python analyze_tps.py server.log --papers "ContextBench:435-698,InlineCoder:764-2556,CodeCompass:3119-4682"
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# Regex for timing lines
RE_PROMPT = re.compile(
    r"task\s+(\d+)\s*\|\s*prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*"
    r"\(\s*([\d.]+)\s+ms per token,\s*([\d.]+)\s+tokens per second\)"
)

RE_GENERATION = re.compile(
    r"task\s+(\d+)\s*\|\s+eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*"
    r"\(\s*([\d.]+)\s+ms per token,\s*([\d.]+)\s+tokens per second\)"
)

RE_TOTAL = re.compile(
    r"task\s+(\d+)\s*\|\s+total time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens"
)

RE_N_DECODED = re.compile(
    r"task\s+(-?\d+)\s*\|\s*n_decoded\s*=\s*(\d+),\s*tg\s*=\s*([\d.]+)\s*t/s"
)


@dataclass
class TaskTiming:
    task_id: int
    prompt_tokens: Optional[int] = None
    prompt_time_ms: Optional[float] = None
    prompt_tps: Optional[float] = None
    gen_tokens: Optional[int] = None
    gen_time_ms: Optional[float] = None
    gen_tps: Optional[float] = None
    total_tokens: Optional[int] = None
    n_decoded_tps: list = field(default_factory=list)

    @property
    def is_prefill_only(self):
        """Tasks with 0 or 1 generation tokens are prefill-only (e.g. warmup, cache hits)."""
        return self.gen_tokens is not None and self.gen_tokens <= 1

    @property
    def is_large_prefill(self):
        """Tasks processing near full context (~128k) — the fan-out section machines."""
        return (self.prompt_tokens or 0) >= 50000


def parse_log(path: str) -> list[TaskTiming]:
    """Parse all timing lines from a llama-server log."""
    timings: dict[int, TaskTiming] = {}

    with open(path) as f:
        for line in f:
            m = RE_PROMPT.search(line)
            if m:
                tid = int(m.group(1))
                t = timings.setdefault(tid, TaskTiming(task_id=tid))
                t.prompt_tokens = int(m.group(3))
                t.prompt_time_ms = float(m.group(2))
                t.prompt_tps = float(m.group(5))
                continue

            m = RE_GENERATION.search(line)
            if m:
                tid = int(m.group(1))
                t = timings.setdefault(tid, TaskTiming(task_id=tid))
                t.gen_tokens = int(m.group(3))
                t.gen_time_ms = float(m.group(2))
                t.gen_tps = float(m.group(5))
                continue

            m = RE_TOTAL.search(line)
            if m:
                tid = int(m.group(1))
                t = timings.setdefault(tid, TaskTiming(task_id=tid))
                t.total_tokens = int(m.group(3))
                continue

            m = RE_N_DECODED.search(line)
            if m:
                tid = int(m.group(1))
                t = timings.setdefault(tid, TaskTiming(task_id=tid))
                t.n_decoded_tps.append(float(m.group(3)))
                continue

    return list(timings.values())


def group_by_paper(timings: list[TaskTiming], paper_ranges: Optional[list[tuple[str, int, int]]]) -> dict[str, list[TaskTiming]]:
    """Group tasks into papers. If no ranges given, split on task ID gaps > 100."""
    if paper_ranges:
        papers = {}
        for name, lo, hi in paper_ranges:
            papers[name] = [t for t in timings if lo <= t.task_id <= hi]
        return papers

    # Auto-detect: split on large gaps in task IDs
    sorted_t = sorted(timings, key=lambda t: t.task_id)
    groups = []
    current = [sorted_t[0]]
    for t in sorted_t[1:]:
        if t.task_id - current[-1].task_id > 100:
            groups.append(current)
            current = [t]
        else:
            current.append(t)
    groups.append(current)

    papers = {}
    for i, group in enumerate(groups):
        lo, hi = group[0].task_id, group[-1].task_id
        papers[f"paper_{i+1} ({lo}-{hi})"] = group
    return papers


def summarize_paper(name: str, tasks: list[TaskTiming]) -> dict:
    """Compute summary stats for a paper's tasks."""
    prefill_tasks = [t for t in tasks if t.prompt_tokens is not None and not t.is_prefill_only]
    gen_tasks = [t for t in tasks if t.gen_tokens is not None and t.gen_tokens > 1]
    all_prefill = [t for t in tasks if t.prompt_tokens is not None]

    total_input = sum(t.prompt_tokens or 0 for t in all_prefill)
    total_output = sum((t.gen_tokens or 0) for t in gen_tasks)
    total_all = sum(t.total_tokens or 0 for t in tasks)

    # Prefill TPS: weighted average by tokens processed
    if prefill_tasks:
        weighted_tps = sum(
            (t.prompt_tokens or 0) * (t.prompt_tps or 0)
            for t in prefill_tasks
        ) / max(total_input, 1)
    else:
        weighted_tps = 0.0

    # Generation TPS: weighted average by output tokens
    if gen_tasks:
        gen_weighted_tps = sum(
            (t.gen_tokens or 0) * (t.gen_tps or 0)
            for t in gen_tasks
        ) / max(total_output, 1)
    else:
        gen_weighted_tps = 0.0

    # Also compute n_decoded TPS (intermediate generation speed samples)
    all_n_decoded = [tps for t in tasks for tps in t.n_decoded_tps]

    return {
        "name": name,
        "task_count": len(tasks),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_all_tokens": total_all,
        "prefill_tps_weighted": weighted_tps,
        "gen_tps_weighted": gen_weighted_tps,
        "n_decoded_tps_avg": sum(all_n_decoded) / len(all_n_decoded) if all_n_decoded else 0.0,
        "n_decoded_tps_min": min(all_n_decoded) if all_n_decoded else 0.0,
        "n_decoded_tps_max": max(all_n_decoded) if all_n_decoded else 0.0,
    }


def parse_paper_ranges(raw: str) -> list[tuple[str, int, int]]:
    """Parse 'Name:lo-hi,Name2:lo2-hi2' format."""
    ranges = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, rng = part.split(":", 1)
        lo, hi = rng.split("-")
        ranges.append((name.strip(), int(lo), int(hi)))
    return ranges


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze llama-server TPS from logs")
    parser.add_argument("log", help="Path to llama-server log file")
    parser.add_argument("--papers", help='Explicit paper task ranges, e.g. "Paper1:100-500,Paper2:600-1200"')
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-task details")
    args = parser.parse_args()

    timings = parse_log(args.log)

    if args.papers:
        paper_ranges = parse_paper_ranges(args.papers)
    else:
        paper_ranges = None

    papers = group_by_paper(timings, paper_ranges)

    print(f"Parsed {len(timings)} task timings from {args.log}")

    if args.verbose:
        # Show per-task details sorted by task ID
        print("\nPer-task details:")
        for t in sorted(timings, key=lambda x: x.task_id):
            flags = []
            if t.is_prefill_only:
                flags.append("prefill-only")
            if t.is_large_prefill:
                flags.append("large-prefill")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            pt = f"{t.prompt_tps:8.1f}" if t.prompt_tps is not None else "      -"
            gt = f"{t.gen_tps:8.1f}" if t.gen_tps is not None else "      -"
            print(f"  task {t.task_id:5d}: prefill={t.prompt_tokens or 0:>6,} tok "
                  f"({pt} t/s)  gen={t.gen_tokens or 0:>5,} tok "
                  f"({gt} t/s)  total={t.total_tokens or 0:>6,}" + flag_str)
        print()

    print()

    for name, tasks in papers.items():
        s = summarize_paper(name, tasks)
        print(f"{'=' * 60}")
        print(f"  {s['name']}")
        print(f"  Tasks:           {s['task_count']}")
        print(f"  Input tokens:    {s['total_input_tokens']:,}")
        print(f"  Output tokens:   {s['total_output_tokens']:,}")
        print(f"  Total tokens:    {s['total_all_tokens']:,}")
        print(f"  Prefill TPS:     {s['prefill_tps_weighted']:.1f} tok/s")
        print(f"  Gen TPS:         {s['gen_tps_weighted']:.1f} tok/s")
        if s['n_decoded_tps_avg']:
            print(f"  n_decoded avg:   {s['n_decoded_tps_avg']:.1f} tok/s "
                  f"(min={s['n_decoded_tps_min']:.0f}, max={s['n_decoded_tps_max']:.0f})")
        print()

    # Overall totals (compute once)
    summaries = [summarize_paper(n, t) for n, t in papers.items()]
    total_input = sum(s["total_input_tokens"] for s in summaries)
    total_output = sum(s["total_output_tokens"] for s in summaries)
    print(f"{'=' * 60}")
    print(f"  TOTALS")
    print(f"  Input tokens:  {total_input:,}")
    print(f"  Output tokens: {total_output:,}")
    print(f"  Total:         {total_input + total_output:,}")


if __name__ == "__main__":
    main()
