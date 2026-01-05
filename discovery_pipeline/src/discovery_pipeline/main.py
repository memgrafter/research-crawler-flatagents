"""Discovery pipeline main entry point."""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flatagents import FlatMachine, FlatAgent

# Import child hooks directly - packages must be installed
from arxiv_crawler.hooks import CrawlerHooks
from relevance_scoring.hooks import ScoringHooks
from reverse_citation_enrichment.hooks import CitationHooks


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Base directories
BASE_DIR = Path(__file__).parent.parent.parent
ARXIV_CRAWLER_DIR = BASE_DIR.parent / "arxiv_crawler"
RELEVANCE_SCORING_DIR = BASE_DIR.parent / "relevance_scoring"
CITATION_ENRICHMENT_DIR = BASE_DIR.parent / "reverse_citation_enrichment"
CONFIG_DIR = BASE_DIR / "config"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paper discovery pipeline"
    )
    parser.add_argument(
        "--db-path",
        default="../arxiv_crawler/data/arxiv.sqlite",
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Max papers to process per phase",
    )
    parser.add_argument(
        "--report-path",
        default="./reports",
        help="Directory to save reports",
    )
    parser.add_argument(
        "--skip-crawl",
        action="store_true",
        help="Skip the crawl phase",
    )
    parser.add_argument(
        "--skip-score",
        action="store_true",
        help="Skip the scoring phase",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip the enrichment phase",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making changes",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Override auto-detected since date (YYYY-MM-DD format)",
    )
    return parser.parse_args()


async def run_crawler(db_path: str, since: str) -> dict:
    """Run the arXiv crawler child machine."""
    config_path = ARXIV_CRAWLER_DIR / "config" / "machine.yml"
    machine = FlatMachine(
        config_file=str(config_path),
        hooks=CrawlerHooks(),
    )
    
    return await machine.execute(input={
        "db_path": db_path,
        "since": since,
        "until": None,
        "categories": [],  # Hooks default to LLM_RELEVANCE_CATEGORIES
        "max_results": 5000,
    })


async def run_scorer(db_path: str, limit: int) -> dict:
    """Run the relevance scoring child machine."""
    config_path = RELEVANCE_SCORING_DIR / "config" / "machine.yml"
    machine = FlatMachine(
        config_file=str(config_path),
        hooks=ScoringHooks(),
    )
    
    return await machine.execute(input={
        "db_path": db_path,
        "config_path": str(BASE_DIR.parent / "relevance_scoring.yml"),
        "limit": limit,
    })


async def run_enricher(db_path: str, limit: int, since: str = None) -> dict:
    """Run the citation enrichment child machine."""
    config_path = CITATION_ENRICHMENT_DIR / "config" / "machine.yml"
    machine = FlatMachine(
        config_file=str(config_path),
        hooks=CitationHooks(),
    )
    
    return await machine.execute(input={
        "db_path": db_path,
        "limit": limit,
        "since": since,  # Filter to only enrich papers updated since this date
        "no_authors_only": True,
        "cooldown_days": 7,  # Match default report span - don't retry failed papers for a week
    })


