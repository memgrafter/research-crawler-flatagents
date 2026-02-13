import glob
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import numpy as np
import yaml
from flatmachines import MachineHooks, get_logger
from sentence_transformers import SentenceTransformer


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_date(value: Optional[str], is_end: bool = False) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if text.lower() in {"none", "null"}:
        return None
    if len(text) == 10 and text.count("-") == 2:
        suffix = "T23:59:59+00:00" if is_end else "T00:00:00+00:00"
        return f"{text}{suffix}"
    return text


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


class ScoringHooks(MachineHooks):
    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    def on_action(self, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "init_db": self._init_db,
            "load_config": self._load_config,
            "select_targets": self._select_targets,
            "score_targets": self._score_targets,
            "upsert_scores": self._upsert_scores,
        }
        handler = handlers.get(action_name)
        if not handler:
            raise ValueError(f"Unknown action: {action_name}")
        return handler(context)

    def _init_db(self, context: Dict[str, Any]) -> Dict[str, Any]:
        db_path = Path(context["db_path"])
        db_path.parent.mkdir(parents=True, exist_ok=True)

        schema_path = Path(__file__).resolve().parents[2] / "schema.sql"
        schema_sql = schema_path.read_text()

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)
        self._ensure_column(
            conn,
            table="paper_relevance",
            column="fmr_2024",
            ddl="fmr_2024 REAL",
        )
        self._ensure_index(
            conn,
            name="idx_paper_relevance_fmr_2024",
            ddl="CREATE INDEX IF NOT EXISTS idx_paper_relevance_fmr_2024 ON paper_relevance(fmr_2024)",
        )
        conn.commit()
        conn.close()
        return context

    def _load_config(self, context: Dict[str, Any]) -> Dict[str, Any]:
        config_path = Path(context["config_path"])
        config = yaml.safe_load(config_path.read_text())

        embedding_cfg = config.get("embedding") or {}
        anchors: List[str] = [str(x).strip() for x in (config.get("anchors") or []) if str(x).strip()]

        anchor_files = config.get("anchor_files") or []
        if anchor_files:
            anchors.extend(self._load_anchors_from_files(anchor_files, config_path.parent))

        anchors = self._dedupe_keep_order(anchors)

        # Optional LLM-pass exclusions to reduce cross-domain bleed.
        exclude_terms: List[str] = [
            str(x).strip() for x in (config.get("anchor_exclude_terms") or []) if str(x).strip()
        ]
        exclude_files = config.get("anchor_exclude_files") or []
        if exclude_files:
            exclude_terms.extend(self._load_anchors_from_files(exclude_files, config_path.parent))

        exclude_set = {t.lower() for t in self._dedupe_keep_order(exclude_terms)}
        if exclude_set:
            before = len(anchors)
            anchors = [a for a in anchors if a.lower() not in exclude_set]
            removed = before - len(anchors)
            self.logger.info("Applied anchor exclusions: removed %d terms", removed)

        if not anchors:
            raise ValueError("No anchors found in relevance_scoring_2024.yml")

        context["anchors"] = anchors
        context["model_name"] = embedding_cfg.get(
            "model", "sentence-transformers/all-MiniLM-L6-v2"
        )
        context["trust_remote_code"] = bool(embedding_cfg.get("trust_remote_code", False))
        context["model_kwargs"] = embedding_cfg.get("model_kwargs") or {}
        context["config_kwargs"] = embedding_cfg.get("config_kwargs") or {}
        context["normalize"] = bool(embedding_cfg.get("normalize", True))
        context["query_prompt"] = embedding_cfg.get("query_prompt")
        context["batch_size"] = int(
            context.get("batch_size") or embedding_cfg.get("batch_size") or 64
        )

        self.logger.info("Loaded %d deduplicated anchors", len(anchors))
        return context

    def _select_targets(self, context: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(context.get("limit") or 0)
        if limit <= 0:
            context["targets"] = []
            context["scored_count"] = 0
            return context

        db_path = Path(context["db_path"])
        year_prefix = str(context.get("year_prefix") or "24").strip()
        since = normalize_date(context.get("since"))
        until = normalize_date(context.get("until"), is_end=True)
        rescore_existing = parse_bool(context.get("rescore_existing"))

        where_clauses = ["p.llm_relevant = 1", "p.arxiv_id LIKE ?"]
        params: List[Any] = [f"{year_prefix}%"]

        if since:
            where_clauses.append("COALESCE(p.updated_at, p.published_at) >= ?")
            params.append(since)
        if until:
            where_clauses.append("COALESCE(p.updated_at, p.published_at) <= ?")
            params.append(until)
        if not rescore_existing:
            where_clauses.append("(pr.paper_id IS NULL OR pr.fmr_2024 IS NULL)")

        query = f"""
            SELECT p.id, p.title, p.abstract
            FROM papers p
            LEFT JOIN paper_relevance pr ON p.id = pr.paper_id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY COALESCE(p.updated_at, p.published_at) DESC
            LIMIT ?
        """
        params.append(limit)

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        rows = conn.execute(query, params).fetchall()
        conn.close()

        targets = [
            {"paper_id": row[0], "title": row[1] or "", "abstract": row[2] or ""}
            for row in rows
        ]
        context["targets"] = targets
        return context

    def _score_targets(self, context: Dict[str, Any]) -> Dict[str, Any]:
        targets = context.get("targets") or []
        if not targets:
            context["scored"] = []
            context["scored_count"] = 0
            return context

        anchors = context.get("anchors") or []
        model_name = context.get("model_name") or "sentence-transformers/all-MiniLM-L6-v2"
        trust_remote_code = bool(context.get("trust_remote_code"))
        model_kwargs = context.get("model_kwargs") or None
        config_kwargs = context.get("config_kwargs") or None
        batch_size = int(context.get("batch_size") or 64)
        normalize = bool(context.get("normalize"))
        query_prompt = context.get("query_prompt")

        total_targets = len(targets)
        self.logger.info("Loading embedding model: %s", model_name)
        model = SentenceTransformer(
            model_name,
            trust_remote_code=trust_remote_code,
            model_kwargs=model_kwargs,
            config_kwargs=config_kwargs,
        )
        self.logger.info(
            "Scoring %d papers in batches of %d", total_targets, batch_size
        )

        if query_prompt:
            anchor_texts = [f"{query_prompt}{anchor}" for anchor in anchors]
        else:
            anchor_texts = anchors

        anchor_embeddings = model.encode(
            anchor_texts, batch_size=batch_size, normalize_embeddings=normalize
        )
        anchor_embeddings = np.asarray(anchor_embeddings)

        dry_run = parse_bool(context.get("dry_run"))
        db_path = Path(context["db_path"]) if not dry_run else None

        scored_count = 0
        total_batches = max(1, (total_targets + batch_size - 1) // batch_size)
        for idx in range(0, total_targets, batch_size):
            batch_index = idx // batch_size + 1
            if batch_index == 1 or batch_index == total_batches or batch_index % 10 == 0:
                self.logger.info(
                    "Scoring batch %d/%d", batch_index, total_batches
                )
            batch = targets[idx: idx + batch_size]
            texts = [
                f"{item['title']}\n\n{item['abstract']}".strip() for item in batch
            ]
            embeddings = model.encode(
                texts, batch_size=batch_size, normalize_embeddings=normalize
            )
            embeddings = np.asarray(embeddings)

            scores = embeddings @ anchor_embeddings.T
            best_idx = np.argmax(scores, axis=1)
            best_scores = np.max(scores, axis=1)

            batch_scored = []
            for item, anchor_idx, score in zip(batch, best_idx, best_scores):
                batch_scored.append(
                    {
                        "paper_id": item["paper_id"],
                        "fmr_2024": float(score),
                        "best_anchor": anchors[int(anchor_idx)],
                    }
                )

            if not dry_run and db_path:
                self._write_batch_scores(db_path, batch_scored)
            scored_count += len(batch_scored)

        context["scored"] = []
        context["scored_count"] = scored_count
        return context

    def _write_batch_scores(self, db_path: Path, batch: List[Dict[str, Any]]) -> None:
        """Write a batch of 2024 scores to the database immediately."""
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        now = utc_now_iso()

        for item in batch:
            conn.execute(
                """
                INSERT INTO paper_relevance (
                    paper_id, fmr_score, fmr_2024, scored_at, details_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    fmr_2024 = excluded.fmr_2024
                """,
                (
                    item["paper_id"],
                    item["fmr_2024"],
                    item["fmr_2024"],
                    now,
                    json.dumps(
                        {
                            "best_anchor": item["best_anchor"],
                            "method": "embedding_max_2024",
                        },
                        ensure_ascii=True,
                    ),
                ),
            )

        conn.commit()
        conn.close()

    def _upsert_scores(self, context: Dict[str, Any]) -> Dict[str, Any]:
        # Writes happen eagerly in _score_targets by batch.
        return context

    @staticmethod
    def _dedupe_keep_order(items: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for item in items:
            t = str(item).strip()
            if not t:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out

    def _load_anchors_from_files(self, patterns: Sequence[str], base_dir: Path) -> List[str]:
        anchors: List[str] = []
        matched_files: List[Path] = []

        for pattern in patterns:
            raw = str(pattern).strip()
            if not raw:
                continue
            p = Path(raw)
            pattern_text = str(p if p.is_absolute() else (base_dir / p))
            for hit in sorted(glob.glob(pattern_text)):
                path = Path(hit)
                if path.is_file():
                    matched_files.append(path)

        if not matched_files:
            self.logger.warning("No anchor files matched: %s", patterns)
            return anchors

        for file_path in matched_files:
            for line in file_path.read_text(encoding="utf-8").splitlines():
                term = line.strip()
                if not term or term.startswith("#"):
                    continue
                anchors.append(term)

        self.logger.info("Loaded %d anchors from %d files", len(anchors), len(matched_files))
        return anchors

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        ddl: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def _ensure_index(self, conn: sqlite3.Connection, name: str, ddl: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ?",
            (name,),
        ).fetchone()
        if not row:
            conn.execute(ddl)
