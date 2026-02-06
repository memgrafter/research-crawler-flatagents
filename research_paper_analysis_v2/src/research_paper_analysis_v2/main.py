"""Minimal runner stub for the v2 machine.

Expects pre-parsed paper inputs. Wire this into a fuller parser/worker entrypoint
as needed.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, Dict

from flatmachines import FlatMachine


async def run_with_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    machine = FlatMachine(config_file=str(root / "config" / "machine.yml"))
    return await machine.execute(input=payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v2 machine with basic input fields")
    parser.add_argument("--title", required=True)
    parser.add_argument("--abstract", required=True)
    parser.add_argument("--section-text", default="")
    parser.add_argument("--authors", default="")
    parser.add_argument("--arxiv-id", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--reference-count", type=int, default=0)
    args = parser.parse_args()

    payload = {
        "title": args.title,
        "abstract": args.abstract,
        "section_text": args.section_text,
        "authors": args.authors,
        "arxiv_id": args.arxiv_id,
        "source_url": args.source_url,
        "reference_count": args.reference_count,
    }

    result = asyncio.run(run_with_input(payload))
    print(result)


if __name__ == "__main__":
    main()
