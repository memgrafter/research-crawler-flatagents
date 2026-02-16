#!/usr/bin/env -S uv run python
"""Extract text files from PDFs in data/ without running the v2 pipeline.

This is intentionally decoupled from prep scheduling so you can prebuild .txt files
at high concurrency, then let prep reuse them.

Usage examples:
  python pdf_text_extractor.py
  python pdf_text_extractor.py --workers 32
  python pdf_text_extractor.py --workers 64 --overwrite
  python pdf_text_extractor.py --ids 2409.19445 2412.04984
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader


@dataclass
class Result:
    pdf: str
    txt: str
    status: str  # written | skipped | error
    pages: int = 0
    chars: int = 0
    error: str = ""


def _extract_one(pdf_path: Path, txt_path: Path, overwrite: bool = False) -> Result:
    try:
        if not overwrite and txt_path.exists() and txt_path.stat().st_size > 0:
            return Result(str(pdf_path.name), str(txt_path.name), "skipped")

        reader = PdfReader(str(pdf_path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(f"[PAGE {i + 1}]\n{text}")

        full_text = "\n\n".join(pages)
        full_text = full_text.encode("utf-8", "replace").decode("utf-8")

        txt_path.write_text(full_text, encoding="utf-8")
        return Result(
            str(pdf_path.name),
            str(txt_path.name),
            "written",
            pages=len(reader.pages),
            chars=len(full_text),
        )
    except Exception as exc:  # noqa: BLE001
        return Result(str(pdf_path.name), str(txt_path.name), "error", error=str(exc))


def _iter_pdfs(data_dir: Path, ids: list[str], limit: int) -> Iterable[Path]:
    pdfs = sorted(data_dir.glob("*.pdf"))
    if ids:
        wanted = {i.strip().replace("/", "_") for i in ids}
        pdfs = [p for p in pdfs if p.stem in wanted]
    if limit > 0:
        pdfs = pdfs[:limit]
    return pdfs


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract data/*.pdf -> data/*.txt")
    parser.add_argument("--data-dir", default="data", help="Directory containing PDFs (default: data)")
    parser.add_argument("--workers", type=int, default=16, help="Extraction worker threads")
    parser.add_argument("--overwrite", action="store_true", help="Rewrite txt even if non-empty txt exists")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N matching PDFs")
    parser.add_argument("--ids", nargs="*", default=[], help="Specific arXiv ids to process (e.g. 2409.19445)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        print(f"Data dir not found: {data_dir}")
        return 2

    pdfs = list(_iter_pdfs(data_dir, args.ids, args.limit))
    if not pdfs:
        print("No PDFs matched.")
        return 0

    workers = max(1, int(args.workers))
    print(f"Extracting {len(pdfs)} PDFs from {data_dir} with workers={workers} overwrite={args.overwrite}")

    written = 0
    skipped = 0
    errors = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = []
        for pdf in pdfs:
            txt = data_dir / f"{pdf.stem}.txt"
            futs.append(ex.submit(_extract_one, pdf, txt, args.overwrite))

        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            if res.status == "written":
                written += 1
                print(f"WRITE  {res.pdf} -> {res.txt} pages={res.pages} chars={res.chars}")
            elif res.status == "skipped":
                skipped += 1
            else:
                errors += 1
                print(f"ERROR  {res.pdf}: {res.error}")

    print(f"Summary: written={written} skipped={skipped} errors={errors} total={len(pdfs)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
