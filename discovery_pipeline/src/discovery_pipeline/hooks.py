"""Discovery pipeline hooks for custom actions."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from flatagents import MachineHooks


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


class DiscoveryHooks(MachineHooks):
    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    def on_action(self, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "load_last_run": self._load_last_run,
            "fetch_top_papers": self._fetch_top_papers,
            "save_report": self._save_report,
        }
        handler = handlers.get(action_name)
        if not handler:
            raise ValueError(f"Unknown action: {action_name}")
        return handler(context)

    def _load_last_run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Load the last crawler run date from DB to use as 'since'."""
        db_path = Path(context["db_path"])
        
        if not db_path.exists():
            # First run - use 7 days ago
            since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            context["since"] = since
            self.logger.info("First run, using since=%s", since)
            return context
        
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT MAX(finished_at) FROM crawler_runs WHERE status = 'completed'"
        ).fetchone()
        conn.close()
        
        if row and row[0]:
            # Use last run date
            last_run = row[0][:10]  # Extract date part
            context["since"] = last_run
            self.logger.info("Last run was %s, using as since date", last_run)
        else:
            # Default to 7 days ago
            since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            context["since"] = since
            self.logger.info("No previous runs found, using since=%s", since)
        
        return context

    def _fetch_top_papers(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch top papers for the report."""
        db_path = Path(context["db_path"])
        since = context.get("since")
        
        query = """
            SELECT 
                p.id,
                p.title,
                p.abstract,
                p.arxiv_id,
                COALESCE(p.updated_at, p.published_at) as published_at,
                pr.fmr_score,
                pc.cited_by_count,
                GROUP_CONCAT(a.display_name, ', ') as authors,
                MAX(a.h_index) as max_h_index
            FROM papers p
            JOIN paper_relevance pr ON p.id = pr.paper_id
            LEFT JOIN paper_authors pa ON p.id = pa.paper_id
            LEFT JOIN authors a ON a.openalex_id = pa.author_openalex_id
            LEFT JOIN paper_citations pc ON pc.paper_id = p.id
            WHERE p.llm_relevant = 1
            AND COALESCE(p.updated_at, p.published_at) >= ?
            GROUP BY p.id
            ORDER BY pr.fmr_score DESC, max_h_index DESC NULLS LAST
            LIMIT 20
        """
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, (since or "2000-01-01",)).fetchall()
        conn.close()
        
        papers = []
        for row in rows:
            papers.append({
                "id": row["id"],
                "title": row["title"],
                "abstract": row["abstract"] or "",
                "arxiv_id": row["arxiv_id"],
                "published_at": row["published_at"],
                "fmr_score": row["fmr_score"],
                "cited_by_count": row["cited_by_count"] or 0,
                "authors": row["authors"] or "Unknown",
                "max_h_index": row["max_h_index"],
            })
        
        context["top_papers"] = papers
        self.logger.info("Fetched %d top papers for report", len(papers))
        return context

    def _save_report(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Save the generated report to a file."""
        report_path = Path(context.get("report_path") or "./reports")
        report_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"discovery_report_{timestamp}.md"
        filepath = report_path / filename
        
        report = context.get("report") or "No report generated."
        
        # Add header
        full_report = f"""# Discovery Report - {timestamp}

**Pipeline Results:**
- New papers crawled: {context.get('crawl_result', {}).get('new_count', 0)}
- Papers scored: {context.get('score_result', {}).get('scored_count', 0)}
- Papers enriched: {context.get('enrich_result', {}).get('resolved_count', 0)}

---

{report}
"""
        
        filepath.write_text(full_report)
        context["report_file"] = str(filepath)
        self.logger.info("Saved report to %s", filepath)
        return context
