import argparse
import asyncio
import json
import sqlite3
import textwrap
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from flatagents import FlatAgent, setup_logging, get_logger

setup_logging(level="INFO")
logger = get_logger(__name__)


@dataclass(frozen=True)
class Candidate:
    index: int
    paper_id: int
    arxiv_id: str
    title: str
    abstract: str
    fmr_score: float
    cited_by_count: int
    authors: str
    max_h_index: Optional[int]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_db_path() -> Path:
    return _repo_root() / "arxiv_crawler" / "data" / "arxiv.sqlite"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_query_term(term: str) -> str:
    normalized = re.sub(r"[-_/]+", " ", term.strip())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _load_query_terms(path: Path) -> List[str]:
    terms: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalized = _normalize_query_term(stripped)
        if normalized:
            terms.append(normalized)
    return terms


def _build_query_from_terms(terms: Iterable[str]) -> str:
    pieces: List[str] = []
    for term in terms:
        if not term:
            continue
        escaped = term.replace('"', '""')
        if re.search(r"\s", escaped):
            pieces.append(f'"{escaped}"')
        else:
            pieces.append(escaped)
    return " OR ".join(pieces)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON;")

    paper_cols = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    if "disable_summary" not in paper_cols:
        conn.execute("ALTER TABLE papers ADD COLUMN disable_summary INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_papers_disable_summary ON papers(disable_summary)"
        )

    queue_cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_queue)")}
    if "summary_path" not in queue_cols:
        conn.execute("ALTER TABLE paper_queue ADD COLUMN summary_path TEXT")

    conn.commit()


def _ensure_fts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts
        USING fts5(
            title,
            abstract,
            content='papers',
            content_rowid='id'
        )
        """
    )

    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS papers_fts_ai
        AFTER INSERT ON papers
        BEGIN
            INSERT INTO papers_fts(rowid, title, abstract)
            VALUES (new.id, new.title, new.abstract);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS papers_fts_ad
        AFTER DELETE ON papers
        BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
            VALUES ('delete', old.id, old.title, old.abstract);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS papers_fts_au
        AFTER UPDATE ON papers
        BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
            VALUES ('delete', old.id, old.title, old.abstract);
            INSERT INTO papers_fts(rowid, title, abstract)
            VALUES (new.id, new.title, new.abstract);
        END;
        """
    )
    conn.commit()


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    conn.commit()

def _fts_row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM papers_fts").fetchone()[0]


def _papers_row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM papers").fetchone()[0]


def _fts_vocab_count(conn: sqlite3.Connection) -> Optional[int]:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts_vocab USING fts5vocab('papers_fts', 'row')"
        )
        return conn.execute("SELECT count(*) FROM papers_fts_vocab").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def _debug_fts_state(conn: sqlite3.Connection, query: str) -> None:
    try:
        fts_count = _fts_row_count(conn)
        papers_count = _papers_row_count(conn)
        vocab_count = _fts_vocab_count(conn)
        print("FTS debug:")
        print(f"  papers rows: {papers_count}")
        print(f"  papers_fts rows: {fts_count}")
        print(f"  vocab rows: {vocab_count if vocab_count is not None else 'N/A'}")
        if vocab_count:
            sample = conn.execute(
                "SELECT term, doc, cnt FROM papers_fts_vocab ORDER BY term ASC LIMIT 10"
            ).fetchall()
            if sample:
                print("  sample terms:")
                for term, doc, cnt in sample:
                    print(f"    {term} (doc={doc}, cnt={cnt})")
        try:
            match_count = conn.execute(
                "SELECT count(*) FROM papers_fts WHERE papers_fts MATCH ?",
                (query,),
            ).fetchone()[0]
            print(f"  match '{query}': {match_count}")
        except sqlite3.OperationalError as exc:
            print(f"  match '{query}' error: {exc}")
    except sqlite3.Error as exc:
        print(f"FTS debug failed: {exc}")


