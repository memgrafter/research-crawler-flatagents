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
