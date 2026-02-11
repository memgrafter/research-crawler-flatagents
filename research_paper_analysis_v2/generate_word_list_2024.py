#!/usr/bin/env python3
"""Generate a shotgun 2024 ML/LLM word list from arXiv SQLite metadata.

This script is intentionally heuristic-only (no LLM calls, no embeddings)
so it can run fast and cheap on title+abstract text.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

CORE_CATEGORIES: Tuple[str, ...] = (
    "cs.LG",
    "cs.CL",
    "cs.AI",
    "cs.CV",
    "cs.IR",
    "cs.NE",
    "stat.ML",
    "cs.MA",
    "cs.SD",
    "eess.AS",
    "eess.IV",
)

# Broad lexical net for initial candidate selection.
SHOTGUN_PATTERNS: Tuple[str, ...] = (
    "large language model",
    "language model",
    "llm",
    "transformer",
    "self attention",
    "multi head attention",
    "retrieval augmented",
    "rag",
    "agent",
    "agentic",
    "reasoning",
    "chain of thought",
    "instruction tuning",
    "fine tuning",
    "alignment",
    "ai safety",
    "hallucination",
    "pretrain",
    "finetun",
    "embedding",
    "mixture of experts",
    "moe",
    "diffusion",
    "multimodal",
    "vision language",
    "benchmark",
    "evaluation",
    "quantization",
    "distillation",
    "reinforcement learning",
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "between",
    "by",
    "can",
    "for",
    "from",
    "had",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "over",
    "that",
    "the",
    "their",
    "this",
    "through",
    "to",
    "under",
    "using",
    "use",
    "via",
    "we",
    "with",
    "within",
    "without",
    "our",
    "new",
    "novel",
    "towards",
    "toward",
    "study",
    "paper",
    "approach",
    "method",
    "methods",
    "task",
    "tasks",
    "results",
    "show",
    "shows",
    "based",
    "high",
    "low",
}

GENERIC_TERMS = {
    "model",
    "models",
    "learning",
    "data",
    "neural",
    "network",
    "networks",
    "performance",
    "framework",
    "system",
    "systems",
    "analysis",
    "efficient",
    "improving",
    "improve",
    "leverage",
    "state-of-the-art",
    "real-world",
    "robust",
    "effective",
}

TECH_UNIGRAM_ALLOWLIST = {
    "llm",
    "llms",
    "rag",
    "rlhf",
    "rlaif",
    "dpo",
    "ppo",
    "sft",
    "moe",
    "lora",
    "qlora",
    "bert",
    "gpt",
    "llama",
    "mistral",
    "mixtral",
    "qwen",
    "gemini",
    "claude",
    "transformer",
    "diffusion",
    "multimodal",
    "retrieval",
    "reranking",
    "quantization",
    "distillation",
    "alignment",
    "hallucination",
    "tokenization",
    "pretraining",
    "finetuning",
    "inference",
    "benchmark",
    "reasoning",
    "agentic",
}

BANNED_ACRONYMS = {
    "ET",
    "AL",
    "FIG",
    "TAB",
    "IEEE",
    "ACM",
}

ALLOWED_TWO_LETTER_ACRONYMS = {
    "AI",
    "ML",
    "RL",
    "CV",
    "NLP",
}

TECH_REGEX = re.compile(
    r"\b(?:"
    r"llm|rag|"
    r"agent(?:ic)?|"
    r"reason(?:ing)?|"
    r"align(?:ment|ed|ing)?|"
    r"safety|"
    r"hallucinat(?:ion|ions|e|ing)?|"
    r"transformer|attention|"
    r"retriev(?:al|e|ed|ing)?|"
    r"embed(?:ding|d|s)?|"
    r"diffusion|"
    r"quantiz(?:ation|e|ed|ing)?|"
    r"distill(?:ation|ed|ing)?|"
    r"benchmark|"
    r"pretrain(?:ing|ed)?|"
    r"finetun(?:e|ing|ed)?|"
    r"multimodal|"
    r"token(?:ization)?|"
    r"moe|rlhf|dpo|ppo|sft|"
    r"gpt|bert|llama|mistral|qwen|gemini|claude"
    r")\b"
)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+._/-]{1,30}")
ACRONYM_RE = re.compile(r"\b[A-Z]{2,}(?:[-_/]?[A-Z0-9]{1,8})?\b")


@dataclass
class TermStat:
    kind: str
    tf: int = 0
    df: int = 0
    forms: Counter = field(default_factory=Counter)


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def _validate_identifier(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name


def _choose_score_expr(conn: sqlite3.Connection, preferred: str, fallback: str) -> Tuple[str, str]:
    preferred = _validate_identifier(preferred)
    fallback = _validate_identifier(fallback)

    cols = set(_table_columns(conn, "paper_relevance"))
    has_preferred = preferred in cols
    has_fallback = fallback in cols

    if has_preferred and has_fallback and preferred != fallback:
        expr = f"COALESCE(pr.{preferred}, pr.{fallback}, 0.0)"
        label = f"{preferred}_or_{fallback}"
        return expr, label
    if has_preferred:
        return f"COALESCE(pr.{preferred}, 0.0)", preferred
    if has_fallback:
        return f"COALESCE(pr.{fallback}, 0.0)", fallback

    raise RuntimeError(
        f"Neither score column exists in paper_relevance: preferred={preferred}, fallback={fallback}"
    )


def _normalize_token(token: str) -> str:
    token = token.strip("-_/+.").lower()
    if token == "llms":
        token = "llm"
    if token.endswith("s") and len(token) > 5 and not token.endswith("ss"):
        token = token[:-1]
    return token


def _tokenize(text: str) -> List[str]:
    lowered = text.lower().replace("\n", " ")
    lowered = lowered.replace("–", "-").replace("—", "-")
    raw_tokens = TOKEN_RE.findall(lowered)

    tokens: List[str] = []
    for raw in raw_tokens:
        tok = _normalize_token(raw)
        if not tok:
            continue
        if tok in STOPWORDS:
            continue
        if tok.isdigit():
            continue
        if len(tok) < 2:
            continue
        tokens.append(tok)
    return tokens


def _is_technical_unigram(token: str) -> bool:
    if token in GENERIC_TERMS:
        return False
    if token in TECH_UNIGRAM_ALLOWLIST:
        return True
    if any(ch.isdigit() for ch in token):
        return True
    if "-" in token or "/" in token or "+" in token:
        return True
    return bool(TECH_REGEX.search(token))


def _is_technical_phrase(phrase: str) -> bool:
    if any(ch.isdigit() for ch in phrase):
        return True
    return bool(TECH_REGEX.search(phrase))


def _is_noise_phrase(phrase: str, chunk: List[str]) -> bool:
    if phrase in {"state-of-the-art", "real-world"}:
        return True
    if chunk[0] in {"model", "models", "framework", "system"}:
        return True
    if chunk[-1] in {"framework", "system", "systems"}:
        return True
    return False


def _extract_terms(title: str, abstract: str) -> List[Tuple[str, str, str]]:
    """Return (canonical, display, kind) term tuples for one document."""
    text = f"{title} {abstract}".strip()
    tokens = _tokenize(text)
    if not tokens:
        return []

    out: List[Tuple[str, str, str]] = []

    # Technical unigrams (strict gate).
    for tok in tokens:
        if _is_technical_unigram(tok):
            out.append((tok, tok, "unigram"))

    # Technical bi/tri-grams (shotgun but filtered).
    for n in (2, 3):
        for i in range(0, len(tokens) - n + 1):
            chunk = tokens[i : i + n]
            if chunk[0] in STOPWORDS or chunk[-1] in STOPWORDS:
                continue
            if all(c in GENERIC_TERMS for c in chunk):
                continue
            phrase = " ".join(chunk)
            if "large language model" in phrase and "llm" in phrase:
                phrase = "large language model"
            elif "language model" in phrase and "llm" in phrase:
                phrase = "language model"

            if _is_noise_phrase(phrase, chunk):
                continue
            if _is_technical_phrase(phrase):
                out.append((phrase, phrase, "phrase"))

    # Acronyms/model tags from raw casing.
    for m in ACRONYM_RE.findall(text):
        ac = m.strip("-_/.").upper()
        if len(ac) < 2 or len(ac) > 20:
            continue
        if len(ac) == 2 and ac not in ALLOWED_TWO_LETTER_ACRONYMS:
            continue
        if ac in BANNED_ACRONYMS:
            continue
        # Ignore pure years as acronyms.
        if ac.isdigit():
            continue
        canonical = ac.lower()
        out.append((canonical, ac, "acronym"))

    return out


def _build_query(score_expr: str, category_count: int, pattern_count: int) -> str:
    category_placeholders = ",".join(["?"] * category_count)
    pattern_clauses = " OR ".join(["LOWER(p.title || ' ' || p.abstract) LIKE ?"] * pattern_count)

    return f"""
        SELECT
            p.id,
            p.arxiv_id,
            p.primary_category,
            p.title,
            p.abstract,
            {score_expr} AS score
        FROM papers p
        JOIN paper_relevance pr ON pr.paper_id = p.id
        WHERE p.arxiv_id LIKE ?
          AND {score_expr} >= ?
          AND (
                p.primary_category IN ({category_placeholders})
                OR p.llm_relevant = 1
                OR ({pattern_clauses})
          )
        ORDER BY score DESC
        LIMIT ?
    """


def _rank_terms(
    stats: Dict[str, TermStat],
    total_docs: int,
    min_df_phrase: int,
    min_df_unigram: int,
    min_df_acronym: int,
    max_doc_ratio: float,
) -> List[Tuple[str, str, float, int, int, str]]:
    ranked: List[Tuple[str, str, float, int, int, str]] = []

    for canonical, st in stats.items():
        if st.kind == "phrase":
            min_df = min_df_phrase
        elif st.kind == "acronym":
            min_df = min_df_acronym
        else:
            min_df = min_df_unigram

        if st.df < min_df:
            continue

        doc_ratio = (st.df / total_docs) if total_docs > 0 else 0.0
        if doc_ratio > max_doc_ratio:
            continue

        idf = math.log((total_docs + 1.0) / (st.df + 1.0)) + 1.0
        score = st.df * idf * math.log1p(st.tf)

        if st.kind == "acronym":
            score *= 1.15
        if any(ch.isdigit() for ch in canonical):
            score *= 1.05

        display = st.forms.most_common(1)[0][0] if st.forms else canonical
        ranked.append((canonical, display, score, st.df, st.tf, st.kind))

    ranked.sort(key=lambda x: (x[2], x[3], x[4]), reverse=True)
    return ranked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate shotgun 2024 ML/LLM word list")
    parser.add_argument(
        "--db-path",
        default="../arxiv_crawler/data/arxiv.sqlite",
        help="Path to arXiv SQLite DB (default: ../arxiv_crawler/data/arxiv.sqlite)",
    )
    parser.add_argument("--year-prefix", default="24", help="arXiv ID year prefix (default: 24)")
    parser.add_argument(
        "--score-column",
        default="fmr_2024",
        help="Preferred score column in paper_relevance (default: fmr_2024)",
    )
    parser.add_argument(
        "--fallback-score-column",
        default="fmr_score",
        help="Fallback score column if preferred is missing (default: fmr_score)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.35,
        help="Minimum score to include in candidate pool (default: 0.35)",
    )
    parser.add_argument(
        "--top-papers",
        type=int,
        default=10000,
        help="Max candidate papers to scan (default: 10000)",
    )
    parser.add_argument(
        "--max-terms",
        type=int,
        default=700,
        help="Max terms to emit to word list (default: 700)",
    )
    parser.add_argument(
        "--min-df-phrase",
        type=int,
        default=7,
        help="Minimum document frequency for phrases (default: 7)",
    )
    parser.add_argument(
        "--min-df-unigram",
        type=int,
        default=15,
        help="Minimum document frequency for unigrams (default: 15)",
    )
    parser.add_argument(
        "--min-df-acronym",
        type=int,
        default=4,
        help="Minimum document frequency for acronyms (default: 4)",
    )
    parser.add_argument(
        "--max-doc-ratio",
        type=float,
        default=0.33,
        help="Drop terms appearing in more than this fraction of docs (default: 0.33)",
    )
    parser.add_argument(
        "--output-dir",
        default="queries/word_clouds_2024",
        help="Output directory for word list (default: queries/word_clouds_2024)",
    )
    parser.add_argument(
        "--output-file",
        default="shotgun_ml_llm_2024.txt",
        help="Output filename inside output-dir (default: shotgun_ml_llm_2024.txt)",
    )
    parser.add_argument(
        "--csv-output",
        default="data/word_list_2024_candidates.csv",
        help="CSV output path for scored candidate terms",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files; print top terms only",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent

    db_path = _resolve_path(project_root, args.db_path)
    output_dir = _resolve_path(project_root, args.output_dir)
    output_path = output_dir / args.output_file
    csv_path = _resolve_path(project_root, args.csv_output)

    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    score_expr, score_label = _choose_score_expr(conn, args.score_column, args.fallback_score_column)
    sql = _build_query(
        score_expr=score_expr,
        category_count=len(CORE_CATEGORIES),
        pattern_count=len(SHOTGUN_PATTERNS),
    )

    params: List[object] = [f"{args.year_prefix}%", float(args.min_score)]
    params.extend(CORE_CATEGORIES)
    params.extend([f"%{p.lower()}%" for p in SHOTGUN_PATTERNS])
    params.append(int(args.top_papers))

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        print("No candidate papers found. Try lowering --min-score or increasing coverage patterns.")
        return 0

    stats: Dict[str, TermStat] = {}

    for idx, row in enumerate(rows, start=1):
        title = row["title"] or ""
        abstract = row["abstract"] or ""
        extracted = _extract_terms(title, abstract)
        if not extracted:
            continue

        seen_in_doc = set()
        for canonical, display, kind in extracted:
            st = stats.get(canonical)
            if st is None:
                st = TermStat(kind=kind)
                stats[canonical] = st
            st.tf += 1
            st.forms[display] += 1

            if canonical not in seen_in_doc:
                st.df += 1
                seen_in_doc.add(canonical)

        if idx % 2000 == 0:
            print(f"Processed {idx}/{len(rows)} papers...")

    ranked = _rank_terms(
        stats=stats,
        total_docs=len(rows),
        min_df_phrase=int(args.min_df_phrase),
        min_df_unigram=int(args.min_df_unigram),
        min_df_acronym=int(args.min_df_acronym),
        max_doc_ratio=float(args.max_doc_ratio),
    )

    top_ranked = ranked[: int(args.max_terms)]

    print(f"Candidate papers: {len(rows)}")
    print(f"Scored terms: {len(ranked)}")
    print(f"Emitting terms: {len(top_ranked)}")
    print("Top 30 preview:")
    for item in top_ranked[:30]:
        _, display, score, df, tf, kind = item
        print(f"  {display:40s} | kind={kind:7s} df={df:4d} tf={tf:5d} score={score:8.2f}")

    if args.dry_run:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Word Cloud: 2024 Shotgun ML+LLM Terms\n")
        f.write(f"# generated_by: generate_word_list_2024.py\n")
        f.write(f"# candidate_papers: {len(rows)}\n")
        f.write(f"# score_column: {score_label}\n")
        f.write(f"# min_score: {args.min_score}\n")
        f.write(f"# top_papers: {args.top_papers}\n")
        f.write("\n")
        for _, display, *_ in top_ranked:
            f.write(f"{display}\n")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["term_canonical", "term_display", "kind", "score", "doc_freq", "term_freq"])
        for canonical, display, score, df, tf, kind in ranked:
            writer.writerow([canonical, display, kind, f"{score:.6f}", df, tf])

    print(f"Wrote word list: {output_path}")
    print(f"Wrote scored terms CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
