#!/usr/bin/env python3
"""Run the analyzer pipeline on multiple papers sequentially.

Usage:
    uv run python run_papers.py              # Run all 3 papers
    uv run python run_papers.py 2602.05892   # Run a single paper by arxiv_id
"""

import asyncio
import sys
from pathlib import Path

from flatmachines import FlatMachine

from research_paper_analysis_v3.flatmachine_v4 import (
    create_v3_hooks_registry,
    get_checkpoint_backend,
)

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

PAPERS = [
    {
        "arxiv_id": "2602.05892",
        "title": "ContextBench: A Benchmark for Context Retrieval in Coding Agents",
        "authors": "Han Li, Letian Zhu, Bohan Zhang, Rili Feng, Jiaming Wang, Yue Pan, Earl T. Barr, Federica Sarro, Zhaoyang Chu, He Ye",
        "abstract": (
            "LLM-based coding agents have shown strong performance on automated "
            "issue resolution benchmarks, yet existing evaluations largely focus "
            "on final task success, providing limited insight into how agents "
            "retrieve and use code context during problem solving. We introduce "
            "ContextBench, a process-oriented evaluation of context retrieval in "
            "coding agents. ContextBench consists of 1,136 issue-resolution tasks "
            "from 66 repositories across eight programming languages, each "
            "augmented with human-annotated gold contexts. We further implement an "
            "automated evaluation framework that tracks agent trajectories and "
            "measures context recall, precision, and efficiency throughout issue "
            "resolution. Using ContextBench, we evaluate four frontier LLMs and "
            "five coding agents. Our results show that sophisticated agent "
            "scaffolding yields only marginal gains in context retrieval (The Bitter Lesson of coding agents), LLMs consistently favor recall "
            "over precision, and substantial gaps exist between explored and "
            "utilized context."
        ),
    },
    {
        "arxiv_id": "2601.00376",
        "title": "In Line with Context: Repository-Level Code Generation via Context Inlining",
        "authors": "Chao Hu, Wenhao Zeng, Yuling Shi, Beijun Shen, Xiaodong Gu",
        "abstract": (
            "Repository-level code generation has attracted growing attention in "
            "recent years. Unlike function-level code generation, it requires the "
            "model to understand the entire repository, reasoning over complex "
            "dependencies across functions, classes, and modules. However, existing "
            "approaches such as retrieval-augmented generation (RAG) or context-based "
            "function selection often fall short: they primarily rely on surface-level "
            "similarity and struggle to capture the rich dependencies that govern "
            "repository-level semantics. In this paper, we introduce InlineCoder, a "
            "novel framework for repository-level code generation. InlineCoder enhances "
            "the understanding of repository context by inlining the unfinished function "
            "into its call graph, thereby reframing the challenging repository "
            "understanding as an easier function-level coding task."
        ),
    },
    {
        "arxiv_id": "2602.20048",
        "title": "The Navigation Paradox in Large-Context Agentic Coding: Graph-Structured Dependency Navigation Outperforms Retrieval in Architecture-Heavy Tasks",
        "authors": "Tejash P., et al.",
        "abstract": (
            "Agentic coding assistants powered by Large Language Models (LLMs) are "
            "increasingly deployed on repository-level software tasks. As context "
            "windows expand toward millions of tokens, a tacit assumption holds that "
            "retrieval bottlenecks dissolve; the model can simply ingest the whole "
            "codebase. We challenge this assumption by introducing the Navigation "
            "Paradox: larger context windows do not eliminate the need for structural "
            "navigation; they shift the failure mode from retrieval capacity to "
            "navigational salience. When architecturally critical but semantically "
            "distant files are absent from the model attention, errors may occur "
            "that additional context budget alone is unlikely to resolve."
        ),
    },
]


async def run_paper(paper):
    """Run the analyzer pipeline on a single paper. Returns the output path or None on failure."""
    arxiv_id = paper["arxiv_id"]
    clean_path = DATA_DIR / f"{arxiv_id}-clean.txt"

    if not clean_path.exists():
        print(f"\n{'=' * 60}")
        print(f"SKIP {arxiv_id}: missing cleaned text at {clean_path}")
        print(f"{'=' * 60}\n")
        return None

    cleaned_paper_text = clean_path.read_text(encoding="utf-8")
    token_estimate = len(cleaned_paper_text) // 4

    print(f"\n{'=' * 60}")
    print(f"RUNNING {arxiv_id}: {paper['title']}")
    print(f"  Text: {len(cleaned_paper_text)} chars (~{token_estimate} tokens)")
    print(f"{'=' * 60}")

    input_data = {
        "arxiv_id": arxiv_id,
        "execution_id": f"v3-{arxiv_id}",
        "paper_id": arxiv_id,
        "source_url": f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}",
        "title": paper["title"],
        "authors": paper["authors"],
        "abstract": paper["abstract"],
        "cleaned_paper_text": cleaned_paper_text,
        "corpus_signals": "",
        "corpus_neighbors": "",
        "reference_count": 0,
    }

    hooks_registry = create_v3_hooks_registry(project_root=PROJECT_ROOT)
    persistence = get_checkpoint_backend()

    machine = FlatMachine(
        config_file=str(CONFIG_DIR / "analyzer_machine.yml"),
        hooks_registry=hooks_registry,
        persistence=persistence,
        profiles_file=str((CONFIG_DIR.resolve() / "profiles.yml")),
    )

    try:
        result = await machine.execute(input=input_data)
        result_path = result.get("result_path")
        if result_path:
            print(f"\n  OK: Digest written to: {result_path}")
            with open(result_path) as f:
                for i, line in enumerate(f):
                    if i >= 15:
                        print("  ...")
                        break
                    print(f"  {line.rstrip()}")
            return result_path
        else:
            print(f"\n  FAIL: No result_path in output")
            for key, val in result.items():
                if isinstance(val, str) and len(val) > 100:
                    print(f"    {key}: ({len(val)} chars)")
                else:
                    print(f"    {key}: {val}")
            return None
    except Exception as e:
        print(f"\n  FAIL: Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    # If a single arxiv_id is passed, run just that one
    if len(sys.argv) > 1:
        target = sys.argv[1]
        papers = [p for p in PAPERS if p["arxiv_id"] == target]
        if not papers:
            print(f"Unknown paper: {target}")
            sys.exit(1)
    else:
        papers = PAPERS

    results = {}
    for paper in papers:
        path = asyncio.run(run_paper(paper))
        results[paper["arxiv_id"]] = path

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for arxiv_id, path in results.items():
        status = f"{path}" if path else "FAILED"
        print(f"  {arxiv_id}: {status}")

    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"\n{len(failed)} paper(s) failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"\nAll {len(results)} papers processed successfully.")


if __name__ == "__main__":
    main()
