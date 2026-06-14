#!/usr/bin/env python3
"""End-to-end test of the analyzer machine using FlatMachine.

Uses the pre-cleaned paper text from data/1706.03762-clean.txt to validate
the full flow: warmup → fan-out → assemble → judge → (repair) → save.

Usage:
    uv run python dry_run_full.py
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


def main():
    paper_path = DATA_DIR / "1706.03762-clean.txt"
    if not paper_path.exists():
        print(f"Missing cleaned paper: {paper_path}")
        sys.exit(1)

    cleaned_paper_text = paper_path.read_text(encoding="utf-8")
    print(f"Loaded paper text: {len(cleaned_paper_text)} chars (~{len(cleaned_paper_text)//4} tokens)")

    input_data = {
        "arxiv_id": "1706.03762",
        "execution_id": "dry-run-001",
        "paper_id": "1706.03762",
        "source_url": "https://ar5iv.labs.arxiv.org/html/1706.03762",
        "title": "Attention Is All You Need",
        "authors": "Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin",
        "abstract": (
            "The dominant sequence transduction models are based on complex "
            "recurrent or convolutional neural networks that include an encoder "
            "and a decoder. The best performing models also connect the encoder "
            "and decoder through an attention mechanism. We propose a new simple "
            "network architecture, the Transformer, based solely on attention "
            "mechanisms, dispensing with recurrence and convolutions entirely. "
            "Experiments on two machine translation tasks show these models to "
            "be superior in quality while being more parallelizable and requiring "
            "significantly less time to train."
        ),
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

    async def run():
        result = await machine.execute(input=input_data)
        return result

    result = asyncio.run(run())
    print("\n--- Result ---")
    for key, val in result.items():
        if isinstance(val, str) and len(val) > 100:
            print(f"  {key}: ({len(val)} chars)")
        else:
            print(f"  {key}: {val}")

    # Show the saved file path
    result_path = result.get("result_path")
    if result_path:
        print(f"\nDigest written to: {result_path}")
        # Print first few lines
        with open(result_path) as f:
            for i, line in enumerate(f):
                if i >= 30:
                    print("  ...")
                    break
                print(f"  {line.rstrip()}")
    else:
        print("\nNo result path — check logs above for errors.")


if __name__ == "__main__":
    main()