def _ensure_fts_ready(conn: sqlite3.Connection, force_rebuild: bool) -> bool:
    if force_rebuild:
        print("Rebuilding FTS index...")
        _rebuild_fts(conn)
        return True

    try:
        fts_count = _fts_row_count(conn)
    except sqlite3.OperationalError as exc:
        raise RuntimeError("FTS5 is not available in this SQLite build.") from exc

    if fts_count:
        vocab_count = _fts_vocab_count(conn)
        if vocab_count is not None and vocab_count == 0:
            papers_count = _papers_row_count(conn)
            if papers_count:
                print("FTS index has no tokens. Rebuilding index (one-time)...")
                _rebuild_fts(conn)
                return True
        return False

    papers_count = _papers_row_count(conn)
    if papers_count:
        print("FTS index is empty. Building index (one-time)...")
        _rebuild_fts(conn)
        return True
    return False


def _order_by_clause(order_by: str) -> str:
    if order_by == "impact":
        return (
            "COALESCE(pr.fmr_score, 0) DESC, "
            "(aa.max_h_index IS NULL) ASC, "
            "aa.max_h_index DESC, "
            "COALESCE(pc.cited_by_count, 0) DESC, "
            "bm25(papers_fts) ASC"
        )
    if order_by == "hybrid":
        return (
            "bm25(papers_fts) ASC, "
            "COALESCE(pr.fmr_score, 0) DESC, "
            "(aa.max_h_index IS NULL) ASC, "
            "aa.max_h_index DESC, "
            "COALESCE(pc.cited_by_count, 0) DESC"
        )
    return "bm25(papers_fts) ASC"


def _count_candidates(
    conn: sqlite3.Connection,
    query: str,
    only_llm_relevant: bool,
) -> int:
    llm_clause = "AND p.llm_relevant = 1" if only_llm_relevant else ""
    query_sql = """
        WITH latest_versions AS (
            SELECT arxiv_id, MAX(version) AS max_version
            FROM papers
            GROUP BY arxiv_id
        )
        SELECT COUNT(*)
        FROM papers_fts
        JOIN papers p ON p.id = papers_fts.rowid
        JOIN latest_versions lv
          ON lv.arxiv_id = p.arxiv_id
         AND lv.max_version = p.version
        WHERE p.disable_summary = 0
          {llm_clause}
          AND papers_fts MATCH ?
          AND NOT EXISTS (
            SELECT 1
            FROM paper_queue pq
            WHERE pq.paper_id = p.id
              AND pq.summary_path IS NOT NULL
              AND pq.summary_path != ''
          )
    """
    row = conn.execute(query_sql.format(llm_clause=llm_clause), (query,)).fetchone()
    return int(row[0] or 0)


def _search_candidates(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    only_llm_relevant: bool,
    order_by: str,
) -> List[Candidate]:
    llm_clause = "AND p.llm_relevant = 1" if only_llm_relevant else ""
    order_clause = _order_by_clause(order_by)
    query_sql = """
        WITH latest_versions AS (
            SELECT arxiv_id, MAX(version) AS max_version
            FROM papers
            GROUP BY arxiv_id
        ),
        author_agg AS (
            SELECT
                pa.paper_id,
                GROUP_CONCAT(DISTINCT a.display_name) AS authors,
                MAX(a.h_index) AS max_h_index
            FROM paper_authors pa
            JOIN authors a ON a.openalex_id = pa.author_openalex_id
            GROUP BY pa.paper_id
        )
        SELECT
            p.id,
            p.arxiv_id,
            p.title,
            p.abstract,
            pr.fmr_score,
            pc.cited_by_count,
            aa.authors,
            aa.max_h_index
        FROM papers_fts
        JOIN papers p ON p.id = papers_fts.rowid
        JOIN latest_versions lv
          ON lv.arxiv_id = p.arxiv_id
         AND lv.max_version = p.version
        LEFT JOIN paper_relevance pr ON pr.paper_id = p.id
        LEFT JOIN paper_citations pc ON pc.paper_id = p.id
        LEFT JOIN author_agg aa ON aa.paper_id = p.id
        WHERE p.disable_summary = 0
          {llm_clause}
          AND papers_fts MATCH ?
          AND NOT EXISTS (
            SELECT 1
            FROM paper_queue pq
            WHERE pq.paper_id = p.id
              AND pq.summary_path IS NOT NULL
              AND pq.summary_path != ''
          )
        ORDER BY {order_clause}
        LIMIT ?
    """
    rows = conn.execute(
        query_sql.format(llm_clause=llm_clause, order_clause=order_clause),
        (query, limit),
    ).fetchall()
    candidates: List[Candidate] = []
    for idx, row in enumerate(rows, start=1):
        candidates.append(
            Candidate(
                index=idx,
                paper_id=row[0],
                arxiv_id=row[1] or "",
                title=row[2] or "Untitled",
                abstract=row[3] or "",
                fmr_score=float(row[4] or 0.0),
                cited_by_count=int(row[5] or 0),
                authors=row[6] or "Unknown",
                max_h_index=row[7],
            )
        )
    return candidates


