"""Standalone distributed worker hooks for research_paper_analysis_v2.

Reuses the existing arxiv_crawler DB + paper_queue schema, but is fully local to
research_paper_analysis_v2.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pypdf import PdfReader

from flatagents import get_logger
from flatmachines import LoggingHooks

logger = get_logger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_title(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return cleaned or "paper"


class DistributedPaperAnalysisHooks(LoggingHooks):
    """Worker lifecycle + paper queue actions for v2."""

    def __init__(self, db_path: Optional[str] = None, log_level: int = 20):
        super().__init__(log_level=log_level)
        repo_root = Path(__file__).resolve().parents[3]
        project_root = Path(__file__).resolve().parents[2]

        self.db_path = db_path or os.environ.get(
            "ARXIV_DB_PATH",
            str(repo_root / "arxiv_crawler" / "data" / "arxiv.sqlite"),
        )
        self.data_dir = project_root / "data"
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def on_action(self, action: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "register_worker": self._register_worker,
            "deregister_worker": self._deregister_worker,
            "claim_paper": self._claim_paper,
            "prepare_paper": self._prepare_paper,
            "complete_paper": self._complete_paper,
            "fail_paper": self._fail_paper,
        }
        handler = handlers.get(action)
        if handler:
            return await handler(context)
        return super().on_action(action, context)

    async def _register_worker(self, context: Dict[str, Any]) -> Dict[str, Any]:
        worker_id = context.get("worker_id")
        if not worker_id:
            raise ValueError("worker_id is required")

        now = _utc_now_iso()
        host = socket.gethostname()
        pid = os.getpid()

        async with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO worker_registry
                (worker_id, status, host, pid, started_at, last_heartbeat)
                VALUES (?, 'active', ?, ?, ?, ?)
                """,
                (worker_id, host, pid, now, now),
            )
            conn.commit()

        context["registered_at"] = now
        return context

    async def _deregister_worker(self, context: Dict[str, Any]) -> Dict[str, Any]:
        worker_id = context.get("worker_id")
        if not worker_id:
            return context

        async with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE worker_registry SET status = 'done', last_heartbeat = ? WHERE worker_id = ?",
                (_utc_now_iso(), worker_id),
            )
            conn.commit()

        return context

    async def _claim_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        worker_id = context.get("worker_id")
        if not worker_id:
            raise ValueError("worker_id is required for claim_paper")

        now = _utc_now_iso()

        async with self._lock:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE paper_queue
                SET status = 'processing',
                    worker = ?,
                    claimed_by = ?,
                    claimed_at = ?,
                    started_at = ?
                WHERE id = (
                    SELECT id FROM paper_queue
                    WHERE status = 'pending'
                    ORDER BY priority DESC, enqueued_at ASC
                    LIMIT 1
                )
                RETURNING id, paper_id
                """,
                (worker_id, worker_id, now, now),
            )
            row = cur.fetchone()
            conn.commit()

            if not row:
                context["queue_id"] = None
                context["paper_id"] = None
                context["paper"] = None
                return context

            queue_id = int(row[0])
            paper_id = int(row[1])

            paper = cur.execute(
                """
                SELECT arxiv_id, title, authors, abstract
                FROM papers
                WHERE id = ?
                """,
                (paper_id,),
            ).fetchone()

        if not paper:
            context["queue_id"] = queue_id
            context["paper_id"] = paper_id
            context["paper"] = None
            return context

        arxiv_id = (paper["arxiv_id"] or "").strip()
        source_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""

        context["queue_id"] = queue_id
        context["paper_id"] = paper_id
        context["paper"] = {
            "arxiv_id": arxiv_id,
            "source_url": source_url,
            "title": paper["title"] or "",
            "authors": paper["authors"] or "",
            "abstract": paper["abstract"] or "",
        }
        return context

    async def _prepare_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        paper = context.get("paper") or {}
        arxiv_id = (paper.get("arxiv_id") or "").strip()

        if not arxiv_id:
            context["prepared"] = False
            context["error"] = "No arxiv_id in claimed paper"
            return context

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            safe_id = arxiv_id.replace("/", "_")
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            pdf_path = self.data_dir / f"{safe_id}.pdf"
            txt_path = self.data_dir / f"{safe_id}.txt"

            if not pdf_path.exists():
                resp = httpx.get(pdf_url, follow_redirects=True, timeout=60.0)
                resp.raise_for_status()
                pdf_path.write_bytes(resp.content)

            if txt_path.exists():
                full_text = txt_path.read_text(encoding="utf-8")
            else:
                reader = PdfReader(pdf_path)
                pages = []
                for i, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    pages.append(f"[PAGE {i + 1}]\n{text}")
                full_text = "\n\n".join(pages)
                txt_path.write_text(full_text, encoding="utf-8")

            section_pattern = r"\n(\d+(?:\.\d+)?)\s+([A-Z][^\n]{3,80})\n"
            section_matches = list(re.finditer(section_pattern, full_text))

            sections: List[str] = []
            for i, match in enumerate(section_matches[:6]):
                section_num = match.group(1)
                section_title = match.group(2).strip()
                start_pos = match.end()

                if i + 1 < len(section_matches):
                    end_pos = section_matches[i + 1].start()
                else:
                    ref_match = re.search(r"\nReferences\s*\n", full_text[start_pos:], re.IGNORECASE)
                    end_pos = start_pos + ref_match.start() if ref_match else len(full_text)

                content = full_text[start_pos:end_pos].strip()[:3000]
                sections.append(f"=== {section_num} {section_title} ===\n{content}")

            section_text = "\n\n".join(sections)

            ref_match = re.search(r"\nReferences\s*\n(.*)", full_text, re.DOTALL | re.IGNORECASE)
            references: List[str] = []
            if ref_match:
                ref_text = ref_match.group(1)
                refs = re.split(r"\n\s*\[?\d+\]?\s*", ref_text)
                references = [r.strip()[:200] for r in refs if len(r.strip()) > 20][:40]

            context["section_text"] = section_text
            context["reference_count"] = len(references)
            context["references_sample"] = references[:10]
            context["prepared"] = True
            context.pop("error", None)
        except Exception as exc:
            logger.exception("prepare_paper failed for %s", arxiv_id)
            context["prepared"] = False
            context["error"] = str(exc)

        return context

    async def _complete_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        queue_id = context.get("queue_id")
        paper = context.get("paper") or {}
        analysis_result = context.get("analysis_result") or {}

        if not queue_id:
            raise ValueError("queue_id is required for complete_paper")

        summary_path = context.get("summary_path")
        formatted_report = analysis_result.get("formatted_report") if isinstance(analysis_result, dict) else None

        if formatted_report and not summary_path:
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                title = paper.get("title") or analysis_result.get("title") or "Untitled"
                arxiv_id = (paper.get("arxiv_id") or "unknown").replace("/", "_")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{arxiv_id}_{slugify_title(title)}_{timestamp}.md"
                file_path = self.data_dir / filename
                file_path.write_text(str(formatted_report), encoding="utf-8")
                summary_path = str(file_path)
                context["summary_path"] = summary_path
            except Exception as exc:
                logger.exception("Failed writing report file")
                context["error"] = str(exc)

        async with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                UPDATE paper_queue
                SET status = 'done',
                    finished_at = ?,
                    summary_path = ?,
                    error = NULL,
                    claimed_by = NULL
                WHERE id = ?
                """,
                (_utc_now_iso(), summary_path, queue_id),
            )
            conn.commit()

        return context

    async def _fail_paper(self, context: Dict[str, Any]) -> Dict[str, Any]:
        queue_id = context.get("queue_id")
        if not queue_id:
            return context

        error_text = str(
            context.get("error")
            or context.get("last_error")
            or (context.get("analysis_result") or {}).get("error")
            or "unknown error"
        )

        async with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT attempts, max_retries FROM paper_queue WHERE id = ?",
                (queue_id,),
            ).fetchone()
            attempts = int((row["attempts"] if row else 0) or 0) + 1
            max_retries = int((row["max_retries"] if row else 3) or 3)

            now = _utc_now_iso()
            if attempts >= max_retries:
                conn.execute(
                    """
                    UPDATE paper_queue
                    SET status = 'poisoned',
                        finished_at = ?,
                        error = ?,
                        attempts = ?,
                        claimed_by = NULL
                    WHERE id = ?
                    """,
                    (now, error_text, attempts, queue_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE paper_queue
                    SET status = 'pending',
                        error = ?,
                        attempts = ?,
                        worker = NULL,
                        claimed_by = NULL,
                        claimed_at = NULL
                    WHERE id = ?
                    """,
                    (error_text, attempts, queue_id),
                )
            conn.commit()

        return context
