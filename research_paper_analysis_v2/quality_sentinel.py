#!/usr/bin/env -S uv run python
"""Lightweight quality sentinel for recently generated v2 markdown reports.

Checks newest N reports in data/*.md for:
1) Required section completeness
2) Fallback/error phrase leakage
3) Executive summary opener duplication
4) Numeric grounding against data/<arxiv_id>.txt

Usage examples:
  python quality_sentinel.py --latest 10
  python quality_sentinel.py --latest 25 --fail-on-warn --json-out logs/quality_sentinel_latest.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple

REQUIRED_SECTIONS = [
    "Quick Facts",
    "Executive Summary",
    "Method Summary",
    "Key Results",
    "Why This Works (Mechanism)",
    "Foundational Learning",
    "Architecture Onboarding",
    "Open Questions the Paper Calls Out",
    "Limitations",
    "Confidence",
    "Next Checks",
]

FALLBACK_PHRASES = [
    "no paper content provided",
    "insufficient context",
    "unable to determine",
    "content unavailable",
    "not enough information",
]

ARXIV_ID_RE = re.compile(r"arxiv_id:\s*'?(\d{4}\.\d{5})'?", re.IGNORECASE)
HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
NUMBER_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:%|[kKmMbB])?")


@dataclass
class FileResult:
    file: str
    arxiv_id: str
    missing_sections: List[str]
    fallback_hits: List[str]
    exec_first_sentence: str
    numbers_total: int
    numbers_grounded: int
    numbers_ungrounded: List[str]
    paper_txt_exists: bool


def _normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _extract_section(text: str, name: str) -> str:
    m = re.search(rf"## {re.escape(name)}\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_exec_first_sentence(text: str) -> str:
    block = _extract_section(text, "Executive Summary")
    if not block:
        return ""
    first = re.split(r"(?<=[.!?])\s+", block.replace("\n", " ").strip())[0]
    return first.strip()


def _number_variants(token: str) -> List[str]:
    """Generate string variants for robust text containment checks."""
    t = (token or "").strip()
    if not t:
        return []

    variants = {t.lower()}
    plain = t.replace(",", "").lower()
    variants.add(plain)

    if plain.endswith("%"):
        variants.add(plain[:-1])
    if plain.endswith(("k", "m", "b")) and len(plain) > 1:
        variants.add(plain[:-1])

    # If integer-like float (e.g., 83.0), add integer variant.
    try:
        if "." in plain and plain.replace(".", "", 1).replace("-", "", 1).isdigit():
            f = float(plain)
            if f.is_integer():
                variants.add(str(int(f)))
    except ValueError:
        pass

    return sorted(v for v in variants if v)


def _ground_numbers(numbers: List[str], paper_text: str) -> Tuple[List[str], List[str]]:
    grounded: List[str] = []
    ungrounded: List[str] = []
    hay = (paper_text or "").lower()

    for n in numbers:
        variants = _number_variants(n)
        if any(v in hay for v in variants):
            grounded.append(n)
        else:
            ungrounded.append(n)

    return grounded, ungrounded


def analyze_file(md_path: Path, data_dir: Path) -> FileResult:
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    arxiv_match = ARXIV_ID_RE.search(text)
    arxiv_id = arxiv_match.group(1) if arxiv_match else ""

    headings = set(HEADING_RE.findall(text))
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in headings]

    low = text.lower()
    fallback_hits = [p for p in FALLBACK_PHRASES if p in low]

    exec_first = _extract_exec_first_sentence(text)

    summary = _extract_section(text, "Executive Summary")
    key_results = _extract_section(text, "Key Results")
    nums = sorted(set(NUMBER_RE.findall(f"{summary}\n{key_results}")))

    paper_txt_path = data_dir / f"{arxiv_id}.txt" if arxiv_id else Path("")
    paper_txt_exists = bool(arxiv_id and paper_txt_path.exists())
    paper_text = paper_txt_path.read_text(encoding="utf-8", errors="ignore") if paper_txt_exists else ""

    grounded, ungrounded = _ground_numbers(nums, paper_text)

    return FileResult(
        file=md_path.name,
        arxiv_id=arxiv_id,
        missing_sections=missing_sections,
        fallback_hits=fallback_hits,
        exec_first_sentence=exec_first,
        numbers_total=len(nums),
        numbers_grounded=len(grounded),
        numbers_ungrounded=ungrounded,
        paper_txt_exists=paper_txt_exists,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Quality sentinel for latest v2 markdown reports")
    parser.add_argument("--latest", type=int, default=10, help="How many latest .md reports to scan")
    parser.add_argument("--data-dir", default="data", help="Reports/data directory")
    parser.add_argument("--min-grounding", type=float, default=0.85, help="Minimum aggregate numeric grounding ratio")
    parser.add_argument(
        "--max-missing-sections",
        type=int,
        default=0,
        help="Maximum allowed files missing required sections",
    )
    parser.add_argument(
        "--max-dup-exec-first",
        type=int,
        default=1,
        help="Maximum allowed duplicated executive-summary opener groups",
    )
    parser.add_argument(
        "--max-fallback-files",
        type=int,
        default=0,
        help="Maximum allowed files containing fallback/error phrases",
    )
    parser.add_argument("--json-out", default="", help="Optional path to write JSON result")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit non-zero when thresholds are violated")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        print(f"ERROR: data directory not found: {data_dir}")
        return 1

    all_md_files = sorted(data_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    md_total_count = len(all_md_files)
    md_files = all_md_files[: args.latest]
    if not md_files:
        print(f"ERROR: no markdown files found in {data_dir}")
        return 1

    results = [analyze_file(p, data_dir) for p in md_files]

    files_missing_sections = [r for r in results if r.missing_sections]
    files_with_fallback = [r for r in results if r.fallback_hits]

    opener_counter = Counter(_normalize_sentence(r.exec_first_sentence) for r in results if r.exec_first_sentence)
    duplicate_openers = {k: v for k, v in opener_counter.items() if v > 1}

    total_numbers = sum(r.numbers_total for r in results)
    grounded_numbers = sum(r.numbers_grounded for r in results)
    grounding_ratio = (grounded_numbers / total_numbers) if total_numbers else 1.0

    warnings: List[str] = []
    if len(files_missing_sections) > args.max_missing_sections:
        warnings.append(
            f"files_missing_sections={len(files_missing_sections)} > max_missing_sections={args.max_missing_sections}"
        )
    if len(duplicate_openers) > args.max_dup_exec_first:
        warnings.append(
            f"duplicate_exec_first_groups={len(duplicate_openers)} > max_dup_exec_first={args.max_dup_exec_first}"
        )
    if len(files_with_fallback) > args.max_fallback_files:
        warnings.append(
            f"files_with_fallback={len(files_with_fallback)} > max_fallback_files={args.max_fallback_files}"
        )
    if grounding_ratio < args.min_grounding:
        warnings.append(
            f"grounding_ratio={grounding_ratio:.3f} < min_grounding={args.min_grounding:.3f}"
        )

    summary: Dict[str, object] = {
        "md_total_count": md_total_count,
        "files_scanned": len(results),
        "files_missing_sections": len(files_missing_sections),
        "files_with_fallback": len(files_with_fallback),
        "duplicate_exec_first_groups": len(duplicate_openers),
        "numbers_grounded": grounded_numbers,
        "numbers_total": total_numbers,
        "grounding_ratio": round(grounding_ratio, 4),
        "warnings": warnings,
        "per_file": [asdict(r) for r in results],
    }

    print(f"md_total_count={summary['md_total_count']}")
    print(f"files_scanned={summary['files_scanned']}")
    print(
        "section_complete="
        f"{len(results) - len(files_missing_sections)}/{len(results)} "
        f"(files_missing_sections={len(files_missing_sections)})"
    )
    print(f"files_with_fallback={len(files_with_fallback)}")
    print(f"duplicate_exec_first_groups={len(duplicate_openers)}")
    print(f"numeric_grounding={grounded_numbers}/{total_numbers} ({grounding_ratio:.1%})")

    if files_with_fallback:
        print("fallback_files:")
        for r in files_with_fallback[:10]:
            print(f"  - {r.file}: {', '.join(r.fallback_hits)}")

    if files_missing_sections:
        print("missing_section_files:")
        for r in files_missing_sections[:10]:
            print(f"  - {r.file}: {', '.join(r.missing_sections)}")

    if duplicate_openers:
        print("duplicate_exec_openers:")
        for opener, count in sorted(duplicate_openers.items(), key=lambda x: x[1], reverse=True)[:10]:
            preview = opener[:120]
            print(f"  - {count}x: {preview}")

    if warnings:
        print("status=WARN")
        for w in warnings:
            print(f"  warning: {w}")
    else:
        print("status=OK")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"json_written={out_path}")

    if warnings and args.fail_on_warn:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
