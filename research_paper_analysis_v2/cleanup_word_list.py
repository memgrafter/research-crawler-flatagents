#!/usr/bin/env python3
"""Post-process a generated word list into a cleaned copy.

This script is intentionally a separate step so model outputs remain preserved.
It reads an existing word-list text file and candidate CSV, applies exclusion terms,
and writes cleaned outputs to separate files.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


def _resolve(base_dir: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def _read_list_file(path: Path) -> List[str]:
    out: List[str] = []
    for raw in _read_lines(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        n = _normalize(item)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(item.strip())
    return out


def _is_excluded_term(term: str, exclusions: Sequence[str]) -> bool:
    text = _normalize(term)
    if not text:
        return True

    for raw_ex in exclusions:
        ex = _normalize(raw_ex)
        if not ex:
            continue

        if " " not in ex:
            if re.search(rf"\b{re.escape(ex)}\b", text):
                return True
            continue

        if ex in text:
            return True

    return False


def _load_exclusions(raw_terms: Sequence[str], raw_files: Sequence[str], base_dir: Path) -> List[str]:
    items: List[str] = []
    items.extend([x for x in raw_terms if x.strip()])
    for raw in raw_files:
        items.extend(_read_list_file(_resolve(base_dir, raw)))
    return _dedupe_keep_order(items)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clean generated word list into separate output files")
    p.add_argument("--input-word-list", required=True, help="Input generated .txt word list")
    p.add_argument("--input-csv", required=True, help="Input generated candidate CSV")
    p.add_argument("--output-word-list", required=True, help="Output cleaned .txt word list")
    p.add_argument("--output-csv", required=True, help="Output cleaned candidate CSV")
    p.add_argument("--exclude-file", action="append", default=[], help="Exclusion file (repeatable)")
    p.add_argument("--exclude-term", action="append", default=[], help="Exclusion term/token (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="Preview counts only")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent

    input_word_list = _resolve(base_dir, args.input_word_list)
    input_csv = _resolve(base_dir, args.input_csv)
    output_word_list = _resolve(base_dir, args.output_word_list)
    output_csv = _resolve(base_dir, args.output_csv)

    exclusions = _load_exclusions(args.exclude_term, args.exclude_file, base_dir)
    if not exclusions:
        raise SystemExit("No exclusions provided. Pass --exclude-file and/or --exclude-term.")

    txt_lines = _read_lines(input_word_list)
    txt_comments: List[str] = []
    txt_terms: List[str] = []
    for line in txt_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            txt_comments.append(line)
            continue
        txt_terms.append(stripped)

    txt_kept = [t for t in txt_terms if not _is_excluded_term(t, exclusions)]

    with input_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    csv_kept = [row for row in rows if not _is_excluded_term(row.get("term_display", ""), exclusions)]

    print(f"Input txt terms: {len(txt_terms)}")
    print(f"Input csv rows: {len(rows)}")
    print(f"Exclusions: {len(exclusions)}")
    print(f"Removed txt terms: {len(txt_terms) - len(txt_kept)}")
    print(f"Removed csv rows: {len(rows) - len(csv_kept)}")
    print(f"Kept txt terms: {len(txt_kept)}")
    print(f"Kept csv rows: {len(csv_kept)}")

    if args.dry_run:
        return 0

    output_word_list.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_word_list.open("w", encoding="utf-8") as f:
        for line in txt_comments:
            f.write(f"{line}\n")
        f.write(f"# cleaned_by: cleanup_word_list.py\n")
        f.write(f"# exclusions_applied: {len(exclusions)}\n")
        f.write(f"# removed_terms: {len(txt_terms) - len(txt_kept)}\n\n")
        for term in txt_kept:
            f.write(f"{term}\n")

    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in csv_kept:
            writer.writerow(row)

    print(f"Wrote cleaned word list: {output_word_list}")
    print(f"Wrote cleaned CSV: {output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
