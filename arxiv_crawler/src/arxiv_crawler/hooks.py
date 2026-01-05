import json
import random
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from flatagents import MachineHooks, get_logger

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
USER_AGENT = "research-crawler/0.1"
RETRY_BACKOFFS = [1, 3, 30, 1, 3, 60, 1, 3, 120]  # Dwell pattern for unstable APIs
THROTTLE_RANGE_SECONDS = (0.5, 1.5)
ARXIV_PAGE_SIZE = 100
LLM_RELEVANCE_CATEGORIES = {
    "cs.CL",
    "cs.AI",
    "cs.LG",
    "stat.ML",
    "cs.IR",
    "cs.RO",
    "cs.SE",
    "cs.HC",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def parse_categories(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text.split(",")
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [str(parsed).strip()]
    return [str(value).strip()]


def parse_arxiv_id(raw_id: str) -> Dict[str, Any]:
    base_id = raw_id
    version = 1
    if "v" in raw_id:
        prefix, maybe_version = raw_id.rsplit("v", 1)
        if maybe_version.isdigit():
            base_id = prefix
            version = int(maybe_version)
    return {"arxiv_id": base_id, "version": version}


def format_arxiv_date(value: Optional[datetime]) -> str:
    if not value:
        return "*"
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def build_search_query(
    categories: List[str],
    since_dt: Optional[datetime],
    until_dt: Optional[datetime],
) -> str:
    """Build arXiv API search query.

    Note: We intentionally do NOT include lastUpdatedDate range in the query
    because arXiv's API returns 500 errors with date range syntax. Instead,
    date filtering is handled client-side in _fetch_arxiv using since_dt/until_dt.
    """
    cat_query = " OR ".join(f"cat:{cat}" for cat in categories)
    base_query = f"({cat_query})" if cat_query else "all:*"
    # Date filtering is handled client-side in _fetch_arxiv, not here
    return base_query


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_feed(xml_text: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{ATOM_NS}id") or "").strip()
        raw_id = entry_id.rsplit("/", 1)[-1]
        id_info = parse_arxiv_id(raw_id)

        title = " ".join((entry.findtext(f"{ATOM_NS}title") or "").split())
        summary = " ".join((entry.findtext(f"{ATOM_NS}summary") or "").split())
        published = entry.findtext(f"{ATOM_NS}published")
        updated = entry.findtext(f"{ATOM_NS}updated")

        authors = [
            (author.findtext(f"{ATOM_NS}name") or "").strip()
            for author in entry.findall(f"{ATOM_NS}author")
        ]

        categories = [
            node.attrib.get("term")
            for node in entry.findall(f"{ATOM_NS}category")
            if node.attrib.get("term")
        ]

        primary_category = None
        primary_node = entry.find(f"{ARXIV_NS}primary_category")
        if primary_node is not None:
            primary_category = primary_node.attrib.get("term")

        pdf_url = None
        for link in entry.findall(f"{ATOM_NS}link"):
            link_type = link.attrib.get("type", "")
            link_title = link.attrib.get("title", "")
            if link_type == "application/pdf" or link_title == "pdf":
                pdf_url = link.attrib.get("href")
                break

        entries.append(
            {
                "arxiv_id": id_info["arxiv_id"],
                "version": id_info["version"],
                "title": title,
                "abstract": summary,
                "authors": authors,
                "categories": categories,
                "primary_category": primary_category,
                "published_at": published,
                "updated_at": updated,
                "pdf_url": pdf_url,
                "entry_url": entry_id,
                "abstract_url": entry_id,
                "doi": entry.findtext(f"{ARXIV_NS}doi"),
                "journal_ref": entry.findtext(f"{ARXIV_NS}journal_ref"),
                "comment": entry.findtext(f"{ARXIV_NS}comment"),
            }
        )
    return entries


def is_llm_relevant(categories: List[str]) -> bool:
    return any(category in LLM_RELEVANCE_CATEGORIES for category in categories)


class CrawlerHooks(MachineHooks):
    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    def on_action(self, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "init_db": self._init_db,
            "load_state": self._load_state,
            "fetch_arxiv": self._fetch_arxiv,
            "upsert_papers": self._upsert_papers,
            "enqueue_new": self._enqueue_new,
            "record_run": self._record_run,
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
            table="papers",
            column="llm_relevant",
            ddl="llm_relevant INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            conn,
            table="papers",
            column="abstract_url",
            ddl="abstract_url TEXT",
        )
        self._ensure_index(
            conn,
            name="idx_papers_llm_relevant",
            ddl="CREATE INDEX IF NOT EXISTS idx_papers_llm_relevant ON papers(llm_relevant)",
        )

        source_id = self._ensure_source(conn)
        run_id = self._create_run(conn, source_id)

        conn.commit()
        conn.close()

        context["source_id"] = source_id
        context["run_id"] = run_id
        return context

    def _load_state(self, context: Dict[str, Any]) -> Dict[str, Any]:
        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        since = context.get("since")
        if not since:
            row = conn.execute(
                "SELECT value FROM crawler_state WHERE key = ?",
                ("last_updated_at",),
            ).fetchone()
            if row:
                since = row[0]

        categories = parse_categories(context.get("categories")) or list(LLM_RELEVANCE_CATEGORIES)
        since_dt = parse_iso_datetime(since)
        until_dt = parse_iso_datetime(context.get("until"))
        query = build_search_query(categories, since_dt, until_dt)

        conn.close()

        context["since"] = since
        context["query"] = query
        return context

    def _fetch_arxiv(self, context: Dict[str, Any]) -> Dict[str, Any]:
        max_results = int(context.get("max_results") or 0)
        if max_results <= 0:
            context["fetched_entries"] = []
            context["fetched_count"] = 0
            return context

        since_dt = parse_iso_datetime(context.get("since"))
        until_dt = parse_iso_datetime(context.get("until"))
        query = context.get("query")
        if not query:
            categories = parse_categories(context.get("categories")) or list(LLM_RELEVANCE_CATEGORIES)
            query = build_search_query(categories, since_dt, until_dt)
        progress_every = int(context.get("progress_every") or 0)

        collected = []
        latest_updated = None
        start = 0
        done = False
        next_log = progress_every if progress_every > 0 else None

        self.logger.info("Fetching arXiv feed...")
        while not done and len(collected) < max_results:
            batch_size = min(ARXIV_PAGE_SIZE, max_results - len(collected))
            params = {
                "search_query": query,
                "sortBy": "lastUpdatedDate",
                "sortOrder": "descending",
                "start": start,
                "max_results": batch_size,
            }
            throttle_delay = random.uniform(*THROTTLE_RANGE_SECONDS)
            time.sleep(throttle_delay)
            response = self._request_with_retries(params)
            entries = parse_feed(response.text)
            if not entries:
                break

            for entry in entries:
                updated_at = parse_iso_datetime(entry.get("updated_at"))
                if until_dt and updated_at and updated_at > until_dt:
                    continue
                if since_dt and updated_at and updated_at < since_dt:
                    done = True
                    break

                collected.append(entry)
                if updated_at and (latest_updated is None or updated_at > latest_updated):
                    latest_updated = updated_at
                if next_log and len(collected) >= next_log:
                    self.logger.info("Fetched %d entries so far...", len(collected))
                    next_log += progress_every

                if len(collected) >= max_results:
                    done = True
                    break

            start += len(entries)

        context["fetched_entries"] = collected
        context["fetched_count"] = len(collected)
        context["latest_updated"] = (
            latest_updated.isoformat() if latest_updated else context.get("latest_updated")
        )
        return context

    def _upsert_papers(self, context: Dict[str, Any]) -> Dict[str, Any]:
        entries = context.get("fetched_entries") or []
        if not entries:
            context["new_ids"] = []
            context["updated_ids"] = []
            return context

        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        dry_run = parse_bool(context.get("dry_run"))
        now = utc_now_iso()

        new_ids = []
        updated_ids = []
        new_count = 0
        updated_count = 0

        for entry in entries:
            row = conn.execute(
                "SELECT id FROM papers WHERE arxiv_id = ? AND version = ?",
                (entry["arxiv_id"], entry["version"]),
            ).fetchone()

            authors_text = "; ".join([a for a in entry["authors"] if a])
            categories_json = json.dumps(entry["categories"])
            llm_relevant = 1 if is_llm_relevant(entry["categories"]) else 0

            if row:
                paper_id = row[0]
                updated_count += 1
                if not dry_run:
                    updated_ids.append(paper_id)
                    conn.execute(
                        """
                        UPDATE papers
                        SET title = ?, abstract = ?, authors = ?, categories = ?,
                            primary_category = ?, published_at = ?, updated_at = ?,
                            pdf_url = ?, entry_url = ?, abstract_url = ?, doi = ?,
                            journal_ref = ?, comment = ?,
                            llm_relevant = ?,
                            last_seen_at = ?
                        WHERE id = ?
                        """,
                        (
                            entry["title"],
                            entry["abstract"],
                            authors_text,
                            categories_json,
                            entry.get("primary_category"),
                            entry.get("published_at"),
                            entry.get("updated_at"),
                            entry.get("pdf_url"),
                            entry.get("entry_url"),
                            entry.get("abstract_url"),
                            entry.get("doi"),
                            entry.get("journal_ref"),
                            entry.get("comment"),
                            llm_relevant,
                            now,
                            paper_id,
                        ),
                    )
                else:
                    updated_ids.append(entry["arxiv_id"])
                    change = {
                        "action": "update",
                        "paper_id": paper_id,
                        "arxiv_id": entry["arxiv_id"],
                        "version": entry["version"],
                        "title": entry["title"],
                        "abstract": entry["abstract"],
                        "authors": entry["authors"],
                        "categories": entry["categories"],
                        "primary_category": entry.get("primary_category"),
                        "published_at": entry.get("published_at"),
                        "updated_at": entry.get("updated_at"),
                        "pdf_url": entry.get("pdf_url"),
                        "entry_url": entry.get("entry_url"),
                        "abstract_url": entry.get("abstract_url"),
                        "doi": entry.get("doi"),
                        "journal_ref": entry.get("journal_ref"),
                        "comment": entry.get("comment"),
                        "llm_relevant": bool(llm_relevant),
                    }
                    print(json.dumps(change, ensure_ascii=True))
            else:
                new_count += 1
                if dry_run:
                    new_ids.append(entry["arxiv_id"])
                    change = {
                        "action": "insert",
                        "arxiv_id": entry["arxiv_id"],
                        "version": entry["version"],
                        "title": entry["title"],
                        "abstract": entry["abstract"],
                        "authors": entry["authors"],
                        "categories": entry["categories"],
                        "primary_category": entry.get("primary_category"),
                        "published_at": entry.get("published_at"),
                        "updated_at": entry.get("updated_at"),
                        "pdf_url": entry.get("pdf_url"),
                        "entry_url": entry.get("entry_url"),
                        "abstract_url": entry.get("abstract_url"),
                        "doi": entry.get("doi"),
                        "journal_ref": entry.get("journal_ref"),
                        "comment": entry.get("comment"),
                        "llm_relevant": bool(llm_relevant),
                    }
                    print(json.dumps(change, ensure_ascii=True))
                    continue
                cursor = conn.execute(
                    """
                    INSERT INTO papers (
                        arxiv_id, version, title, abstract, authors, categories,
                        primary_category, published_at, updated_at, pdf_url, entry_url,
                        abstract_url, doi, journal_ref, comment, llm_relevant,
                        ingested_at, last_seen_at, source_id,
                        crawl_run_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["arxiv_id"],
                        entry["version"],
                        entry["title"],
                        entry["abstract"],
                        authors_text,
                        categories_json,
                        entry.get("primary_category"),
                        entry.get("published_at"),
                        entry.get("updated_at"),
                        entry.get("pdf_url"),
                        entry.get("entry_url"),
                        entry.get("abstract_url"),
                        entry.get("doi"),
                        entry.get("journal_ref"),
                        entry.get("comment"),
                        llm_relevant,
                        now,
                        now,
                        context["source_id"],
                        context["run_id"],
                    ),
                )
                new_ids.append(cursor.lastrowid)

        if not dry_run:
            conn.commit()
        conn.close()

        context["new_ids"] = new_ids
        context["updated_ids"] = updated_ids
        if dry_run:
            context["new_count"] = new_count
            context["updated_count"] = updated_count
        else:
            context["new_count"] = len(new_ids)
            context["updated_count"] = len(updated_ids)
        return context

    def _enqueue_new(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if parse_bool(context.get("dry_run")):
            return context

        new_ids = context.get("new_ids") or []
        if not new_ids:
            return context

        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        now = utc_now_iso()
        for paper_id in new_ids:
            conn.execute(
                """
                INSERT OR IGNORE INTO paper_queue (
                    paper_id, status, priority, enqueued_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (paper_id, "pending", 0, now),
            )

        conn.commit()
        conn.close()
        return context

    def _record_run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        finished_at = utc_now_iso()
        status = "ok"

        conn.execute(
            """
            UPDATE crawl_runs
            SET finished_at = ?, status = ?, query = ?, categories = ?, since = ?,
                fetched_count = ?, new_count = ?, updated_count = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                context.get("query"),
                json.dumps(parse_categories(context.get("categories"))),
                context.get("since"),
                int(context.get("fetched_count") or 0),
                int(context.get("new_count") or 0),
                int(context.get("updated_count") or 0),
                context["run_id"],
            ),
        )

        self._set_state(conn, "last_run_at", finished_at)
        if context.get("latest_updated"):
            existing = conn.execute(
                "SELECT value FROM crawler_state WHERE key = ?",
                ("last_updated_at",),
            ).fetchone()
            existing_dt = parse_iso_datetime(existing[0]) if existing else None
            candidate_dt = parse_iso_datetime(context["latest_updated"])
            if candidate_dt and (existing_dt is None or candidate_dt > existing_dt):
                self._set_state(conn, "last_updated_at", context["latest_updated"])

        conn.commit()
        conn.close()
        return context

    def _ensure_source(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT id FROM sources WHERE name = ?",
            ("arxiv",),
        ).fetchone()
        if row:
            return row[0]
        cursor = conn.execute(
            "INSERT INTO sources (name, base_url) VALUES (?, ?)",
            ("arxiv", ARXIV_API_URL),
        )
        return cursor.lastrowid

    def _create_run(self, conn: sqlite3.Connection, source_id: int) -> int:
        cursor = conn.execute(
            """
            INSERT INTO crawl_runs (source_id, started_at, status)
            VALUES (?, ?, ?)
            """,
            (source_id, utc_now_iso(), "running"),
        )
        return cursor.lastrowid

    def _set_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO crawler_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _ensure_index(self, conn: sqlite3.Connection, name: str, ddl: str) -> None:
        rows = conn.execute("PRAGMA index_list(papers)").fetchall()
        existing = {row[1] for row in rows}
        if name not in existing:
            conn.execute(ddl)

    def _request_with_retries(self, params: Dict[str, Any]) -> httpx.Response:
        headers = {"User-Agent": USER_AGENT}
        last_error: Optional[Exception] = None
        max_retries = len(RETRY_BACKOFFS)

        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = RETRY_BACKOFFS[attempt - 1]
                jitter = random.uniform(0.0, 0.5)
                sleep_for = backoff + jitter
                self.logger.warning("Retrying in %.1fs (attempt %d/%d)", sleep_for, attempt, max_retries)
                time.sleep(sleep_for)
            try:
                response = httpx.get(
                    ARXIV_API_URL,
                    params=params,
                    timeout=30.0,
                    headers=headers,
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_seconds = float(retry_after) if retry_after else 60.0
                    self.logger.warning("429 received; waiting %.1fs before retry", wait_seconds)
                    time.sleep(wait_seconds)
                    continue
                if response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"Server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("Failed to fetch arXiv feed after retries.")

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
