"""Integration tests for paper machine lifecycle.

Tests the full pipeline: ranking → extraction → digest
using mock sub-machines.
"""

import json
import sqlite3

import pytest


@pytest.mark.asyncio
async def test_full_pipeline(paper_machine):
    """Test the complete paper pipeline from start to done."""
    result = await paper_machine.execute(
        input={
            "paper_id": "2401.12345",
            "title": "Test Paper",
            "abstract": "A test abstract.",
        },
    )

    assert result["success"] is True, f"Pipeline failed: {result}"
    assert result["paper_id"] == "2401.12345"
    assert result["fmr_score"] == 0.85
    # Mock machines return hardcoded paths
    assert result["docling_json_path"] == "data/papers_docling_json/mock-paper.json"
    assert result["digest_path"] == "data/digests/mock-paper.md"


@pytest.mark.asyncio
async def test_checkpoints_created(paper_machine, test_db_path):
    """Test that checkpoints are created during pipeline execution."""
    await paper_machine.execute(
        input={
            "paper_id": "2401.67890",
            "title": "Checkpoint Test",
            "abstract": "Testing checkpoint creation.",
        },
    )

    # Verify checkpoints exist in DB (execution IDs are UUIDs, not paper_ids)
    conn = sqlite3.connect(test_db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM machine_checkpoints"
    ).fetchone()[0]
    assert count > 0, "No checkpoints created"

    # Verify latest checkpoints exist for multiple executions
    count_latest = conn.execute(
        "SELECT COUNT(*) FROM machine_latest"
    ).fetchone()[0]
    assert count_latest >= 1, "No latest checkpoints found"

    conn.close()


@pytest.mark.asyncio
async def test_generated_columns_queryable(paper_machine, test_db_path):
    """Test that generated columns enable efficient metadata queries."""
    await paper_machine.execute(
        input={
            "paper_id": "2401.99999",
            "title": "Generated Column Test",
            "abstract": "Testing queryable metadata.",
        },
    )

    conn = sqlite3.connect(test_db_path)

    # Query via generated fmr_score column — should find checkpoints with fmr_score
    rows = conn.execute(
        """
        SELECT mc.fmr_score
        FROM machine_checkpoints mc
        WHERE mc.fmr_score >= 0.6
        """
    ).fetchall()
    assert len(rows) > 0, "No checkpoints found with fmr_score >= 0.6"

    # Query via generated docling_json_path column
    rows = conn.execute(
        """
        SELECT mc.docling_json_path
        FROM machine_checkpoints mc
        WHERE mc.docling_json_path IS NOT NULL
        """
    ).fetchall()
    assert len(rows) > 0, "No checkpoints found with docling_json_path"

    conn.close()


@pytest.mark.asyncio
async def test_persistence_across_executions(paper_machine, test_db_path):
    """Test that state persists across separate machine executions."""
    # First execution
    result1 = await paper_machine.execute(
        input={
            "paper_id": "2401.11111",
            "title": "Persistence Test",
            "abstract": "Testing persistence.",
        },
    )
    assert result1["success"] is True

    # Verify state in DB after first execution — query any checkpoint with fmr_score
    conn = sqlite3.connect(test_db_path)
    row = conn.execute(
        """
        SELECT mc.fmr_score, mc.docling_json_path, mc.digest_path
        FROM machine_checkpoints mc
        WHERE mc.fmr_score IS NOT NULL
        LIMIT 1
        """
    ).fetchone()

    assert row is not None, "No checkpoint found after execution"
    assert row[0] == 0.85, f"fmr_score mismatch: {row[0]}"

    conn.close()


@pytest.mark.asyncio
async def test_v3_executions_save(test_db_path, test_project_root):
    """Test that _save_analyzer_result writes to v3_executions."""
    from research_paper_analysis_v3.hooks import V3Hooks

    hooks = V3Hooks(project_root=test_project_root, db_path=test_db_path)

    context = {
        "execution_id": "2401.55555",
        "arxiv_id": "2401.55555",
        "title": "Executions Test",
        "formatted_report": "# Test Report\n\nContent.",
    }
    result = await hooks._save_analyzer_result(context)

    assert result["result_path"] is not None

    # Check DB
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM v3_executions WHERE paper_id = ?",
        ("2401.55555",),
    ).fetchone()
    assert row is not None, "No row in v3_executions"
    assert row["status"] == "done", f"Status mismatch: {row['status']}"
    assert row["result_path"] is not None
    assert row["error"] is None
    conn.close()


