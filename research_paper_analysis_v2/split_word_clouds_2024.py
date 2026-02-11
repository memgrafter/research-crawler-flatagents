#!/usr/bin/env python3
"""Split 2024 shotgun terms into per-word-cloud files.

Inputs:
- data/word_list_2024_candidates.csv (from generate_word_list_2024.py)

Outputs (default dir: queries/word_clouds_2024):
- general_ml_llm_high_recall_95.txt
- 14 themed word-cloud files aligned with existing 2025 taxonomy names
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Pattern, Tuple

THEME_FILES: Tuple[str, ...] = (
    "core_architecture_components",
    "model_families_specific_implementations",
    "training_optimization",
    "reasoning_cognition",
    "information_retrieval_knowledge",
    "robustness_safety",
    "evaluation_benchmarking",
    "efficiency_scaling",
    "multimodal_cross_domain",
    "deployment_production",
    "continual_adaptive_learning",
    "interpretability_transparency",
    "human_ai_interaction",
    "data_training_paradigms",
)

GENERAL_FILE = "general_ml_llm_high_recall_95"

HIGH_RECALL_ANCHORS: Tuple[str, ...] = (
    "large language model",
    "language model",
    "llm",
    "foundation model",
    "transformer",
    "self attention",
    "multi-head attention",
    "attention mechanism",
    "encoder-decoder",
    "decoder-only",
    "mixture of experts",
    "moe",
    "tokenization",
    "context window",
    "long context",
    "pretraining",
    "fine-tuning",
    "instruction tuning",
    "supervised fine-tuning",
    "rlhf",
    "dpo",
    "ppo",
    "reward model",
    "alignment",
    "ai safety",
    "jailbreak",
    "hallucination",
    "factuality",
    "reasoning",
    "chain of thought",
    "agent",
    "agentic",
    "tool use",
    "function calling",
    "retrieval augmented generation",
    "rag",
    "retrieval",
    "reranking",
    "vector database",
    "embedding model",
    "knowledge graph",
    "semantic search",
    "benchmark",
    "evaluation",
    "mmlu",
    "gsm8k",
    "truthfulqa",
    "multimodal",
    "vision-language",
    "diffusion",
    "quantization",
    "distillation",
    "pruning",
    "inference optimization",
    "latency",
    "throughput",
    "scaling law",
    "continual learning",
    "domain adaptation",
    "interpretability",
    "mechanistic interpretability",
    "attribution",
    "human-ai interaction",
    "prompt engineering",
    "synthetic data",
    "self-supervised learning",
    "contrastive learning",
)

THEME_RULES: Dict[str, List[Pattern[str]]] = {
    "core_architecture_components": [
        re.compile(r"\b(transformer|attention|encoder|decoder|token|rope|alibi|position|embedding)\b", re.I),
    ],
    "model_families_specific_implementations": [
        re.compile(r"\b(gpt|bert|t5|llama|mistral|mixtral|qwen|gemini|claude|phi|falcon|mpt|olmo|vit|clip)\b", re.I),
    ],
    "training_optimization": [
        re.compile(r"\b(pretrain|fine[- ]?tun|instruction tun|sft|rlhf|rlaif|dpo|ppo|optimizer|gradient|backprop|curriculum|distill)\b", re.I),
    ],
    "reasoning_cognition": [
        re.compile(r"\b(reason|chain of thought|cot|self[- ]consistency|deliberat|planning|reflection|metacognit)\b", re.I),
    ],
    "information_retrieval_knowledge": [
        re.compile(r"\b(retriev|rag|rerank|rank|search|index|embedding|vector|faiss|qdrant|weaviate|knowledge graph|semantic)\b", re.I),
    ],
    "robustness_safety": [
        re.compile(r"\b(safety|alignment|jailbreak|adversarial|robust|toxicity|bias|fairness|hallucination|truthful|harmless)\b", re.I),
    ],
    "evaluation_benchmarking": [
        re.compile(r"\b(benchmark|evaluation|metric|leaderboard|mmlu|hellaswag|gsm8k|truthfulqa|humaneval|mtbench|alpaca)\b", re.I),
    ],
    "efficiency_scaling": [
        re.compile(r"\b(quantiz|prun|spars|compress|distill|efficien|latency|throughput|speedup|memory|cache|scal)\b", re.I),
    ],
    "multimodal_cross_domain": [
        re.compile(r"\b(multimodal|vision[- ]language|video|audio|speech|image|cross[- ]modal|vlm|mllm)\b", re.I),
    ],
    "deployment_production": [
        re.compile(r"\b(deploy|production|serving|inference engine|on-device|edge|runtime|monitoring|observability)\b", re.I),
    ],
    "continual_adaptive_learning": [
        re.compile(r"\b(continual|lifelong|online learning|domain adaptation|transfer learning|adapti|incremental)\b", re.I),
    ],
    "interpretability_transparency": [
        re.compile(r"\b(interpret|explain|attribution|saliency|mechanistic|transparen|feature importance|probing)\b", re.I),
    ],
    "human_ai_interaction": [
        re.compile(r"\b(human[- ]ai|human feedback|preference|chatbot|instruction following|interactive|user study|alignment)\b", re.I),
    ],
    "data_training_paradigms": [
        re.compile(r"\b(synthetic data|data curation|data mixture|self-supervised|contrastive|augmentation|curriculum|preference data)\b", re.I),
    ],
}


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _clean_term(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip())


def _key(term: str) -> str:
    return _clean_term(term).lower()


def _read_existing_terms(path: Path) -> List[str]:
    if not path.exists():
        return []
    terms: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms


def _dedupe_keep_order(terms: Iterable[str]) -> List[str]:
    seen: OrderedDict[str, str] = OrderedDict()
    for term in terms:
        cleaned = _clean_term(term)
        if not cleaned:
            continue
        k = _key(cleaned)
        if k not in seen:
            seen[k] = cleaned
    return list(seen.values())


def _load_candidates(csv_path: Path, min_score: float) -> List[Tuple[str, float]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Candidates CSV not found: {csv_path}")

    out: List[Tuple[str, float]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            term = _clean_term(row.get("term_display", ""))
            if not term:
                continue
            try:
                score = float(row.get("score", "0") or 0.0)
            except ValueError:
                score = 0.0
            if score < min_score:
                continue
            out.append((term, score))

    # CSV is already ranked, but enforce deterministic ordering anyway.
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _term_themes(term: str) -> List[str]:
    hits: List[str] = []
    for theme, patterns in THEME_RULES.items():
        if any(p.search(term) for p in patterns):
            hits.append(theme)
    return hits


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split shotgun 2024 terms into per-cloud files")
    parser.add_argument(
        "--input-csv",
        default="data/word_list_2024_candidates.csv",
        help="CSV from generate_word_list_2024.py",
    )
    parser.add_argument(
        "--output-dir",
        default="queries/word_clouds_2024",
        help="Where to write per-cloud term files",
    )
    parser.add_argument(
        "--seed-dir",
        default="queries/word_clouds",
        help="Existing 2025 cloud dir used for backfill seeds",
    )
    parser.add_argument(
        "--theme-cap",
        type=int,
        default=260,
        help="Max terms per themed cloud (default: 260)",
    )
    parser.add_argument(
        "--general-cap",
        type=int,
        default=2200,
        help="Max terms for general high-recall cloud (default: 2200)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum candidate score to consider (default: 0.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only, do not write files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent

    input_csv = _resolve(project_root, args.input_csv)
    output_dir = _resolve(project_root, args.output_dir)
    seed_dir = _resolve(project_root, args.seed_dir)

    candidates = _load_candidates(input_csv, min_score=float(args.min_score))
    if not candidates:
        print("No candidate terms loaded from CSV.")
        return 0

    themed: Dict[str, List[str]] = {name: [] for name in THEME_FILES}

    ranked_terms = [t for t, _ in candidates]

    for term in ranked_terms:
        for theme in _term_themes(term):
            themed[theme].append(term)

    # Backfill each themed cloud with existing seeds to improve recall.
    for theme in THEME_FILES:
        seed_terms = _read_existing_terms(seed_dir / f"{theme}.txt")
        merged = _dedupe_keep_order([*themed[theme], *seed_terms])
        themed[theme] = merged[: int(args.theme_cap)]

    # General high-recall cloud: anchors + all ranked candidates + all existing seeds.
    seed_all: List[str] = []
    for theme in THEME_FILES:
        seed_all.extend(_read_existing_terms(seed_dir / f"{theme}.txt"))

    general_terms = _dedupe_keep_order([
        *HIGH_RECALL_ANCHORS,
        *ranked_terms,
        *seed_all,
    ])[: int(args.general_cap)]

    if args.dry_run:
        print(f"Loaded candidate terms: {len(ranked_terms)}")
        print(f"General cloud terms: {len(general_terms)}")
        for theme in THEME_FILES:
            print(f"  {theme}: {len(themed[theme])}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    general_path = output_dir / f"{GENERAL_FILE}.txt"
    with general_path.open("w", encoding="utf-8") as f:
        f.write("# Word Cloud: General ML/LLM High-Recall (~95% target coverage)\n")
        f.write("# source: 2024 shotgun candidates + existing seeds + fixed anchors\n\n")
        for term in general_terms:
            f.write(f"{term}\n")

    for theme in THEME_FILES:
        path = output_dir / f"{theme}.txt"
        title = theme.replace("_", " ").title()
        with path.open("w", encoding="utf-8") as f:
            f.write(f"# Word Cloud: {title} (2024 shotgun split)\n")
            f.write("# source: 2024 shotgun candidates + existing theme seeds\n\n")
            for term in themed[theme]:
                f.write(f"{term}\n")

    print(f"Wrote general cloud: {general_path} ({len(general_terms)} terms)")
    for theme in THEME_FILES:
        print(f"Wrote themed cloud: {output_dir / (theme + '.txt')} ({len(themed[theme])} terms)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
