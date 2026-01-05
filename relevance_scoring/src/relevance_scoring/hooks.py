import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import yaml
from flatagents import MachineHooks, get_logger
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
        conn.commit()
        conn.close()
        return context

    def _load_config(self, context: Dict[str, Any]) -> Dict[str, Any]:
        config_path = Path(context["config_path"])
        config = yaml.safe_load(config_path.read_text())

        embedding_cfg = config.get("embedding") or {}
        anchors = config.get("anchors") or []
        if not anchors:
            raise ValueError("No anchors found in relevance_scoring.yml")

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
        return context

    def _select_targets(self, context: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(context.get("limit") or 0)
        if limit <= 0:
            context["targets"] = []
            context["scored_count"] = 0
            return context

        db_path = Path(context["db_path"])
        since = normalize_date(context.get("since"))
        until = normalize_date(context.get("until"), is_end=True)

        where_clauses = ["p.llm_relevant = 1"]
        params: List[Any] = []

        if since:
            where_clauses.append("COALESCE(p.updated_at, p.published_at) >= ?")
            params.append(since)
        if until:
            where_clauses.append("COALESCE(p.updated_at, p.published_at) <= ?")
            params.append(until)

        query = f"""
            SELECT p.id, p.title, p.abstract
            FROM papers p
            LEFT JOIN paper_relevance pr ON p.id = pr.paper_id
            WHERE {" AND ".join(where_clauses)}
              AND pr.paper_id IS NULL
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
            batch = targets[idx : idx + batch_size]
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

            # Write batch to DB immediately
            batch_scored = []
            for item, anchor_idx, score in zip(batch, best_idx, best_scores):
                batch_scored.append(
                    {
                        "paper_id": item["paper_id"],
                        "fmr_score": float(score),
                        "best_anchor": anchors[int(anchor_idx)],
                    }
                )
            
            if not dry_run and db_path:
                self._write_batch_scores(db_path, batch_scored)
            scored_count += len(batch_scored)

        context["scored"] = []  # Already written to DB
        context["scored_count"] = scored_count
        return context
    
    def _write_batch_scores(self, db_path: Path, batch: List[Dict[str, Any]]) -> None:
        """Write a batch of scores to the database immediately."""
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        now = utc_now_iso()
        
        for item in batch:
            conn.execute(
                """
                INSERT INTO paper_relevance (
                    paper_id, fmr_score, scored_at, details_json
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    fmr_score = excluded.fmr_score,
                    scored_at = excluded.scored_at,
                    details_json = excluded.details_json
                """,
                (
                    item["paper_id"],
                    item["fmr_score"],
                    now,
                    json.dumps(
                        {
                            "best_anchor": item["best_anchor"],
                            "method": "embedding_max",
                        },
                        ensure_ascii=True,
                    ),
                ),
            )
        
        conn.commit()
        conn.close()

    def _upsert_scores(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if parse_bool(context.get("dry_run")):
            self.logger.info("Dry run: skipping database writes")
            return context

        scored = context.get("scored") or []
        if not scored:
            return context

        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        now = utc_now_iso()
        for item in scored:
            conn.execute(
                """
                INSERT INTO paper_relevance (
                    paper_id, fmr_score, scored_at, details_json
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    fmr_score = excluded.fmr_score,
                    scored_at = excluded.scored_at,
                    details_json = excluded.details_json
                """,
                (
                    item["paper_id"],
                    item["fmr_score"],
                    now,
                    json.dumps(
                        {
                            "best_anchor": item["best_anchor"],
                            "method": "embedding_max",
                        },
                        ensure_ascii=True,
                    ),
                ),
            )

        conn.commit()
        conn.close()
        return context
