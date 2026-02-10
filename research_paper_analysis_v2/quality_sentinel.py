#!/usr/bin/env -S uv run python
"""Lightweight quality sentinel for recently generated v2 markdown reports.

Checks newest N reports in data/*.md for:
1) Required section completeness
2) Fallback/error phrase leakage
3) Executive summary opener duplication
4) Numeric grounding against data/<arxiv_id>.txt

--fix mode:
  Scans ALL md files (or --latest N). For each arxiv_id:
  - If duplicates exist: keep the best complete one (prefer DB-pointed, then largest).
    Delete orphans.  If the DB-pointed file is broken but an orphan is good, swap it in.
  - If the surviving file is incomplete: reset execution to pending and delete the file
    so the pipeline re-processes it.

Usage examples:
  python quality_sentinel.py --latest 10
  python quality_sentinel.py --latest 25 --fail-on-warn --json-out logs/quality_sentinel_latest.json
  python quality_sentinel.py --fix                  # scan ALL, fix duplicates + incomplete
  python quality_sentinel.py --fix --latest 500     # scan latest 500 only
  python quality_sentinel.py --fix --dry-run        # show what would happen, don't touch anything
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


def is_complete(md_path: Path) -> bool:
    """Quick check: does this file have all required sections?"""
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    headings = set(HEADING_RE.findall(text))
    return all(s in headings for s in REQUIRED_SECTIONS)


def _get_v2_db(data_dir: Path) -> sqlite3.Connection:
    db_path = os.environ.get(
        "V2_EXECUTIONS_DB_PATH",
        str(data_dir / "v2_executions.sqlite"),
    )
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def run_fix(data_dir: Path, md_files: List[Path], dry_run: bool = False) -> Dict[str, int]:
    """Fix duplicates and incomplete reports.

    Returns counts of actions taken.
    """
    stats = {
        "files_scanned": 0,
        "duplicates_deleted": 0,
        "orphan_promoted": 0,
        "incomplete_reset": 0,
        "incomplete_deleted": 0,
        "already_ok": 0,
    }

    conn = _get_v2_db(data_dir)

    # Group md files by arxiv_id
    by_arxiv: Dict[str, List[Path]] = defaultdict(list)
    for p in md_files:
        m = re.match(r"^(\d{4}\.\d{4,5})", p.name)
        if m:
            by_arxiv[m.group(1)].append(p)
    stats["files_scanned"] = len(md_files)

    for arxiv_id, files in by_arxiv.items():
        # Look up DB row
        row = conn.execute(
            "SELECT execution_id, status, result_path FROM executions WHERE arxiv_id = ?",
            (arxiv_id,),
        ).fetchone()
        if not row:
            continue

        db_result_path = row["result_path"] or ""
        db_file = Path(db_result_path).name if db_result_path else ""

        # Identify which file the DB points to vs orphans
        db_pointed: Optional[Path] = None
        orphans: List[Path] = []
        for f in files:
            if f.name == db_file:
                db_pointed = f
            else:
                orphans.append(f)

        # If no DB match (e.g., result_path cleared), treat largest as db_pointed
        if db_pointed is None and files:
            files_sorted = sorted(files, key=lambda p: p.stat().st_size, reverse=True)
            db_pointed = files_sorted[0]
            orphans = files_sorted[1:]

        # Check completeness
        db_ok = db_pointed is not None and is_complete(db_pointed)

        # If DB file is broken, check orphans for a good one
        promoted = False
        if not db_ok and orphans:
            for orphan in sorted(orphans, key=lambda p: p.stat().st_size, reverse=True):
                if is_complete(orphan):
                    # Promote this orphan: update DB result_path, swap files
                    print(f"  PROMOTE {arxiv_id}: {orphan.name} -> result_path (replaces broken {db_pointed.name if db_pointed else 'none'})")
                    if not dry_run:
                        conn.execute(
                            "UPDATE executions SET result_path = ?, updated_at = ? WHERE arxiv_id = ?",
                            (str(orphan), datetime.now(timezone.utc).isoformat(), arxiv_id),
                        )
                        conn.commit()
                    # Delete the broken DB file
                    if db_pointed and db_pointed.exists():
                        print(f"  DELETE  {arxiv_id}: {db_pointed.name} (broken, replaced by orphan)")
                        if not dry_run:
                            db_pointed.unlink()
                        stats["duplicates_deleted"] += 1
                    # Remove promoted orphan from orphan list
                    orphans.remove(orphan)
                    db_pointed = orphan
                    db_ok = True
                    promoted = True
                    stats["orphan_promoted"] += 1
                    break

        # Delete remaining orphans (duplicates)
        for orphan in orphans:
            print(f"  DELETE  {arxiv_id}: {orphan.name} (duplicate orphan)")
            if not dry_run:
                orphan.unlink()
            stats["duplicates_deleted"] += 1

        # If surviving file is still incomplete, reset execution
        if not db_ok and row["status"] == "done":
            print(f"  RESET   {arxiv_id}: incomplete report -> pending")
            if not dry_run:
                conn.execute(
                    "UPDATE executions SET status = 'pending', result_path = NULL, error = NULL, updated_at = ? WHERE arxiv_id = ?",
                    (datetime.now(timezone.utc).isoformat(), arxiv_id),
                )
                conn.commit()
            if db_pointed and db_pointed.exists():
                print(f"  DELETE  {arxiv_id}: {db_pointed.name} (incomplete, will re-process)")
                if not dry_run:
                    db_pointed.unlink()
                stats["incomplete_deleted"] += 1
            stats["incomplete_reset"] += 1
        elif db_ok and not orphans and not promoted:
            stats["already_ok"] += 1

    conn.close()
    return stats


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
    parser.add_argument("--fix", action="store_true", help="Fix duplicates and reset incomplete reports")
    parser.add_argument("--dry-run", action="store_true", help="With --fix, show what would happen without changing anything")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        print(f"ERROR: data directory not found: {data_dir}")
        return 1

    all_md_files = sorted(data_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    md_total_count = len(all_md_files)

    # --fix mode: scan and repair
    if args.fix:
        fix_files = all_md_files[: args.latest] if args.latest != 10 else all_md_files  # default=scan all
        print(f"fix mode: scanning {len(fix_files)} of {md_total_count} md files{' (dry-run)' if args.dry_run else ''}")
        stats = run_fix(data_dir, fix_files, dry_run=args.dry_run)
        print(f"\nfix results:")
        for k, v in stats.items():
            print(f"  {k}={v}")
        return 0

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
