#!/usr/bin/env python3
"""Generate organic (corpus-first) multi-word ML/LLM term lists.

Key properties:
- Does NOT require paper_relevance rows (no JOIN to paper_relevance).
- Mines terms from title+abstract for a target arXiv year prefix.
- Focuses on multi-word phrases (2..N grams by default).
- Optionally reranks mined terms by semantic similarity to anchor concepts.

This is intended for historical-year backfills where relevance coverage is sparse,
while still keeping a semantic "close to ML/LLM" signal.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sqlite3

# Cap MPS memory pool to 60% of unified RAM by default.
# Without this, PyTorch's MPS allocator grabs all available system memory (~15GB on 16GB Macs)
# even though the model only needs ~3-5GB. Set to "0.0" to uncap.
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.6")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.5")
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import yaml

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

SHOTGUN_PATTERNS: Tuple[str, ...] = (
    "large language model",
    "language model",
    "machine learning",
    "deep learning",
    "neural network",
    "foundation model",
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

DEFAULT_ANCHORS: Tuple[str, ...] = (
    "language model",
    "large language model",
    "foundation model",
    "transformer",
    "attention mechanism",
    "instruction tuning",
    "fine-tuning",
    "supervised fine-tuning",
    "rlhf",
    "dpo",
    "alignment",
    "ai safety",
    "hallucination",
    "reasoning",
    "chain of thought",
    "agentic system",
    "tool use",
    "retrieval augmented generation",
    "prompt engineering",
)

JUNK_FRAGMENT_TOKENS: Tuple[str, ...] = (
    "such",
    "including",
    "include",
    "includes",
    "e.g",
    "i.e",
    "etc",
    "like",
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
    "method",
    "methods",
    "approach",
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

BANNED_ACRONYMS = {"ET", "AL", "FIG", "TAB", "IEEE", "ACM"}
ALLOWED_TWO_LETTER_ACRONYMS = {"AI", "ML", "RL", "CV"}

TECH_REGEX = re.compile(
    r"\\b(?:"
    r"llm|rag|"
    r"machine learning|deep learning|"
    r"language model|foundation model|"
    r"neural network|"
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
    r")\\b",
    re.I,
)

MODEL_SHAPE_RE = re.compile(
    r"\\b(?:[a-z0-9][a-z0-9+._/-]{1,30}\\s+){1,3}models?\\b", re.I
)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+._/-]{1,30}")
ACRONYM_RE = re.compile(r"\\b[A-Z]{2,}(?:[-_/]?[A-Z0-9]{1,8})?\\b")


@dataclass
class TermStat:
    kind: str
    token_count: int
    tf: int = 0
    df: int = 0
    forms: Counter = field(default_factory=Counter)


@dataclass
class EmbeddingConfig:
    model_name: str
    batch_size: int
    normalize_embeddings: bool
    trust_remote_code: bool
    model_kwargs: Dict[str, object]
    config_kwargs: Dict[str, object]
    query_prompt: Optional[str]


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _validate_identifier(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name


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


def _is_noise_phrase(phrase: str, chunk: Sequence[str]) -> bool:
    if phrase in {"state-of-the-art", "real-world"}:
        return True
    if chunk[0] in {"model", "models", "framework", "system"}:
        return True
    if chunk[-1] in {"framework", "system", "systems"}:
        return True
    if all(tok in GENERIC_TERMS for tok in chunk):
        return True
    if any(tok in JUNK_FRAGMENT_TOKENS for tok in chunk):
        return True
    return False


def _extract_terms(
    title: str,
    abstract: str,
    *,
    ngram_min: int,
    ngram_max: int,
    include_unigrams: bool,
    include_acronyms: bool,
) -> List[Tuple[str, str, str, int]]:
    """Return (canonical, display, kind, token_count) for one document."""
    text = f"{title} {abstract}".strip()
    tokens = _tokenize(text)
    if not tokens:
        return []

    out: List[Tuple[str, str, str, int]] = []

    if include_unigrams:
        for tok in tokens:
            if _is_technical_unigram(tok):
                out.append((tok, tok, "unigram", 1))

    low_n = max(2, int(ngram_min))
    high_n = max(low_n, int(ngram_max))
    for n in range(low_n, high_n + 1):
        for i in range(0, len(tokens) - n + 1):
            chunk = tokens[i : i + n]
            if chunk[0] in STOPWORDS or chunk[-1] in STOPWORDS:
                continue

            phrase = " ".join(chunk)
            if _is_noise_phrase(phrase, chunk):
                continue
            if _is_technical_phrase(phrase):
                out.append((phrase, phrase, "phrase", n))

    if include_acronyms:
        for m in ACRONYM_RE.findall(text):
            ac = m.strip("-_/.").upper()
            if len(ac) < 2 or len(ac) > 20:
                continue
            if len(ac) == 2 and ac not in ALLOWED_TWO_LETTER_ACRONYMS:
                continue
            if ac in BANNED_ACRONYMS:
                continue
            if ac.isdigit():
                continue
            out.append((ac.lower(), ac, "acronym", 1))

    return out


def _build_query(category_count: int, pattern_count: int, use_llm_relevant: bool) -> str:
    category_placeholders = ",".join(["?"] * category_count)
    pattern_clauses = " OR ".join(["LOWER(p.title || ' ' || p.abstract) LIKE ?"] * pattern_count)

    relevance_clause = " OR p.llm_relevant = 1" if use_llm_relevant else ""

    return f"""
        SELECT
            p.id,
            p.arxiv_id,
            p.primary_category,
            p.title,
            p.abstract
        FROM papers p
        WHERE p.arxiv_id LIKE ?
          AND (
                p.primary_category IN ({category_placeholders})
                OR ({pattern_clauses})
                {relevance_clause}
          )
        ORDER BY COALESCE(p.updated_at, p.published_at) DESC
        LIMIT ?
    """


def _rank_terms_lexical(
    stats: Dict[str, TermStat],
    *,
    total_docs: int,
    min_df_phrase: int,
    min_df_unigram: int,
    min_df_acronym: int,
    max_doc_ratio: float,
) -> List[Tuple[str, str, float, int, int, str, int]]:
    ranked: List[Tuple[str, str, float, int, int, str, int]] = []

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
        lexical = st.df * idf * math.log1p(st.tf)

        if st.kind == "acronym":
            lexical *= 1.05
        if st.kind == "phrase":
            lexical *= 1.10
            if st.token_count >= 3:
                lexical *= 1.08
        if any(ch.isdigit() for ch in canonical):
            lexical *= 1.05

        display = st.forms.most_common(1)[0][0] if st.forms else canonical
        ranked.append((canonical, display, lexical, st.df, st.tf, st.kind, st.token_count))

    ranked.sort(key=lambda x: (x[2], x[3], x[4]), reverse=True)
    return ranked


def _read_anchor_lines(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Anchor file not found: {path}")
    out: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen: Dict[str, str] = {}
    out: List[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", item.strip())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen[key] = text
        out.append(text)
    return out


def _load_anchors(raw_anchors: Sequence[str], anchor_files: Sequence[str], base_dir: Path) -> List[str]:
    anchors: List[str] = []
    anchors.extend([x for x in raw_anchors if x.strip()])

    for raw_path in anchor_files:
        path = _resolve_path(base_dir, raw_path)
        anchors.extend(_read_anchor_lines(path))

    if not anchors:
        anchors = list(DEFAULT_ANCHORS)

    return _dedupe_keep_order(anchors)


def _load_exclusions(raw_terms: Sequence[str], files: Sequence[str], base_dir: Path) -> List[str]:
    terms: List[str] = []
    terms.extend([x for x in raw_terms if x.strip()])

    for raw_path in files:
        path = _resolve_path(base_dir, raw_path)
        terms.extend(_read_anchor_lines(path))

    return _dedupe_keep_order(terms)


def _is_excluded_term(term: str, exclusions: Sequence[str]) -> bool:
    text = re.sub(r"\s+", " ", term.strip().lower())
    if not text:
        return True

    for raw_ex in exclusions:
        ex = re.sub(r"\s+", " ", raw_ex.strip().lower())
        if not ex:
            continue

        # Single token exclusions act as token-level filters (e.g., '3d', 'cifar').
        if " " not in ex:
            if re.search(rf"\b{re.escape(ex)}\b", text):
                return True
            continue

        # Multi-token exclusions match as phrase substrings.
        if ex in text:
            return True

    return False


def _resolve_embedding_config(args: argparse.Namespace, base_dir: Path) -> EmbeddingConfig:
    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    batch_size = 128
    normalize_embeddings = True
    trust_remote_code = False
    model_kwargs: Dict[str, object] = {}
    config_kwargs: Dict[str, object] = {}
    query_prompt: Optional[str] = None

    if args.embedding_config:
        cfg_path = _resolve_path(base_dir, args.embedding_config)
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        emb = raw.get("embedding") or {}

        if emb.get("model"):
            model_name = str(emb["model"])
        if emb.get("batch_size") is not None:
            batch_size = int(emb["batch_size"])
        normalize_embeddings = _parse_bool(emb.get("normalize"), normalize_embeddings)
        trust_remote_code = _parse_bool(emb.get("trust_remote_code"), trust_remote_code)

        mk = emb.get("model_kwargs")
        if isinstance(mk, dict):
            model_kwargs = dict(mk)

        ck = emb.get("config_kwargs")
        if isinstance(ck, dict):
            config_kwargs = dict(ck)

        qp = emb.get("query_prompt")
        if qp is not None:
            query_prompt = str(qp)

    if args.model_name:
        model_name = str(args.model_name)
    if args.embedding_batch_size is not None:
        batch_size = int(args.embedding_batch_size)
    if args.trust_remote_code:
        trust_remote_code = True
    if args.disable_embedding_normalization:
        normalize_embeddings = False
    if args.query_prompt is not None:
        query_prompt = str(args.query_prompt)

    return EmbeddingConfig(
        model_name=model_name,
        batch_size=int(batch_size),
        normalize_embeddings=bool(normalize_embeddings),
        trust_remote_code=bool(trust_remote_code),
        model_kwargs=model_kwargs,
        config_kwargs=config_kwargs,
        query_prompt=query_prompt,
    )


def _semantic_max_similarity(
    terms: Sequence[str],
    anchors: Sequence[str],
    *,
    embedding: EmbeddingConfig,
) -> np.ndarray:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for semantic reranking. "
            "Install it in this environment or run with --disable-semantic."
        ) from exc

    model = SentenceTransformer(
        embedding.model_name,
        trust_remote_code=embedding.trust_remote_code,
        model_kwargs=embedding.model_kwargs or None,
        config_kwargs=embedding.config_kwargs or None,
    )

    if embedding.query_prompt:
        anchor_texts = [f"{embedding.query_prompt}{a}" for a in anchors]
    else:
        anchor_texts = list(anchors)

    anchor_emb = model.encode(
        anchor_texts,
        batch_size=embedding.batch_size,
        normalize_embeddings=embedding.normalize_embeddings,
    )
    term_emb = model.encode(
        list(terms),
        batch_size=embedding.batch_size,
        normalize_embeddings=embedding.normalize_embeddings,
    )

    anchor_mat = np.asarray(anchor_emb, dtype=np.float32)
    term_mat = np.asarray(term_emb, dtype=np.float32)

    sims = term_mat @ anchor_mat.T
    return sims.max(axis=1)


def _pattern_bonus(term: str, bonus: float) -> float:
    if bonus <= 0:
        return 0.0
    if MODEL_SHAPE_RE.search(term):
        return bonus
    return 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate corpus-first multi-word ML/LLM term list with semantic reranking"
    )
    parser.add_argument(
        "--db-path",
        default="../arxiv_crawler/data/arxiv.sqlite",
        help="Path to arXiv SQLite DB (default: ../arxiv_crawler/data/arxiv.sqlite)",
    )
    parser.add_argument("--year-prefix", default="23", help="arXiv ID year prefix (default: 23)")
    parser.add_argument(
        "--top-papers",
        type=int,
        default=70000,
        help="Max candidate papers to scan (default: 70000)",
    )
    parser.add_argument(
        "--max-terms",
        type=int,
        default=2500,
        help="Max terms emitted to word list (default: 2500)",
    )
    parser.add_argument(
        "--ngram-min",
        type=int,
        default=2,
        help="Minimum n-gram size for phrase mining (default: 2)",
    )
    parser.add_argument(
        "--ngram-max",
        type=int,
        default=4,
        help="Maximum n-gram size for phrase mining (default: 4)",
    )
    parser.add_argument(
        "--include-unigrams",
        action="store_true",
        help="Include technical unigrams in addition to phrases",
    )
    parser.add_argument(
        "--disable-acronyms",
        action="store_true",
        help="Disable acronym extraction",
    )
    parser.add_argument(
        "--min-df-phrase",
        type=int,
        default=8,
        help="Minimum document frequency for phrases (default: 8)",
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
        default=0.35,
        help="Drop terms appearing in more than this fraction of docs (default: 0.35)",
    )
    parser.add_argument(
        "--disable-llm-relevant-gate",
        action="store_true",
        help="Do not include p.llm_relevant=1 in candidate paper gating",
    )

    parser.add_argument(
        "--anchor",
        action="append",
        default=[],
        help="Anchor term for semantic reranking (repeatable)",
    )
    parser.add_argument(
        "--anchor-file",
        action="append",
        default=[],
        help="Path to newline-delimited anchor list file (repeatable)",
    )
    parser.add_argument(
        "--exclude-term",
        action="append",
        default=[],
        help="Term or token exclusion applied after scoring (repeatable)",
    )
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        help="Path to newline-delimited exclusion list file (repeatable)",
    )
    parser.add_argument(
        "--embedding-config",
        default="",
        help="Optional YAML config path with embedding settings (e.g. ../relevance_scoring_2024.yml)",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="SentenceTransformer model name override",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=None,
        help="Embedding batch size override",
    )
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="Enable trust_remote_code for SentenceTransformer",
    )
    parser.add_argument(
        "--disable-embedding-normalization",
        action="store_true",
        help="Disable normalize_embeddings when encoding anchors/terms",
    )
    parser.add_argument(
        "--query-prompt",
        default=None,
        help="Optional prefix prompt applied to anchors before embedding",
    )
    parser.add_argument(
        "--disable-semantic",
        action="store_true",
        help="Disable semantic reranking and use lexical score only",
    )
    parser.add_argument(
        "--lexical-weight",
        type=float,
        default=0.55,
        help="Weight for normalized lexical score (default: 0.55)",
    )
    parser.add_argument(
        "--semantic-weight",
        type=float,
        default=0.45,
        help="Weight for normalized semantic score (default: 0.45)",
    )
    parser.add_argument(
        "--semantic-floor",
        type=float,
        default=0.00,
        help="Similarity floor before normalization (default: 0.0)",
    )
    parser.add_argument(
        "--model-pattern-boost",
        type=float,
        default=0.05,
        help="Extra boost for '<something> model(s)' phrases (default: 0.05)",
    )

    parser.add_argument(
        "--output-dir",
        default="queries/word_clouds_23_organic_semantic",
        help="Output directory for word list",
    )
    parser.add_argument(
        "--output-file",
        default="shotgun_ml_llm_23_organic_semantic.txt",
        help="Output filename inside output-dir",
    )
    parser.add_argument(
        "--csv-output",
        default="data/word_list_23_organic_semantic_candidates.csv",
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

    sql = _build_query(
        category_count=len(CORE_CATEGORIES),
        pattern_count=len(SHOTGUN_PATTERNS),
        use_llm_relevant=not bool(args.disable_llm_relevant_gate),
    )

    params: List[object] = [f"{args.year_prefix}%"]
    params.extend(CORE_CATEGORIES)
    params.extend([f"%{p.lower()}%" for p in SHOTGUN_PATTERNS])
    params.append(int(args.top_papers))

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        print("No candidate papers found for this year prefix and gating settings.")
        return 0

    stats: Dict[str, TermStat] = {}

    for idx, row in enumerate(rows, start=1):
        title = row["title"] or ""
        abstract = row["abstract"] or ""
        extracted = _extract_terms(
            title,
            abstract,
            ngram_min=int(args.ngram_min),
            ngram_max=int(args.ngram_max),
            include_unigrams=bool(args.include_unigrams),
            include_acronyms=not bool(args.disable_acronyms),
        )
        if not extracted:
            continue

        seen_in_doc = set()
        for canonical, display, kind, token_count in extracted:
            st = stats.get(canonical)
            if st is None:
                st = TermStat(kind=kind, token_count=token_count)
                stats[canonical] = st
            st.tf += 1
            st.forms[display] += 1
            st.token_count = max(st.token_count, token_count)

            if canonical not in seen_in_doc:
                st.df += 1
                seen_in_doc.add(canonical)

        if idx % 5000 == 0:
            print(f"Processed {idx}/{len(rows)} papers...")

    ranked_lex = _rank_terms_lexical(
        stats,
        total_docs=len(rows),
        min_df_phrase=int(args.min_df_phrase),
        min_df_unigram=int(args.min_df_unigram),
        min_df_acronym=int(args.min_df_acronym),
        max_doc_ratio=float(args.max_doc_ratio),
    )

    if not ranked_lex:
        print("No terms survived lexical filtering. Lower DF thresholds or increase top-papers.")
        return 0

    terms_display = [display for _, display, *_ in ranked_lex]
    lexical_values = np.array([lex for _, _, lex, *_ in ranked_lex], dtype=np.float64)

    lex_max = float(lexical_values.max()) if lexical_values.size else 1.0
    if lex_max <= 0:
        lexical_norm = np.zeros_like(lexical_values)
    else:
        lexical_norm = lexical_values / lex_max

    if args.disable_semantic:
        semantic = np.zeros_like(lexical_values)
        semantic_norm = semantic
        anchors = []
        embedding_cfg: Optional[EmbeddingConfig] = None
    else:
        anchors = _load_anchors(args.anchor, args.anchor_file, project_root)
        embedding_cfg = _resolve_embedding_config(args, project_root)
        semantic = _semantic_max_similarity(
            terms_display,
            anchors,
            embedding=embedding_cfg,
        ).astype(np.float64)
        floor = float(args.semantic_floor)
        denom = max(1e-9, 1.0 - floor)
        semantic_norm = np.clip((semantic - floor) / denom, 0.0, 1.0)

    w_lex = max(0.0, float(args.lexical_weight))
    w_sem = max(0.0, float(args.semantic_weight))
    w_sum = (w_lex + w_sem) if (w_lex + w_sem) > 0 else 1.0

    combined = ((w_lex * lexical_norm) + (w_sem * semantic_norm)) / w_sum

    rows_ranked: List[Tuple[str, str, str, float, float, float, int, int, int]] = []
    for idx, (canonical, display, lex_raw, df, tf, kind, token_count) in enumerate(ranked_lex):
        score = float(combined[idx]) + _pattern_bonus(display, float(args.model_pattern_boost))
        rows_ranked.append(
            (
                canonical,
                display,
                kind,
                score,
                float(lex_raw),
                float(semantic[idx]) if semantic.size else 0.0,
                df,
                tf,
                token_count,
            )
        )

    exclusions = _load_exclusions(args.exclude_term, args.exclude_file, project_root)
    removed_by_exclusion = 0
    if exclusions:
        before = len(rows_ranked)
        rows_ranked = [row for row in rows_ranked if not _is_excluded_term(row[1], exclusions)]
        removed_by_exclusion = before - len(rows_ranked)

    rows_ranked.sort(key=lambda x: (x[3], x[4], x[6], x[7]), reverse=True)
    top_rows = rows_ranked[: int(args.max_terms)]

    print(f"Candidate papers: {len(rows)}")
    print(f"Lexically ranked terms: {len(ranked_lex)}")
    if exclusions:
        print(f"Excluded terms removed: {removed_by_exclusion}")
        print(f"Exclusion entries: {len(exclusions)}")
    print(f"Emitting terms: {len(top_rows)}")
    if anchors:
        print(f"Semantic anchors: {len(anchors)}")
    if embedding_cfg is not None:
        print(f"Embedding model: {embedding_cfg.model_name}")
        print(f"Embedding batch size: {embedding_cfg.batch_size}")
    print("Top 30 preview:")
    for _, display, kind, score, lex_raw, sem_raw, df, tf, token_count in top_rows[:30]:
        print(
            f"  {display:44s} | kind={kind:7s} n={token_count} "
            f"df={df:5d} tf={tf:6d} score={score:7.4f} lex={lex_raw:9.2f} sem={sem_raw:6.3f}"
        )

    if args.dry_run:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Word Cloud: Organic Semantic ML+LLM Terms\n")
        f.write("# generated_by: generate_word_list_organic_semantic.py\n")
        f.write(f"# candidate_papers: {len(rows)}\n")
        f.write(f"# year_prefix: {args.year_prefix}\n")
        f.write(f"# ngram_range: {args.ngram_min}-{args.ngram_max}\n")
        f.write(f"# include_unigrams: {bool(args.include_unigrams)}\n")
        f.write(f"# semantic_enabled: {not bool(args.disable_semantic)}\n")
        if anchors:
            f.write(f"# semantic_anchors: {len(anchors)}\n")
        if embedding_cfg is not None:
            f.write(f"# embedding_model: {embedding_cfg.model_name}\n")
            f.write(f"# embedding_batch_size: {embedding_cfg.batch_size}\n")
        if exclusions:
            f.write(f"# exclusions_applied: {len(exclusions)}\n")
            f.write(f"# excluded_terms_removed: {removed_by_exclusion}\n")
        f.write("\n")
        for _, display, *_ in top_rows:
            f.write(f"{display}\n")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "term_canonical",
                "term_display",
                "kind",
                "score",
                "lexical_score",
                "semantic_score",
                "doc_freq",
                "term_freq",
                "token_count",
            ]
        )
        for canonical, display, kind, score, lex_raw, sem_raw, df, tf, token_count in rows_ranked:
            writer.writerow(
                [
                    canonical,
                    display,
                    kind,
                    f"{score:.6f}",
                    f"{lex_raw:.6f}",
                    f"{sem_raw:.6f}",
                    df,
                    tf,
                    token_count,
                ]
            )

    print(f"Wrote word list: {output_path}")
    print(f"Wrote scored terms CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