@pytest.mark.asyncio
async def test_v3_executions_fail(test_db_path, test_project_root):
    """Test that _mark_execution_failed writes to v3_executions."""
    from research_paper_analysis_v3.hooks import V3Hooks

    hooks = V3Hooks(project_root=test_project_root, db_path=test_db_path)

    context = {
        "execution_id": "2401.66666",
        "arxiv_id": "2401.66666",
        "last_error": "HTTP 503: Service Unavailable",
    }
    result = await hooks._mark_execution_failed(context)

    assert result["error"] == "HTTP 503: Service Unavailable"

    # Check DB
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM v3_executions WHERE paper_id = ?",
        ("2401.66666",),
    ).fetchone()
    assert row is not None, "No row in v3_executions"
    assert row["status"] == "failed", f"Status mismatch: {row['status']}"
    assert "503" in row["error"]
    conn.close()


@pytest.mark.asyncio
async def test_v3_executions_queries(test_db_path, test_project_root):
    """Test PaperManagerHooks v3_executions query methods."""
    from research_paper_analysis_v3.hooks import V3Hooks
    from research_paper_analysis_v3.paper_manager import PaperManagerHooks

    hooks = V3Hooks(project_root=test_project_root, db_path=test_db_path)

    # Save a successful execution
    await hooks._save_analyzer_result(
        {
            "execution_id": "2401.77777",
            "arxiv_id": "2401.77777",
            "title": "Done Paper",
            "formatted_report": "# Done\nContent.",
        }
    )
    # Save a failed execution
    await hooks._mark_execution_failed(
        {
            "execution_id": "2401.88888",
            "arxiv_id": "2401.88888",
            "last_error": "timeout",
        }
    )

    # Query via PaperManagerHooks
    pm = PaperManagerHooks(db_path=test_db_path)

    done = pm.get_done_executions()
    assert len(done) >= 1, "No done executions found"
    assert any(e["paper_id"] == "2401.77777" for e in done)

    failed = pm.get_failed_executions()
    assert len(failed) >= 1, "No failed executions found"
    assert any(e["paper_id"] == "2401.88888" for e in failed)

    # Query by arbitrary status (should return empty for non-existent status)
    pending = pm.get_executions_by_status("pending")
    assert pending == []


@pytest.mark.asyncio
async def test_v3_executions_prep(test_db_path, test_project_root):
    """Test that _save_prep_result writes 'prepped' to v3_executions."""
    from research_paper_analysis_v3.hooks import V3Hooks

    hooks = V3Hooks(project_root=test_project_root, db_path=test_db_path)

    context = {
        "arxiv_id": "2401.99999",
        "docling_json_path": "data/papers_docling_json/2401.99999.json",
    }
    await hooks._save_prep_result(context)

    # Check DB
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM v3_executions WHERE paper_id = ?",
        ("2401.99999",),
    ).fetchone()
    assert row is not None, "No row in v3_executions"
    assert row["status"] == "prepped", f"Status mismatch: {row['status']}"
    assert "papers_docling_json" in row["result_path"]
    conn.close()


@pytest.mark.asyncio
async def test_clean_paper_strips_references(test_db_path, test_project_root):
    """Test that _clean_paper strips the References section."""
    from research_paper_analysis_v3.hooks import V3Hooks

    # Create a minimal docling JSON with references
    docling_dir = test_project_root / "data" / "papers_docling_json"
    docling_dir.mkdir(parents=True, exist_ok=True)
    json_path = docling_dir / "2401.test.json"
    import json as json_module

    doc_data = {
        "texts": [
            {"text": "Attention Is All You Need"},
            {"text": "The transformer architecture..."},
            {"text": "References"},
            {"text": "[1] Some citation"},
            {"text": "[2] Another citation"},
        ]
    }
    json_path.write_text(json_module.dumps(doc_data))

    hooks = V3Hooks(project_root=test_project_root, db_path=test_db_path)
    context = {
        "docling_json_path": str(json_path),
    }
    result = await hooks._clean_paper(context)

    cleaned = result.get("cleaned_paper_text", "")
    assert "attention" in cleaned.lower()
    assert "transformer" in cleaned.lower()
    assert "references" not in cleaned.lower(), f"References not stripped: {cleaned}"
    assert "citation" not in cleaned.lower(), f"Citations not stripped: {cleaned}"