def _format_candidates(candidates: Iterable[Candidate]) -> str:
    lines: List[str] = []
    for c in candidates:
        abstract = textwrap.shorten(" ".join(c.abstract.split()), width=280, placeholder="...")
        lines.append(f"[{c.index}] {c.title}")
        lines.append(f"  arXiv: {c.arxiv_id} | FMR: {c.fmr_score:.3f} | citations: {c.cited_by_count}")
        if c.max_h_index is not None:
            lines.append(f"  max h-index: {c.max_h_index}")
        lines.append(f"  authors: {c.authors}")
        lines.append(f"  abstract: {abstract}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _batch_id() -> str:
    return _utc_now_iso()


def _infer_default_action(user_input: str) -> str:
    text = user_input.lower()
    if "do them all" in text or "do all" in text or "summarize all" in text or "summarise all" in text:
        return "summarize"
    return "pass"


def _extract_indices_by_context(text: str, phrases: Iterable[str], window: int = 32) -> List[int]:
    lower = text.lower()
    indices: List[int] = []
    for match in re.finditer(r"\b(\d+)\b", lower):
        start = match.start()
        prefix = lower[max(0, start - window):start]
        if any(phrase in prefix for phrase in phrases):
            indices.append(int(match.group(1)))
    return indices


def _extract_json(payload: str) -> Optional[Dict[str, Any]]:
    text = payload.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_action(value: str) -> str:
    val = (value or "").strip().lower()
    if val in {"summarize", "summarise"}:
        return "summarize"
    if val in {"disable_summary", "disable", "do_not_summarize", "do_not_summarise"}:
        return "disable_summary"
    return "pass"


def _default_priorities(candidates: List[Candidate]) -> Dict[int, int]:
    return {c.index: rank for rank, c in enumerate(candidates, start=1)}


def _parse_index_list(value: Any) -> List[int]:
    if not isinstance(value, list):
        return []
    indices: List[int] = []
    for item in value:
        idx: Optional[int] = None
        if isinstance(item, int):
            idx = item
        elif isinstance(item, str) and item.isdigit():
            idx = int(item)
        elif isinstance(item, dict):
            raw_idx = item.get("index")
            if isinstance(raw_idx, int):
                idx = raw_idx
            elif isinstance(raw_idx, str) and raw_idx.isdigit():
                idx = int(raw_idx)
        if idx is not None:
            indices.append(idx)
    return indices


def _normalize_actions_from_groups(
    candidates: List[Candidate],
    grouped_actions: Dict[str, Any],
    default_action: str,
) -> List[Dict[str, Any]]:
    by_index = {c.index: c for c in candidates}
    priorities = _default_priorities(candidates)

    summarize_set = set(_parse_index_list(grouped_actions.get("summarize", [])))
    disable_set = set(
        _parse_index_list(
            grouped_actions.get("disable_summary", grouped_actions.get("disable", []))
        )
    )
    pass_set = set(_parse_index_list(grouped_actions.get("pass", [])))

    resolved: Dict[int, Dict[str, Any]] = {}
    for idx, candidate in by_index.items():
        if idx in disable_set:
            action = "disable_summary"
        elif idx in summarize_set:
            action = "summarize"
        elif idx in pass_set:
            action = "pass"
        else:
            action = "summarize" if default_action == "summarize" else "pass"
        resolved[idx] = {
            "index": idx,
            "arxiv_id": candidate.arxiv_id,
            "title": candidate.title,
            "action": action,
            "priority": priorities[idx],
        }

    ordered = [resolved[idx] for idx in sorted(resolved)]
    return ordered


def _normalize_actions(
    candidates: List[Candidate],
    llm_actions: List[Dict[str, Any]],
    default_action: str,
) -> List[Dict[str, Any]]:
    by_index = {c.index: c for c in candidates}
    priorities = _default_priorities(candidates)
    resolved: Dict[int, Dict[str, Any]] = {}

    for action in llm_actions:
        idx = action.get("index")
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        if idx is None:
            continue
        if idx not in by_index:
            continue
        candidate = by_index[idx]
        normalized_action = _normalize_action(action.get("action", "pass"))
        priority = action.get("priority") or priorities[idx]
        resolved[idx] = {
            "index": idx,
            "arxiv_id": candidate.arxiv_id,
            "title": candidate.title,
            "action": normalized_action,
            "priority": int(priority),
        }

    for idx, candidate in by_index.items():
        if idx in resolved:
            continue
        if default_action == "summarize":
            action = "summarize"
        else:
            action = "pass"
        resolved[idx] = {
            "index": idx,
            "arxiv_id": candidate.arxiv_id,
            "title": candidate.title,
            "action": action,
            "priority": priorities[idx],
        }

    ordered = [resolved[idx] for idx in sorted(resolved)]
    return ordered


def _group_actions_for_output(actions: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    grouped = {
        "summarize": [],
        "disable_summary": [],
        "pass": [],
    }
    for action in actions:
        action_name = action.get("action", "pass")
        idx = action.get("index")
        if isinstance(idx, int) and action_name in grouped:
            grouped[action_name].append(idx)
    for key in grouped:
        grouped[key] = sorted(set(grouped[key]))
    return grouped


async def _llm_format_actions(
    agent: FlatAgent,
    candidates: List[Candidate],
    user_input: str,
    batch_id: str,
    recommendations_json: str,
    max_retries: int = 1,
) -> Dict[str, Any]:
    candidates_payload = [
        {
            "index": c.index,
            "arxiv_id": c.arxiv_id,
            "title": c.title,
            "fmr_score": c.fmr_score,
            "authors": c.authors,
        }
        for c in candidates
    ]
    last_content: Optional[str] = None
    for attempt in range(max_retries + 1):
        response = await agent.call(
            candidates_json=json.dumps(candidates_payload, ensure_ascii=True),
            user_input=user_input,
            batch_id=batch_id,
            recommendations_json=recommendations_json,
        )
        content = None
        if hasattr(response, "output") and response.output:
            content = response.output
        elif hasattr(response, "content") and response.content:
            content = response.content
        else:
            content = response
        if isinstance(content, dict):
            return content
        parsed = _extract_json(str(content))
        if parsed:
            return parsed
        last_content = str(content)
        if attempt < max_retries:
            logger.warning("LLM returned invalid JSON; retrying (%s/%s).", attempt + 1, max_retries)
    raise ValueError("LLM did not return valid JSON.")


def _apply_disable_summary(conn: sqlite3.Connection, candidates_by_index: Dict[int, Candidate], actions: List[Dict[str, Any]]) -> None:
    disable_ids = [
        candidates_by_index[action["index"]].paper_id
        for action in actions
        if action["action"] == "disable_summary"
    ]
    if not disable_ids:
        return
    conn.executemany(
        "UPDATE papers SET disable_summary = 1 WHERE id = ?",
        [(paper_id,) for paper_id in disable_ids],
    )
    conn.commit()


def _ensure_queue_row(conn: sqlite3.Connection, paper_id: int, priority: int) -> None:
    now = _utc_now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO paper_queue (
            paper_id, status, priority, enqueued_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (paper_id, "pending", priority, now),
    )
    conn.execute(
        "UPDATE paper_queue SET worker = ?, priority = ? WHERE paper_id = ?",
        ("summarizer", priority, paper_id),
    )


async def _summarize_actions(
    conn: sqlite3.Connection,
    candidates_by_index: Dict[int, Candidate],
    actions: List[Dict[str, Any]],
) -> None:
    """Queue papers for downstream processing (DB-only)."""
    to_summarize = [a for a in actions if a["action"] == "summarize"]
    if not to_summarize:
        print("No papers to summarize.")
        return

    to_summarize.sort(key=lambda item: item.get("priority", 0))

    print(f"\n📋 Queuing {len(to_summarize)} paper(s) for processing...")
    for action in to_summarize:
        candidate = candidates_by_index[action["index"]]
        priority = int(action.get("priority") or 0)
        _ensure_queue_row(conn, candidate.paper_id, priority)
        conn.execute(
            """
            UPDATE paper_queue
            SET status = 'pending', worker = NULL, started_at = NULL, priority = ?
            WHERE paper_id = ?
            """,
            (priority, candidate.paper_id),
        )
    conn.commit()
    print(f"   ✅ Queued {len(to_summarize)} papers.")


async def run_repl(
    db_path: Path,
    limit: int,
    query: Optional[str],
    query_file: Optional[Path],
    rebuild_fts: bool,
    only_llm_relevant: bool,
    order_by: str,
    auto_summarize: bool,
    show_count: bool,
) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    _ensure_fts(conn)
    rebuilt = _ensure_fts_ready(conn, rebuild_fts)
    if rebuilt:
        fts_count = _fts_row_count(conn)
        print(f"FTS index ready (rows: {fts_count}).")

    query = (query or "").strip()
    if query_file:
        if not query_file.exists():
            raise FileNotFoundError(f"Query file not found: {query_file}")
        terms = _load_query_terms(query_file)
        if not terms:
            raise ValueError(f"No query terms found in {query_file}")
        file_query = _build_query_from_terms(terms)
        if query:
            query = f"({file_query}) AND ({query})"
        else:
            query = file_query
        print(f"Loaded {len(terms)} query term(s) from {query_file}")
    if not query:
        query = input("Search query: ").strip()
    if not query:
        print("No query provided.")
        conn.close()
        return

    try:
        if show_count:
            total_matches = _count_candidates(conn, query, only_llm_relevant)
            print(f"Total matches: {total_matches}")
        candidates = _search_candidates(conn, query, limit, only_llm_relevant, order_by)
    except sqlite3.OperationalError as exc:
        conn.close()
        raise RuntimeError(f"Search failed. Check FTS query syntax: {exc}") from exc

    if not candidates:
        if not rebuilt and _fts_row_count(conn) == 0 and _papers_row_count(conn) > 0:
            print("FTS index empty. Rebuilding and retrying search...")
            _rebuild_fts(conn)
            fts_count = _fts_row_count(conn)
            print(f"FTS index ready (rows: {fts_count}).")
            if show_count:
                total_matches = _count_candidates(conn, query, only_llm_relevant)
                print(f"Total matches: {total_matches}")
            candidates = _search_candidates(conn, query, limit, only_llm_relevant, order_by)
        if not candidates:
            _debug_fts_state(conn, query)
            print("No matching papers found.")
            conn.close()
            return

    print("\n=== Search Results ===\n")
    print(f"Query: {query}\n")
    print(_format_candidates(candidates))

    if auto_summarize:
        batch_id = _batch_id()
        normalized_actions = [
            {
                "index": c.index,
                "arxiv_id": c.arxiv_id,
                "title": c.title,
                "action": "summarize",
                "priority": rank,
            }
            for rank, c in enumerate(candidates, start=1)
        ]
        print(f"\nAuto-summarize enabled: queueing {len(normalized_actions)} paper(s).")
        candidates_by_index = {c.index: c for c in candidates}
        await _summarize_actions(
            conn,
            candidates_by_index,
            normalized_actions,
        )
        conn.close()
        return

    print("\nEnter your instructions. Unmentioned papers default to pass.")
    print("Use phrases like: 'do them all', 'do all except ...', or 'do not summarize ...'.\n")

    user_input = input("Your instructions: ").strip()
    if not user_input:
        user_input = "pass"

    batch_id = _batch_id()
    default_action = _infer_default_action(user_input)

    config_dir = _repo_root() / "research_paper_analysis" / "config"
    agent = FlatAgent(config_file=str(config_dir / "summarizer_humanloop.yml"))

    disable_phrases = [
        "never do",
        "never summarize",
        "never summarise",
        "do not summarize",
        "do not summarise",
        "don't summarize",
        "don't summarise",
        "do not do",
        "don't do",
        "disable summary",
        "disable summarization",
        "do not summarize paper",
        "do not summarize paper",
        "do not summarize",
        "do not summarise",
    ]
    pass_phrases = [
        "skip",
        "pass",
        "leave out",
        "leave",
        "except",
        "do all except",
    ]
    disable_indices = _extract_indices_by_context(user_input, disable_phrases)
    pass_indices = _extract_indices_by_context(user_input, pass_phrases)
    recommendations = []
    for candidate in candidates:
        if candidate.index in disable_indices:
            action = "disable_summary"
        elif candidate.index in pass_indices:
            action = "pass"
        else:
            action = "summarize" if default_action == "summarize" else "pass"
        recommendations.append({"index": candidate.index, "action": action})
    recommendations_json = json.dumps(
        _group_actions_for_output(
            _normalize_actions(candidates, recommendations, default_action)
        ),
        ensure_ascii=True,
    )

    parsed = await _llm_format_actions(agent, candidates, user_input, batch_id, recommendations_json)
    normalized_actions: List[Dict[str, Any]]
    if any(key in parsed for key in ("summarize", "disable_summary", "pass")):
        normalized_actions = _normalize_actions_from_groups(candidates, parsed, default_action)
    else:
        llm_actions = parsed.get("actions", [])
        if not isinstance(llm_actions, list):
            raise ValueError("LLM did not return grouped actions or an action list.")
        normalized_actions = _normalize_actions(candidates, llm_actions, default_action)
    payload = {
        "batch_id": batch_id,
        **_group_actions_for_output(normalized_actions),
    }

    print("\nProposed actions (review before applying):")
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    confirm = input("Apply these actions? (y/n): ").strip().lower()
    if confirm != "y":
        print("No changes applied.")
        conn.close()
        return

    candidates_by_index = {c.index: c for c in candidates}
    _apply_disable_summary(conn, candidates_by_index, normalized_actions)
    await _summarize_actions(
        conn,
        candidates_by_index,
        normalized_actions,
    )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Search + human-loop summarizer REPL")
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="Path to the SQLite database (default: arxiv_crawler/data/arxiv.sqlite)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of search matches per batch (default: 10)",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="FTS5 search query (prompted if omitted)",
    )
    parser.add_argument(
        "--query-file",
        default=None,
        help="Path to a newline-delimited query term file (combined with OR)",
    )
    parser.add_argument(
        "--order-by",
        default="bm25",
        choices=["bm25", "impact", "hybrid"],
        help="Sort order for results (bm25, impact, hybrid)",
    )
    parser.add_argument(
        "--auto-summarize",
        action="store_true",
        help="Queue all matches without prompts",
    )
    parser.add_argument(
        "--show-count",
        action="store_true",
        help="Print total match count (can be slow on large DBs)",
    )
    parser.add_argument(
        "--rebuild-fts",
        action="store_true",
        help="Force a rebuild of the FTS index before searching",
    )
    parser.add_argument(
        "--llm-relevant-only",
        action="store_true",
        help="Restrict results to papers where llm_relevant = 1",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else _default_db_path()
    query_file = Path(args.query_file) if args.query_file else None
    asyncio.run(
        run_repl(
            db_path,
            args.limit,
            args.query,
            query_file,
            args.rebuild_fts,
            args.llm_relevant_only,
            args.order_by,
            args.auto_summarize,
            args.show_count,
        )
    )


if __name__ == "__main__":
    main()