async def generate_report(
    db_path: str,
    since: str,
    crawl_result: dict,
    score_result: dict,
    enrich_result: dict,
    report_path: str,
) -> str:
    """Generate LLM report on top papers."""
    import sqlite3
    
    query = """
        SELECT 
            p.id, p.title, p.abstract, p.arxiv_id,
            p.published_at as first_published,
            p.updated_at as last_updated,
            p.primary_category,
            p.categories,
            pr.fmr_score, pc.cited_by_count,
            GROUP_CONCAT(DISTINCT a.display_name) as authors,
            MAX(a.h_index) as max_h_index
        FROM papers p
        JOIN paper_relevance pr ON p.id = pr.paper_id
        LEFT JOIN paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN authors a ON a.openalex_id = pa.author_openalex_id
        LEFT JOIN paper_citations pc ON pc.paper_id = p.id
        WHERE p.llm_relevant = 1 AND COALESCE(p.updated_at, p.published_at) >= ?
        GROUP BY p.id
        ORDER BY pr.fmr_score DESC, max_h_index DESC NULLS LAST
        LIMIT 15
    """
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, (since or "2000-01-01",)).fetchall()
    conn.close()
    
    papers = []
    for row in rows:
        arxiv_id = row["arxiv_id"] or ""
        # Extract version from arxiv_id (e.g., "2401.12345v2" -> "v2")
        version = ""
        if "v" in arxiv_id:
            parts = arxiv_id.split("v")
            if len(parts) > 1 and parts[-1].isdigit():
                version = f"v{parts[-1]}"
        
        papers.append({
            "title": row["title"] or "Untitled",
            "abstract": row["abstract"] or "",
            "arxiv_id": arxiv_id,
            "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
            "first_published": row["first_published"] or "Unknown",
            "last_updated": row["last_updated"] or "",
            "version": version,
            "primary_category": row["primary_category"] or "",
            "categories": row["categories"] or "",
            "fmr_score": row["fmr_score"] if row["fmr_score"] is not None else 0.0,
            "cited_by_count": row["cited_by_count"] or 0,
            "authors": row["authors"] or "Unknown",
            "max_h_index": row["max_h_index"] if row["max_h_index"] is not None else 0,
        })
    
    if not papers:
        logger.info("No papers found for report")
        return "No papers found."
    
    logger.info(f"Generating report for {len(papers)} papers")
    
    reporter = FlatAgent(config_file=str(CONFIG_DIR / "reporter.yml"))
    try:
        result = await reporter.call(
            papers=papers,
            since=since,
            new_count=crawl_result.get("new_count", 0),
            scored_count=score_result.get("scored_count", 0),
            enriched_count=enrich_result.get("resolved_count", 0),
        )
        
        # Debug: log what we got from the agent
        logger.info(f"AgentResponse: content={bool(result.content)}, output={bool(result.output)}, content_len={len(result.content) if result.content else 0}")
        
        # If content is empty, log the raw response for debugging
        if not result.content:
            logger.error(f"Empty content! raw_response: {result.raw_response}")
        
        # Check for error in result
        if hasattr(result, 'error') and result.error:
            logger.error(f"Agent error: {result.error}")
        
        # No output schema = plain text in result.content
        report_text = result.content or "Report generation failed - LLM returned empty response"
    except Exception as e:
        logger.error(f"Report generation failed: {e}", exc_info=True)
        report_text = f"Report generation failed: {e}"
    
    report_dir = Path(report_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filepath = report_dir / f"discovery_report_{timestamp}.md"
    
    full_report = f"""# Discovery Report - {timestamp}

**Pipeline Results:**
- New papers crawled: {crawl_result.get('new_count', 0)}
- Papers scored: {score_result.get('scored_count', 0)}  
- Papers enriched: {enrich_result.get('resolved_count', 0)}

---

{report_text}
"""
    
    filepath.write_text(full_report)
    logger.info(f"Saved report to {filepath}")
    return str(filepath)


async def get_last_run_date(db_path: str) -> str:
    """Get the last crawler run date from DB.
    
    Uses crawler_state.last_updated_at first (most reliable), 
    then falls back to crawl_runs table.
    """
    import sqlite3
    
    if not Path(db_path).exists():
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        logger.info(f"First run, using since={since}")
        return since
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Best option: use crawler_state.last_updated_at (set by arxiv crawler)
        row = conn.execute(
            "SELECT value FROM crawler_state WHERE key = 'last_updated_at'"
        ).fetchone()
        if row and row[0]:
            last_updated = row[0][:10]  # Just the date portion
            logger.info(f"Using last_updated_at from crawler_state: {last_updated}")
            conn.close()
            return last_updated
        
        # Fallback: check crawl_runs table (note: it's 'crawl_runs' not 'crawler_runs')
        row = conn.execute(
            "SELECT MAX(finished_at) FROM crawl_runs WHERE status = 'ok'"
        ).fetchone()
        conn.close()
        
        if row and row[0]:
            last_run = row[0][:10]
            logger.info(f"Last run was {last_run}")
            return last_run
    except sqlite3.OperationalError as e:
        logger.warning(f"DB error getting last run date: {e}")
    
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    logger.info(f"No previous runs, using since={since}")
    return since


async def run(args: argparse.Namespace) -> dict:
    logger.info("=" * 60)
    logger.info("Paper Discovery Pipeline")
    logger.info("=" * 60)
    
    db_path = args.db_path
    since = args.since or await get_last_run_date(db_path)
    
    crawl_result = score_result = enrich_result = {}
    
    if not args.skip_crawl:
        logger.info("Phase 1: Crawling arXiv")
        crawl_result = await run_crawler(db_path, since)
        logger.info(f"Crawl: {crawl_result.get('new_count', 0)} new papers")
    
    if not args.skip_score:
        logger.info("Phase 2: Scoring papers")
        score_result = await run_scorer(db_path, args.limit)
        logger.info(f"Scored: {score_result.get('scored_count', 0)} papers")
    
    if not args.skip_enrich:
        logger.info("Phase 3: Enriching with OpenAlex")
        enrich_result = await run_enricher(db_path, args.limit, since=since)
        logger.info(f"Enriched: {enrich_result.get('resolved_count', 0)} papers")
    
    logger.info("Phase 4: Generating report")
    report_path = await generate_report(
        db_path, since, crawl_result, score_result, enrich_result, args.report_path
    )
    
    logger.info("=" * 60)
    logger.info(f"Pipeline complete! Report: {report_path}")
    
    return {"crawl": crawl_result, "score": score_result, "enrich": enrich_result, "report": report_path}


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
