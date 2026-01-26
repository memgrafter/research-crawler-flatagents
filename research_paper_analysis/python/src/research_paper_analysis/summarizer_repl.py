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

from research_paper_analysis.main import run as analyze_paper

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


def _fetch_candidates(conn: sqlite3.Connection, limit: int) -> List[Candidate]:
    query = """
        WITH latest_versions AS (
            SELECT arxiv_id, MAX(version) AS max_version
            FROM papers
            GROUP BY arxiv_id
        )
        SELECT
            p.id,
            p.arxiv_id,
            p.title,
            p.abstract,
            pr.fmr_score,
            pc.cited_by_count,
            GROUP_CONCAT(DISTINCT a.display_name) as authors,
            MAX(a.h_index) as max_h_index
        FROM papers p
        JOIN latest_versions lv
          ON lv.arxiv_id = p.arxiv_id
         AND lv.max_version = p.version
        JOIN paper_relevance pr ON pr.paper_id = p.id
        LEFT JOIN paper_citations pc ON pc.paper_id = p.id
        LEFT JOIN paper_authors pa ON pa.paper_id = p.id
        LEFT JOIN authors a ON a.openalex_id = pa.author_openalex_id
        LEFT JOIN paper_queue pq ON pq.paper_id = p.id
        WHERE p.llm_relevant = 1
          AND p.disable_summary = 0
          AND (pq.summary_path IS NULL OR pq.summary_path = '')
        GROUP BY p.id
        ORDER BY pr.fmr_score DESC,
                 (MAX(a.h_index) IS NULL) ASC,
                 MAX(a.h_index) DESC,
                 pc.cited_by_count DESC
        LIMIT ?
    """
    rows = conn.execute(query, (limit,)).fetchall()
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


def _override_actions(
    actions: List[Dict[str, Any]],
    disable_indices: List[int],
    pass_indices: List[int],
) -> List[Dict[str, Any]]:
    disable_set = set(disable_indices)
    pass_set = set(pass_indices)
    for action in actions:
        idx = action.get("index")
        if idx in disable_set:
            action["action"] = "disable_summary"
        elif idx in pass_set:
            action["action"] = "pass"
    return actions


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


async def _llm_format_actions(
    agent: FlatAgent,
    candidates: List[Candidate],
    user_input: str,
    batch_id: str,
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
    response = await agent.call(
        candidates_json=json.dumps(candidates_payload, ensure_ascii=True),
        user_input=user_input,
        batch_id=batch_id,
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
    if not parsed:
        raise ValueError("LLM did not return valid JSON.")
    return parsed


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
    to_summarize = [a for a in actions if a["action"] == "summarize"]
    to_summarize.sort(key=lambda item: item.get("priority", 0))

    for action in to_summarize:
        candidate = candidates_by_index[action["index"]]
        priority = int(action.get("priority") or 0)
        _ensure_queue_row(conn, candidate.paper_id, priority)

        started_at = _utc_now_iso()
        conn.execute(
            """
            UPDATE paper_queue
            SET status = ?, started_at = ?, worker = ?, priority = ?
            WHERE paper_id = ?
            """,
            ("processing", started_at, "summarizer", priority, candidate.paper_id),
        )
        conn.commit()

        try:
            result = await analyze_paper(arxiv_input=candidate.arxiv_id)
            report_path = ""
            if isinstance(result, dict):
                report_path = result.get("report_path") or ""
            finished_at = _utc_now_iso()
            conn.execute(
                """
                UPDATE paper_queue
                SET status = ?, finished_at = ?, summary_path = ?, error = NULL
                WHERE paper_id = ?
                """,
                ("done", finished_at, report_path, candidate.paper_id),
            )
            conn.commit()
        except Exception as exc:
            finished_at = _utc_now_iso()
            conn.execute(
                """
                UPDATE paper_queue
                SET status = ?, finished_at = ?, error = ?
                WHERE paper_id = ?
                """,
                ("error", finished_at, str(exc), candidate.paper_id),
            )
            conn.commit()
            logger.exception("Summarization failed for %s", candidate.arxiv_id)


async def run_repl(db_path: Path, limit: int) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)

    candidates = _fetch_candidates(conn, limit)
    if not candidates:
        print("No eligible papers found.")
        conn.close()
        return

    print("\n=== Candidate Papers ===\n")
    print(_format_candidates(candidates))
    print("\nEnter your instructions. Unmentioned papers default to pass.")
    print("Use phrases like: 'do them all', 'do all except ...', or 'do not summarize ...'.\n")

    user_input = input("Your instructions: ").strip()
    if not user_input:
        user_input = "pass"

    batch_id = _batch_id()
    default_action = _infer_default_action(user_input)

    config_dir = _repo_root() / "research_paper_analysis" / "config"
    agent = FlatAgent(config_file=str(config_dir / "summarizer_humanloop.yml"))

    parsed = await _llm_format_actions(agent, candidates, user_input, batch_id)
    llm_actions = parsed.get("actions", [])
    if not isinstance(llm_actions, list):
        raise ValueError("LLM did not return an action list.")

    normalized_actions = _normalize_actions(candidates, llm_actions, default_action)
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
    normalized_actions = _override_actions(normalized_actions, disable_indices, pass_indices)
    payload = {
        "batch_id": batch_id,
        "actions": normalized_actions,
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
    await _summarize_actions(conn, candidates_by_index, normalized_actions)
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Human-loop summarizer REPL")
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
        help="Number of candidate papers per batch (default: 10)",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else _default_db_path()
    asyncio.run(run_repl(db_path, args.limit))


if __name__ == "__main__":
    main()
