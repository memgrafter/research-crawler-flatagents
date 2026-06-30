"""End-to-end integration test for prep pipeline with real PDF.

Tests the actual download + Docling extraction pipeline using a real
arXiv paper (Attention Is All You Need, 1706.03762) downloaded from
Kaggle/GCS.
"""

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_prep_e2e_attention_is_all_you_need(test_project_root, test_db_path):
    """Full prep pipeline: download PDF from GCS → extract with Docling.

    Uses 1706.03762 (Attention Is All You Need) which is available on Kaggle/GCS.
    """
    from flatmachines import FlatMachine, HooksRegistry
    from flatmachines.persistence import SQLiteCheckpointBackend

    from research_paper_analysis_v3.hooks import V3Hooks

    ARXIV_ID = "1706.03762"
    TITLE = "Attention Is All You Need"

    # Build hooks registry
    registry = HooksRegistry()
    registry.register(
        "v3-hooks",
        lambda: V3Hooks(
            project_root=test_project_root,
            data_dir=test_project_root / "data",
            db_path=test_db_path,
        ),
    )

    # Create prep machine with real persistence
    persistence = SQLiteCheckpointBackend(db_path=test_db_path)
    machine = FlatMachine(
        config_file="config/prep_machine.yml",
        hooks_registry=registry,
        persistence=persistence,
    )

    # Run the prep pipeline
    result = await machine.execute(
        input={
            "arxiv_id": ARXIV_ID,
            "paper_id": ARXIV_ID,
            "title": TITLE,
            "abstract": "We propose a new simple network architecture, the Transformer...",
        },
    )

    # Verify result — FlatMachine returns output from final state
    assert "error" not in result, f"Prep pipeline failed: {result}"
    assert result.get("arxiv_id") == ARXIV_ID

    # Verify PDF was downloaded (arxiv_id with / replaced by _)
    pdf_path = test_project_root / "data" / "1706.03762.pdf"
    assert pdf_path.exists(), f"PDF not found: {pdf_path}"
    assert pdf_path.stat().st_size > 0, "PDF is empty"

    # Verify Docling JSON was created
    docling_path = test_project_root / "data" / "papers_docling_json" / "1706.03762.json"
    assert docling_path.exists(), f"Docling JSON not found: {docling_path}"
    assert docling_path.stat().st_size > 0, "Docling JSON is empty"

    # Verify Docling JSON content has expected structure
    with open(docling_path) as f:
        doc_data = json.load(f)

    # DoclingDocument dict should have text content
    assert isinstance(doc_data, dict), "Docling output is not a dict"
    # The document should have some content (text from the paper)
    # Docling structure has pages with text elements
    doc_text = _extract_text_from_docling(doc_data)
    assert len(doc_text) > 1000, f"Docling text too short: {len(doc_text)} chars"

    # The paper should contain expected content from Attention Is All You Need
    doc_text_lower = doc_text.lower()
    assert "attention" in doc_text_lower, "Expected 'attention' in extracted text"
    assert "transformer" in doc_text_lower, "Expected 'transformer' in extracted text"

    # Verify v3_executions DB was updated with prepped status
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM v3_executions WHERE paper_id = ?",
        (ARXIV_ID,),
    ).fetchone()
    assert row is not None, "No row in v3_executions"
    assert row["status"] == "prepped", f"Status mismatch: {row['status']}"
    assert row["result_path"] is not None, "result_path is None"
    assert "papers_docling_json" in row["result_path"], f"Unexpected result_path: {row['result_path']}"
    conn.close()

    # Verify cleaned_paper_text was produced
    cleaned = result.get("cleaned_paper_text", "")
    assert len(cleaned) > 1000, f"Cleaned text too short: {len(cleaned)} chars"
    assert "attention" in cleaned.lower(), "Expected 'attention' in cleaned text"
    assert "transformer" in cleaned.lower(), "Expected 'transformer' in cleaned text"
    # References section should be stripped
    import re
    assert not re.search(r"References", cleaned, re.I), "References section not stripped"


def _extract_text_from_docling(doc_data: dict) -> str:
    """Extract text content from a DoclingDocument dict.

    Docling documents store text in the 'texts' array at the top level,
    where each item has a 'text' field with the actual content.
    """
    texts = doc_data.get("texts", [])
    if not isinstance(texts, list):
        return ""

    parts = []
    for item in texts:
        if isinstance(item, dict) and "text" in item:
            text = item["text"]
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts)
