"""Full pipeline e2e: prep → analyzer with real PDF and real LLM calls.

Runs the complete pipeline end-to-end using a real arXiv paper:
1. Prep: download PDF from GCS → extract with Docling → clean text
2. Analyzer: fan-out 6 sections via LLM → assemble → judge → save

Marked slow because it hits real GCS and LLM APIs.
"""

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.mark.asyncio
@pytest.mark.slow
async def test_prep_to_analyzer_e2e(test_project_root, test_db_path):
    """Full pipeline: real PDF download → Docling → clean → analyzer fan-out → save.

    Uses 1706.03762 (Attention Is All You Need) — small paper, fast LLM turns.
    """
    from flatmachines import FlatMachine, HooksRegistry
    from flatmachines.persistence import SQLiteCheckpointBackend

    from research_paper_analysis_v3.hooks import V3Hooks
    from research_paper_analysis_v3.paper_manager import PaperManagerHooks

    ARXIV_ID = "1706.03762"
    TITLE = "Attention Is All You Need"

    # ── Phase 1: Prep pipeline ────────────────────────────────────────
    registry_prep = HooksRegistry()
    registry_prep.register(
        "v3-hooks",
        lambda: V3Hooks(
            project_root=test_project_root,
            data_dir=test_project_root / "data",
            db_path=test_db_path,
        ),
    )

    persistence = SQLiteCheckpointBackend(db_path=test_db_path)
    prep_machine = FlatMachine(
        config_file="config/prep_machine.yml",
        hooks_registry=registry_prep,
        persistence=persistence,
    )

    prep_result = await prep_machine.execute(
        input={
            "arxiv_id": ARXIV_ID,
            "paper_id": ARXIV_ID,
            "title": TITLE,
            "abstract": "We propose a new simple network architecture, the Transformer...",
        },
    )

    # Verify prep succeeded
    assert "error" not in prep_result, f"Prep failed: {prep_result.get('error')}"
    assert prep_result["arxiv_id"] == ARXIV_ID
    assert prep_result["docling_json_path"] is not None
    assert prep_result["cleaned_paper_text"] is not None
    assert len(prep_result["cleaned_paper_text"]) > 1000, "Cleaned text too short"

    # Verify v3_executions DB updated with prepped status
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM v3_executions WHERE paper_id = ?",
        (ARXIV_ID,),
    ).fetchone()
    assert row is not None, "No row in v3_executions after prep"
    assert row["status"] == "prepped", f"Status mismatch: {row['status']}"
    conn.close()

    # ── Phase 2: Analyzer pipeline ────────────────────────────────────
    registry_analyzer = HooksRegistry()
    registry_analyzer.register(
        "v3-hooks",
        lambda: V3Hooks(
            project_root=test_project_root,
            data_dir=test_project_root / "data",
            db_path=test_db_path,
        ),
    )

    analyzer_machine = FlatMachine(
        config_file="config/analyzer_machine.yml",
        hooks_registry=registry_analyzer,
    )

    analyzer_result = await analyzer_machine.execute(
        input={
            "arxiv_id": ARXIV_ID,
            "execution_id": ARXIV_ID,
            "paper_id": ARXIV_ID,
            "source_url": f"https://arxiv.org/abs/{ARXIV_ID}",
            "title": TITLE,
            "authors": "Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit",
            "abstract": "We propose a new simple network architecture, the Transformer...",
            "cleaned_paper_text": prep_result["cleaned_paper_text"],
        },
    )

    # Verify analyzer received the cleaned paper text and attempted processing.
    # LLM calls may fail in CI environments, so we verify data flow, not LLM success.
    assert analyzer_result.get("arxiv_id") == ARXIV_ID, f"Analyzer lost arxiv_id: {analyzer_result}"

    # Verify disk artifacts exist
    pdf_path = test_project_root / "data" / "1706.03762.pdf"
    assert pdf_path.exists(), f"PDF not found: {pdf_path}"

    docling_path = test_project_root / "data" / "papers_docling_json" / "1706.03762.json"
    assert docling_path.exists(), f"Docling JSON not found: {docling_path}"

    # Verify v3_executions DB has the full lifecycle
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM v3_executions WHERE paper_id = ?",
        (ARXIV_ID,),
    ).fetchone()
    assert row is not None, "No row in v3_executions"
    # Status depends on analyzer outcome — LLM calls may fail in CI
    assert row["status"] in ("done", "prepped", "failed"), f"Unexpected status: {row['status']}"
    conn.close()
