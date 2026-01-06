import asyncio
import json
import os
import random
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from flatagents import MachineHooks, get_logger

OPENALEX_BASE_URL = "https://api.openalex.org/works"
USER_AGENT = "research-crawler/0.1 (reverse-citation)"
MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
RATE_LIMIT_PER_SEC = 10
MAX_OR_VALUES = 100

ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+/\d{7})(v\d+)?",
    re.IGNORECASE,
)


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


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def sanitize_search_query(value: str) -> str:
    """Remove characters that cause OpenAlex API 400 errors."""
    # Remove pipes, brackets, and other problematic characters
    sanitized = re.sub(r"[|{}\[\]<>]", " ", value)
    # Collapse multiple spaces
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def normalize_doi(value: str) -> str:
    text = value.strip()
    if text.startswith("https://doi.org/"):
        text = text.replace("https://doi.org/", "", 1)
    return text.lower()


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def is_probable_doi(value: str) -> bool:
    return value.startswith("10.") and "/" in value


def doi_to_url(value: str) -> str:
    text = normalize_doi(value)
    return f"https://doi.org/{text}"


def chunked(values: List[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


def extract_arxiv_id_from_url(url: str) -> Optional[str]:
    match = ARXIV_ID_RE.search(url)
    if not match:
        return None
    return match.group(2)


def extract_arxiv_id(work: Dict[str, Any]) -> Optional[str]:
    locations = []
    primary = work.get("primary_location")
    if primary:
        locations.append(primary)
    best = work.get("best_oa_location")
    if best:
        locations.append(best)
    locations.extend(work.get("locations") or [])
    for location in locations:
        for key in ("landing_page_url", "pdf_url"):
            url = location.get(key)
            if not url:
                continue
            arxiv_id = extract_arxiv_id_from_url(url)
            if arxiv_id:
                return arxiv_id
    return None


class RateLimiter:
    def __init__(self, rate_per_sec: float) -> None:
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._next_time = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next_time > now:
                await asyncio.sleep(self._next_time - now)
            self._next_time = max(self._next_time, now) + self._interval


def run_async(coro: asyncio.Future) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Dict[str, Any] = {}
    exception: List[BaseException] = []

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as e:
            exception.append(e)

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    
    if exception:
        raise exception[0]
    return result.get("value")


class CitationHooks(MachineHooks):
    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    def on_action(self, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "init_db": self._init_db,
            "select_targets": self._select_targets,
            "process_papers": self._process_papers,
            "resolve_targets": self._resolve_targets,
            "enrich_authors": self._enrich_authors,
            "fetch_citations": self._fetch_citations,
            "upsert_results": self._upsert_results,
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
        conn.commit()
        conn.close()

        context["started_at"] = utc_now_iso()
        return context

    def _select_targets(self, context: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(context.get("limit") or 0)
        if limit <= 0:
            context["targets"] = []
            context["target_count"] = 0
            return context

        db_path = Path(context["db_path"])
        since = normalize_date(context.get("since"))
        until = normalize_date(context.get("until"), is_end=True)
        cooldown_days = int(context.get("cooldown_days") or 0)
        provider = context.get("provider") or "openalex"

        self.logger.info(
            "Selecting targets from %s (limit=%d, since=%s, until=%s, cooldown_days=%d)",
            db_path,
            limit,
            since,
            until,
            cooldown_days,
        )

        where_clauses = ["p.llm_relevant = 1"]
        params: List[Any] = [provider]

        if since:
            where_clauses.append("COALESCE(p.updated_at, p.published_at) >= ?")
            params.append(since)
        if until:
            where_clauses.append("COALESCE(p.updated_at, p.published_at) <= ?")
            params.append(until)

        min_score = context.get("min_score")
        score_join = ""
        min_score_str = str(min_score).strip().lower() if min_score is not None else ""
        if min_score_str and min_score_str != "none":
            score_join = "INNER JOIN paper_relevance pr_score ON pr_score.paper_id = p.id"
            where_clauses.append("pr_score.fmr_score >= ?")
            params.append(float(min_score))

        # Filter for papers without author enrichment
        no_authors_only = parse_bool(context.get("no_authors_only"))
        if no_authors_only:
            where_clauses.append("NOT EXISTS (SELECT 1 FROM paper_authors pa WHERE pa.paper_id = p.id)")

        cutoff = None
        having_clause = "1=1"
        if cooldown_days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown_days)).isoformat()
            having_clause = "last_retrieved_at IS NULL OR last_retrieved_at < ?"

        query = f"""
            SELECT
                p.id,
                p.arxiv_id,
                p.title,
                p.doi,
                COALESCE(p.updated_at, p.published_at) AS updated_at,
                MAX(pc.retrieved_at) AS last_retrieved_at
            FROM papers p
            {score_join}
            LEFT JOIN paper_citations pc
                ON pc.paper_id = p.id AND pc.source = ?
            WHERE {" AND ".join(where_clauses)}
            GROUP BY p.id
            HAVING {having_clause}
            ORDER BY updated_at DESC
            LIMIT ?
        """

        if cutoff:
            params.append(cutoff)
        params.append(limit)

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        rows = conn.execute(query, params).fetchall()
        conn.close()

        targets = [
            {
                "paper_id": row[0],
                "arxiv_id": row[1],
                "title": row[2],
                "doi": row[3],
                "updated_at": row[4],
                "last_retrieved_at": row[5],
            }
            for row in rows
        ]

        context["targets"] = targets
        context["target_count"] = len(targets)
        self.logger.info("Selected %d target papers", len(targets))
        return context

    def _process_papers(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Process papers incrementally with saves after each paper."""
        targets = context.get("targets") or []
        if not targets:
            context["resolved_count"] = 0
            context["authors_enriched"] = 0
            context["edge_count"] = 0
            return context

        mailto = os.getenv("OPENALEX_MAILTO")
        if not mailto:
            raise ValueError("OPENALEX_MAILTO environment variable not set")

        db_path = Path(context["db_path"])
        dry_run = parse_bool(context.get("dry_run"))
        batch_size = min(int(context.get("batch_size") or MAX_OR_VALUES), MAX_OR_VALUES)
        max_pages = max(1, int(context.get("max_pages") or 1))
        use_doi = parse_bool(context.get("use_doi"))
        provider = context.get("provider") or "openalex"

        resolved_count = 0
        authors_enriched = 0
        edge_count = 0
        
        total = len(targets)
        for idx, target in enumerate(targets):
            paper_id = target["paper_id"]
            
            # Log progress every 10 papers or at start/end
            if idx == 0 or (idx + 1) % 10 == 0 or idx == total - 1:
                self.logger.info("Processing paper %d/%d (resolved=%d, authors=%d)", 
                                 idx + 1, total, resolved_count, authors_enriched)
            
            # Resolve paper to OpenAlex
            result = run_async(
                self._resolve_single_paper(
                    target=target,
                    mailto=mailto,
                    use_doi=use_doi,
                )
            )
            
            if not result:
                # Resolution failed - still record the attempt for cooldown
                if not dry_run:
                    self._record_attempt(db_path, paper_id, provider)
                continue
            
            openalex_id = result.get("openalex_id")
            work_data = result.get("work_data")
            
            if not openalex_id:
                # Got response but no match - still record attempt for cooldown
                if not dry_run:
                    self._record_attempt(db_path, paper_id, provider)
                continue
            
            resolved_count += 1
            
            # Extract and fetch authors
            author_ids = []
            if work_data:
                authorships = work_data.get("authorships") or []
                for pos, authorship in enumerate(authorships):
                    author = authorship.get("author") or {}
                    author_id = author.get("id")
                    if author_id:
                        author_ids.append((author_id, pos))
            
            # Fetch missing authors
            new_authors = 0
            if author_ids and not dry_run:
                new_authors = run_async(
                    self._fetch_and_save_authors(
                        author_ids=[aid for aid, _ in author_ids],
                        db_path=db_path,
                        mailto=mailto,
                    )
                )
                authors_enriched += new_authors
            
            # Fetch citations for this paper
            edges = run_async(
                self._fetch_single_paper_citations(
                    paper_id=paper_id,
                    openalex_id=openalex_id,
                    mailto=mailto,
                    batch_size=batch_size,
                    max_pages=max_pages,
                )
            )
            edge_count += len(edges or [])
            
            # Save everything for this paper
            if not dry_run:
                self._save_paper_results(
                    db_path=db_path,
                    paper_id=paper_id,
                    openalex_id=openalex_id,
                    work_data=work_data,
                    author_ids=author_ids,
                    edges=edges or [],
                    provider=provider,
                )
        
        context["resolved_count"] = resolved_count
        context["authors_enriched"] = authors_enriched
        context["edge_count"] = edge_count
        self.logger.info("Processed %d papers: resolved=%d, authors=%d, edges=%d",
                         total, resolved_count, authors_enriched, edge_count)
        return context

    async def _resolve_single_paper(
        self,
        target: Dict[str, Any],
        mailto: str,
        use_doi: bool,
    ) -> Optional[Dict[str, Any]]:
        """Resolve a single paper to OpenAlex ID."""
        headers = {"User-Agent": f"{USER_AGENT} mailto:{mailto}"}
        
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            openalex_id = None
            work_data = None
            
            # Try title search first
            try:
                params = {
                    "search": sanitize_search_query(target.get("title") or ""),
                    "filter": "indexed_in:arxiv",
                    "per-page": 5,
                    "mailto": mailto,
                }
                response = await client.get(OPENALEX_BASE_URL, params=params)
                if response.status_code == 200:
                    data = response.json()
                    for work in data.get("results", []):
                        candidate_title = work.get("title") or work.get("display_name") or ""
                        if normalize_title(candidate_title) == normalize_title(target["title"]):
                            openalex_id = work.get("id") or work.get("ids", {}).get("openalex")
                            work_data = work
                            break
            except Exception:
                pass
            
            # Try arxiv_id search if title didn't match
            if not openalex_id and target.get("arxiv_id"):
                try:
                    params = {
                        "search": target["arxiv_id"],
                        "filter": "indexed_in:arxiv",
                        "per-page": 5,
                        "mailto": mailto,
                    }
                    response = await client.get(OPENALEX_BASE_URL, params=params)
                    if response.status_code == 200:
                        data = response.json()
                        for work in data.get("results", []):
                            candidate_id = extract_arxiv_id(work)
                            if candidate_id == target["arxiv_id"]:
                                openalex_id = work.get("id") or work.get("ids", {}).get("openalex")
                                work_data = work
                                break
                except Exception:
                    pass
            
            if openalex_id:
                return {"openalex_id": openalex_id, "work_data": work_data}
            return None

    async def _fetch_and_save_authors(
        self,
        author_ids: List[str],
        db_path: Path,
        mailto: str,
    ) -> int:
        """Fetch missing authors and save them. Returns count of new authors."""
        # Check which are already cached
        conn = sqlite3.connect(db_path)
        placeholders = ",".join("?" * len(author_ids))
        rows = conn.execute(
            f"SELECT openalex_id FROM authors WHERE openalex_id IN ({placeholders})",
            author_ids,
        ).fetchall()
        cached = {row[0] for row in rows}
        conn.close()
        
        missing = [aid for aid in author_ids if aid not in cached]
        if not missing:
            return 0
        
        # Fetch from OpenAlex
        headers = {"User-Agent": f"{USER_AGENT} mailto:{mailto}"}
        
        def short_id(full_id: str) -> str:
            return full_id.replace("https://openalex.org/", "")
        
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            id_filter = "|".join(short_id(aid) for aid in missing[:50])  # Max 50 at a time
            params = {
                "filter": f"ids.openalex:{id_filter}",
                "per-page": 50,
                "mailto": mailto,
            }
            try:
                response = await client.get("https://api.openalex.org/authors", params=params)
                if response.status_code != 200:
                    return 0
                data = response.json()
                
                # Save to DB
                conn = sqlite3.connect(db_path)
                new_count = 0
                for author in data.get("results", []):
                    stats = author.get("summary_stats") or {}
                    last_inst = (author.get("last_known_institutions") or [{}])[0] if author.get("last_known_institutions") else {}
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO authors (
                            openalex_id, display_name, orcid, h_index, i10_index,
                            cited_by_count, works_count, affiliation_name, affiliation_ror,
                            retrieved_at, raw_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            author.get("id"),
                            author.get("display_name"),
                            author.get("orcid"),
                            stats.get("h_index"),
                            stats.get("i10_index"),
                            author.get("cited_by_count"),
                            author.get("works_count"),
                            last_inst.get("display_name"),
                            last_inst.get("ror"),
                            utc_now_iso(),
                            json.dumps(author, ensure_ascii=True),
                        ),
                    )
                    new_count += 1
                conn.commit()
                conn.close()
                return new_count
            except Exception:
                return 0

    async def _fetch_single_paper_citations(
        self,
        paper_id: int,
        openalex_id: str,
        mailto: str,
        batch_size: int,
        max_pages: int,
    ) -> List[Dict[str, Any]]:
        """Fetch citations for a single paper."""
        headers = {"User-Agent": f"{USER_AGENT} mailto:{mailto}"}
        edges: List[Dict[str, Any]] = []
        
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            cursor = "*"
            pages = 0
            while cursor and pages < max_pages:
                params = {
                    "filter": f"referenced_works:{openalex_id}",
                    "per-page": batch_size,
                    "cursor": cursor,
                    "mailto": mailto,
                }
                try:
                    response = await client.get(OPENALEX_BASE_URL, params=params)
                    if response.status_code != 200:
                        break
                    data = response.json()
                    retrieved_at = utc_now_iso()
                    
                    for work in data.get("results", []):
                        arxiv_id = extract_arxiv_id(work)
                        if arxiv_id:
                            edges.append({
                                "source_paper_id": paper_id,
                                "citing_arxiv_id": arxiv_id,
                                "source": "openalex",
                                "retrieved_at": retrieved_at,
                            })
                    
                    cursor = data.get("meta", {}).get("next_cursor")
                    pages += 1
                except Exception:
                    break
        
        return edges

    def _save_paper_results(
        self,
        db_path: Path,
        paper_id: int,
        openalex_id: str,
        work_data: Optional[Dict[str, Any]],
        author_ids: List[Tuple[str, int]],
        edges: List[Dict[str, Any]],
        provider: str,
    ) -> None:
        """Save all results for a single paper to the database."""
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        
        # Save work to cache
        if work_data:
            doi_value = work_data.get("doi") or work_data.get("ids", {}).get("doi")
            conn.execute(
                """
                INSERT OR REPLACE INTO citation_work_cache (
                    openalex_id, arxiv_id, doi, title, source, retrieved_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    openalex_id,
                    extract_arxiv_id(work_data),
                    doi_value,
                    work_data.get("title"),
                    provider,
                    utc_now_iso(),
                    json.dumps(work_data, ensure_ascii=True),
                ),
            )
            
            # Save citation count
            cited_by = work_data.get("cited_by_count") or 0
            conn.execute(
                """
                INSERT INTO paper_citations (paper_id, source, cited_by_count, retrieved_at, raw_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, source) DO UPDATE SET
                    cited_by_count = excluded.cited_by_count,
                    retrieved_at = excluded.retrieved_at
                """,
                (paper_id, provider, cited_by, utc_now_iso(), json.dumps({"count": cited_by})),
            )
        
        # Link paper to authors (only existing ones)
        if author_ids:
            existing = set()
            placeholders = ",".join("?" * len(author_ids))
            rows = conn.execute(
                f"SELECT openalex_id FROM authors WHERE openalex_id IN ({placeholders})",
                [aid for aid, _ in author_ids],
            ).fetchall()
            existing = {row[0] for row in rows}
            
            for author_id, position in author_ids:
                if author_id in existing:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO paper_authors (paper_id, author_openalex_id, author_position)
                        VALUES (?, ?, ?)
                        """,
                        (paper_id, author_id, position),
                    )
        
        # Save citation edges
        for edge in edges:
            arxiv_id = edge.get("citing_arxiv_id")
            if not arxiv_id:
                continue
            # Look up citing paper ID
            row = conn.execute(
                "SELECT id FROM papers WHERE arxiv_id = ?",
                (arxiv_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    INSERT INTO citations (source_paper_id, citing_paper_id, source, retrieved_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_paper_id, citing_paper_id, source)
                    DO UPDATE SET retrieved_at = excluded.retrieved_at
                    """,
                    (paper_id, row[0], edge["source"], edge["retrieved_at"]),
                )
        
        conn.commit()
        conn.close()

    def _record_attempt(self, db_path: Path, paper_id: int, provider: str) -> None:
        """Record that we attempted to resolve a paper, even if it failed.
        
        This ensures cooldown_days applies to failed papers too.
        """
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO paper_citations (paper_id, source, cited_by_count, retrieved_at, raw_json)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(paper_id, source) DO UPDATE SET
                retrieved_at = excluded.retrieved_at
            """,
            (paper_id, provider, utc_now_iso(), json.dumps({"status": "not_found"})),
        )
        conn.commit()
        conn.close()


    def _resolve_targets(self, context: Dict[str, Any]) -> Dict[str, Any]:
        targets = context.get("targets") or []
        if not targets:
            context["resolved_targets"] = []
            context["resolved_count"] = 0
            return context

        mailto = os.getenv("OPENALEX_MAILTO")
        if not mailto:
            raise ValueError("OPENALEX_MAILTO environment variable not set")

        batch_size = min(int(context.get("batch_size") or MAX_OR_VALUES), MAX_OR_VALUES)
        concurrency = max(1, int(context.get("concurrency") or 1))
        use_doi = parse_bool(context.get("use_doi"))

        resolved, cache_entries = run_async(
            self._resolve_targets_async(
                targets=targets,
                batch_size=batch_size,
                concurrency=concurrency,
                mailto=mailto,
                use_doi=use_doi,
            )
        )

        context["resolved_targets"] = resolved
        context["resolved_count"] = len(resolved)
        context["work_cache"] = (context.get("work_cache") or []) + cache_entries
        self.logger.info("Resolved %d targets to OpenAlex IDs", len(resolved))
        return context

    def _enrich_authors(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch and cache author data from OpenAlex."""
        resolved_targets = context.get("resolved_targets") or []
        work_cache = context.get("work_cache") or []
        
        if not resolved_targets:
            context["authors_enriched"] = 0
            return context

        mailto = os.getenv("OPENALEX_MAILTO")
        if not mailto:
            raise ValueError("OPENALEX_MAILTO environment variable not set")

        db_path = Path(context["db_path"])
        dry_run = parse_bool(context.get("dry_run"))
        batch_size = min(int(context.get("batch_size") or MAX_OR_VALUES), MAX_OR_VALUES)
        concurrency = max(1, int(context.get("concurrency") or 1))

        # Extract author IDs from work cache (raw_json has full author data)
        author_ids_by_paper: Dict[int, List[Tuple[str, int]]] = {}  # paper_id -> [(author_id, position)]
        all_author_ids: set = set()
        
        for target in resolved_targets:
            paper_id = target["paper_id"]
            openalex_id = target.get("openalex_id")
            if not openalex_id:
                continue
            
            # Find the cached work data
            for entry in work_cache:
                if entry.get("openalex_id") == openalex_id:
                    raw = entry.get("raw_json")
                    if raw:
                        work = json.loads(raw)
                        authorships = work.get("authorships") or []
                        author_list = []
                        for pos, authorship in enumerate(authorships):
                            author = authorship.get("author") or {}
                            author_id = author.get("id")
                            if author_id:
                                author_list.append((author_id, pos))
                                all_author_ids.add(author_id)
                        author_ids_by_paper[paper_id] = author_list
                    break

        if not all_author_ids:
            self.logger.info("No author IDs found in resolved works")
            context["authors_enriched"] = 0
            return context

        # Check which authors are already cached
        cached_author_ids: set = set()
        if not dry_run:
            conn = sqlite3.connect(db_path)
            placeholders = ",".join("?" * len(all_author_ids))
            rows = conn.execute(
                f"SELECT openalex_id FROM authors WHERE openalex_id IN ({placeholders})",
                list(all_author_ids),
            ).fetchall()
            cached_author_ids = {row[0] for row in rows}
            conn.close()

        missing_author_ids = list(all_author_ids - cached_author_ids)
        self.logger.info(
            "Found %d unique authors, %d already cached, %d to fetch",
            len(all_author_ids),
            len(cached_author_ids),
            len(missing_author_ids),
        )

        # Fetch missing authors from OpenAlex
        if missing_author_ids:
            fetched_authors = run_async(
                self._fetch_authors_async(
                    author_ids=missing_author_ids,
                    batch_size=batch_size,
                    concurrency=concurrency,
                    mailto=mailto,
                )
            )
            context["fetched_authors"] = fetched_authors
        else:
            context["fetched_authors"] = []

        context["author_ids_by_paper"] = author_ids_by_paper
        context["authors_enriched"] = len(missing_author_ids)
        return context

    async def _fetch_authors_async(
        self,
        author_ids: List[str],
        batch_size: int,
        concurrency: int,
        mailto: str,
    ) -> List[Dict[str, Any]]:
        """Fetch author data from OpenAlex in batches."""
        headers = {"User-Agent": f"{USER_AGENT} mailto:{mailto}"}
        rate_limiter = RateLimiter(RATE_LIMIT_PER_SEC)
        authors: List[Dict[str, Any]] = []
        
        # OpenAlex uses short IDs like A5103024730
        def short_id(full_id: str) -> str:
            return full_id.replace("https://openalex.org/", "")

        semaphore = asyncio.Semaphore(concurrency)
        
        async with httpx.AsyncClient(headers=headers) as client:
            for batch in chunked(author_ids, batch_size):
                async with semaphore:
                    # Use filter with OR for batch lookup
                    id_filter = "|".join(short_id(aid) for aid in batch)
                    params = {
                        "filter": f"ids.openalex:{id_filter}",
                        "per-page": len(batch),
                        "mailto": mailto,
                    }
                    await rate_limiter.wait()
                    try:
                        response = await client.get(
                            "https://api.openalex.org/authors",
                            params=params,
                            timeout=30.0,
                        )
                        response.raise_for_status()
                        data = response.json()
                        
                        for author in data.get("results", []):
                            stats = author.get("summary_stats") or {}
                            last_inst = (author.get("last_known_institutions") or [{}])[0] if author.get("last_known_institutions") else {}
                            authors.append({
                                "openalex_id": author.get("id"),
                                "display_name": author.get("display_name"),
                                "orcid": author.get("orcid"),
                                "h_index": stats.get("h_index"),
                                "i10_index": stats.get("i10_index"),
                                "cited_by_count": author.get("cited_by_count"),
                                "works_count": author.get("works_count"),
                                "affiliation_name": last_inst.get("display_name"),
                                "affiliation_ror": last_inst.get("ror"),
                                "retrieved_at": utc_now_iso(),
                                "raw_json": json.dumps(author, ensure_ascii=True),
                            })
                    except Exception as e:
                        self.logger.warning("Failed to fetch author batch: %s", e)
        
        self.logger.info("Fetched %d authors from OpenAlex", len(authors))
        return authors

    def _fetch_citations(self, context: Dict[str, Any]) -> Dict[str, Any]:
        targets = context.get("resolved_targets") or []
        if not targets:
            context["edges"] = []
            context["edge_count"] = 0
            context["citation_summaries"] = []
            return context

        mailto = os.getenv("OPENALEX_MAILTO")
        if not mailto:
            raise ValueError("OPENALEX_MAILTO environment variable not set")

        batch_size = min(int(context.get("batch_size") or MAX_OR_VALUES), MAX_OR_VALUES)
        max_pages = max(1, int(context.get("max_pages") or 1))
        concurrency = max(1, int(context.get("concurrency") or 1))

        edges, cache_entries, summaries = run_async(
            self._fetch_citations_async(
                targets=targets,
                batch_size=batch_size,
                max_pages=max_pages,
                concurrency=concurrency,
                mailto=mailto,
            )
        )

        context["edges"] = edges
        context["edge_count"] = len(edges)
        context["work_cache"] = (context.get("work_cache") or []) + cache_entries
        context["citation_summaries"] = list(summaries.values())
        self.logger.info("Collected %d citation edges", len(edges))
        return context

    def _upsert_results(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if parse_bool(context.get("dry_run")):
            self.logger.info("Dry run: skipping database writes")
            return context

        edges = context.get("edges") or []
        cache_entries = context.get("work_cache") or []
        summaries = context.get("citation_summaries") or []
        if not edges and not cache_entries and not summaries:
            return context

        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        paper_id_cache: Dict[str, Optional[int]] = {}

        def resolve_paper_id(arxiv_id: str) -> Optional[int]:
            if arxiv_id in paper_id_cache:
                return paper_id_cache[arxiv_id]
            row = conn.execute(
                "SELECT id FROM papers WHERE arxiv_id = ?",
                (arxiv_id,),
            ).fetchone()
            paper_id_cache[arxiv_id] = row[0] if row else None
            return paper_id_cache[arxiv_id]

        for edge in edges:
            arxiv_id = edge.get("citing_arxiv_id")
            if not arxiv_id:
                continue
            citing_id = resolve_paper_id(arxiv_id)
            if citing_id is None:
                continue
            conn.execute(
                """
                INSERT INTO citations (
                    source_paper_id, citing_paper_id, source, retrieved_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_paper_id, citing_paper_id, source)
                DO UPDATE SET retrieved_at = excluded.retrieved_at
                """,
                (
                    edge["source_paper_id"],
                    citing_id,
                    edge["source"],
                    edge["retrieved_at"],
                ),
            )

        for entry in cache_entries:
            conn.execute(
                """
                INSERT OR REPLACE INTO citation_work_cache (
                    openalex_id, arxiv_id, doi, title, source, retrieved_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("openalex_id"),
                    entry.get("arxiv_id"),
                    entry.get("doi"),
                    entry.get("title"),
                    entry.get("source"),
                    entry.get("retrieved_at"),
                    entry.get("raw_json"),
                ),
            )

        for summary in summaries:
            conn.execute(
                """
                INSERT INTO paper_citations (
                    paper_id, source, cited_by_count, retrieved_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, source)
                DO UPDATE SET
                    cited_by_count = excluded.cited_by_count,
                    retrieved_at = excluded.retrieved_at,
                    raw_json = excluded.raw_json
                """,
                (
                    summary["paper_id"],
                    summary["source"],
                    summary["cited_by_count"],
                    summary["retrieved_at"],
                    summary.get("raw_json"),
                ),
            )

        # Save fetched authors
        fetched_authors = context.get("fetched_authors") or []
        for author in fetched_authors:
            conn.execute(
                """
                INSERT OR REPLACE INTO authors (
                    openalex_id, display_name, orcid, h_index, i10_index,
                    cited_by_count, works_count, affiliation_name, affiliation_ror,
                    retrieved_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    author["openalex_id"],
                    author["display_name"],
                    author.get("orcid"),
                    author.get("h_index"),
                    author.get("i10_index"),
                    author.get("cited_by_count"),
                    author.get("works_count"),
                    author.get("affiliation_name"),
                    author.get("affiliation_ror"),
                    author["retrieved_at"],
                    author.get("raw_json"),
                ),
            )

        # Link papers to authors (only for authors that exist in the table)
        author_ids_by_paper = context.get("author_ids_by_paper") or {}
        # Get set of author IDs that are now in the database
        all_author_ids = set()
        for author_list in author_ids_by_paper.values():
            for author_id, _ in author_list:
                all_author_ids.add(author_id)
        
        existing_authors = set()
        if all_author_ids:
            placeholders = ",".join("?" * len(all_author_ids))
            rows = conn.execute(
                f"SELECT openalex_id FROM authors WHERE openalex_id IN ({placeholders})",
                list(all_author_ids),
            ).fetchall()
            existing_authors = {row[0] for row in rows}
        
        for paper_id, author_list in author_ids_by_paper.items():
            for author_id, position in author_list:
                if author_id in existing_authors:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO paper_authors (
                            paper_id, author_openalex_id, author_position
                        )
                        VALUES (?, ?, ?)
                        """,
                        (paper_id, author_id, position),
                    )

        conn.commit()
        conn.close()
        return context

    def _record_run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if parse_bool(context.get("dry_run")):
            return context

        db_path = Path(context["db_path"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        finished_at = utc_now_iso()
        status = "ok" if not context.get("error") else "error"

        cursor = conn.execute(
            """
            INSERT INTO citation_runs (
                provider, started_at, finished_at, status,
                since, until, target_count, resolved_count, edge_count, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.get("provider") or "openalex",
                context.get("started_at"),
                finished_at,
                status,
                context.get("since"),
                context.get("until"),
                context.get("target_count") or 0,
                context.get("resolved_count") or 0,
                context.get("edge_count") or 0,
                context.get("error"),
            ),
        )

        conn.commit()
        conn.close()

        context["run_id"] = cursor.lastrowid
        return context

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        params: Dict[str, Any],
        rate_limiter: RateLimiter,
    ) -> Dict[str, Any]:
        for attempt in range(MAX_RETRIES):
            await rate_limiter.wait()
            try:
                response = await client.get(
                    OPENALEX_BASE_URL,
                    params=params,
                    timeout=30.0,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        "Retryable error",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, httpx.RequestError) as exc:
                if attempt >= MAX_RETRIES - 1:
                    raise exc
                backoff = min(BASE_BACKOFF_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)
                jitter = random.uniform(0, backoff * 0.1)
                await asyncio.sleep(backoff + jitter)
        return {}

    async def _resolve_targets_async(
        self,
        targets: List[Dict[str, Any]],
        batch_size: int,
        concurrency: int,
        mailto: str,
        use_doi: bool,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        headers = {"User-Agent": f"{USER_AGENT} mailto:{mailto}"}
        rate_limiter = RateLimiter(RATE_LIMIT_PER_SEC)
        resolved: List[Dict[str, Any]] = []
        cache_entries: List[Dict[str, Any]] = []

        doi_map: Dict[str, str] = {}
        if use_doi:
            doi_map, doi_cache = await self._resolve_by_doi(
                targets=targets,
                batch_size=batch_size,
                mailto=mailto,
                headers=headers,
                rate_limiter=rate_limiter,
            )
            cache_entries.extend(doi_cache)

        semaphore = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(headers=headers) as client:
            async def resolve_one(target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                async with semaphore:
                    openalex_id = None
                    match_type = None
                    doi_value = target.get("doi")
                    if use_doi and doi_value:
                        doi_key = normalize_doi(doi_value)
                        openalex_id = doi_map.get(doi_key)
                        if openalex_id:
                            match_type = "doi"

                    if not openalex_id:
                        try:
                            params = {
                                "search": sanitize_search_query(target.get("title") or ""),
                                "filter": "indexed_in:arxiv",
                                "per-page": 5,
                                "mailto": mailto,
                            }
                            data = await self._request_json(client, params, rate_limiter)
                            for work in data.get("results", []):
                                candidate_title = work.get("title") or work.get("display_name") or ""
                                if normalize_title(candidate_title) == normalize_title(target["title"]):
                                    openalex_id = work.get("id") or work.get("ids", {}).get("openalex")
                                    match_type = "title"
                                    entry = self._build_cache_entry(work, source="openalex")
                                    if entry:
                                        cache_entries.append(entry)
                                    break
                        except httpx.HTTPStatusError as e:
                            if e.response.status_code == 400:
                                pass  # Skip papers with problematic titles
                            else:
                                raise

                    if not openalex_id and target.get("arxiv_id"):
                        params = {
                            "search": target["arxiv_id"],
                            "filter": "indexed_in:arxiv",
                            "per-page": 5,
                            "mailto": mailto,
                        }
                        data = await self._request_json(client, params, rate_limiter)
                        for work in data.get("results", []):
                            candidate_id = extract_arxiv_id(work)
                            if candidate_id == target["arxiv_id"]:
                                openalex_id = work.get("id") or work.get("ids", {}).get("openalex")
                                match_type = "arxiv_id"
                                entry = self._build_cache_entry(work, source="openalex")
                                if entry:
                                    cache_entries.append(entry)
                                break

                    if not openalex_id:
                        return None

                    resolved_target = dict(target)
                    resolved_target["openalex_id"] = openalex_id
                    resolved_target["match_type"] = match_type
                    return resolved_target

            tasks = [resolve_one(target) for target in targets]
            results = await asyncio.gather(*tasks)
        for item in results:
            if item:
                resolved.append(item)

        return resolved, cache_entries

    async def _resolve_by_doi(
        self,
        targets: List[Dict[str, Any]],
        batch_size: int,
        mailto: str,
        headers: Dict[str, str],
        rate_limiter: RateLimiter,
    ) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
        doi_values = [
            normalize_doi(target["doi"])
            for target in targets
            if target.get("doi")
        ]
        if not doi_values:
            return {}, []

        valid_dois = [doi for doi in doi_values if is_probable_doi(doi)]
        if not valid_dois:
            return {}, []

        doi_map: Dict[str, str] = {}
        cache_entries: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(headers=headers) as client:
            for batch in chunked(valid_dois, batch_size):
                doi_filter = "|".join(doi_to_url(doi) for doi in batch)
                params = {
                    "filter": f"doi:{doi_filter}",
                    "per-page": len(batch),
                    "mailto": mailto,
                }
                data = await self._request_json(client, params, rate_limiter)
                for work in data.get("results", []):
                    doi = work.get("doi") or work.get("ids", {}).get("doi")
                    if not doi:
                        continue
                    key = normalize_doi(doi)
                    openalex_id = work.get("id") or work.get("ids", {}).get("openalex")
                    if openalex_id:
                        doi_map[key] = openalex_id
                        entry = self._build_cache_entry(work, source="openalex")
                        if entry:
                            cache_entries.append(entry)
        return doi_map, cache_entries

    async def _fetch_citations_async(
        self,
        targets: List[Dict[str, Any]],
        batch_size: int,
        max_pages: int,
        concurrency: int,
        mailto: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
        headers = {"User-Agent": f"{USER_AGENT} mailto:{mailto}"}
        rate_limiter = RateLimiter(RATE_LIMIT_PER_SEC)
        edges: List[Dict[str, Any]] = []
        cache_entries: Dict[str, Dict[str, Any]] = {}
        summaries: Dict[int, Dict[str, Any]] = {}

        semaphore = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(headers=headers) as client:
            async def fetch_for_target(target: Dict[str, Any]) -> None:
                async with semaphore:
                    openalex_id = target.get("openalex_id")
                    if not openalex_id:
                        return
                    cursor = "*"
                    pages = 0
                    while cursor and pages < max_pages:
                        params = {
                            "filter": f"referenced_works:{openalex_id}",
                            "per-page": batch_size,
                            "cursor": cursor,
                            "mailto": mailto,
                        }
                        data = await self._request_json(client, params, rate_limiter)
                        retrieved_at = utc_now_iso()
                        if target["paper_id"] not in summaries:
                            count = data.get("meta", {}).get("count", 0)
                            summaries[target["paper_id"]] = {
                                "paper_id": target["paper_id"],
                                "source": "openalex",
                                "cited_by_count": count,
                                "retrieved_at": retrieved_at,
                                "raw_json": json.dumps(
                                    {"count": count},
                                    ensure_ascii=True,
                                ),
                            }
                        for work in data.get("results", []):
                            arxiv_id = extract_arxiv_id(work)
                            if arxiv_id:
                                edges.append(
                                    {
                                        "source_paper_id": target["paper_id"],
                                        "citing_arxiv_id": arxiv_id,
                                        "source": "openalex",
                                        "retrieved_at": retrieved_at,
                                    }
                                )
                            entry = self._build_cache_entry(work, source="openalex")
                            if entry.get("openalex_id"):
                                cache_entries[entry["openalex_id"]] = entry

                        cursor = data.get("meta", {}).get("next_cursor")
                        pages += 1

            tasks = [fetch_for_target(target) for target in targets]
            await asyncio.gather(*tasks)

        return edges, list(cache_entries.values()), summaries

    def _build_cache_entry(self, work: Dict[str, Any], source: str) -> Dict[str, Any]:
        openalex_id = work.get("id") or work.get("ids", {}).get("openalex")
        if not openalex_id:
            return {}
        doi_value = work.get("doi") or work.get("ids", {}).get("doi")
        title = work.get("title") or work.get("display_name")
        return {
            "openalex_id": openalex_id,
            "arxiv_id": extract_arxiv_id(work),
            "doi": doi_value,
            "title": title,
            "source": source,
            "retrieved_at": utc_now_iso(),
            "raw_json": json.dumps(work, ensure_ascii=True),
        }
