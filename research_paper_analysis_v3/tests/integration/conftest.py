"""Integration test fixtures for paper machine."""

import asyncio
import tempfile
from pathlib import Path

import pytest

# Resolve paths relative to this file's directory
TESTS_DIR = Path(__file__).resolve().parent
CONFIG_DIR = TESTS_DIR / "config"


@pytest.fixture
def test_db_path(tmp_path: Path) -> str:
    """Create a temporary SQLite database path for each test."""
    return str(tmp_path / "test_papers.sqlite")


@pytest.fixture
def test_trigger_base(tmp_path: Path) -> str:
    """Create a temporary trigger directory for each test."""
    trigger_dir = tmp_path / "trigger"
    trigger_dir.mkdir()
    return str(trigger_dir)


@pytest.fixture
def test_project_root(tmp_path: Path) -> Path:
    """Temporary project root."""
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def paper_machine_config() -> Path:
    """Path to the test paper machine config."""
    return CONFIG_DIR / "test_paper_machine.yml"


@pytest.fixture
def mock_ranking_hooks():
    """Mock ranking hooks."""
    from tests.integration.mock_hooks import MockRankingHooks

    return MockRankingHooks()


@pytest.fixture
def mock_extraction_hooks():
    """Mock extraction hooks."""
    from tests.integration.mock_hooks import MockExtractionHooks

    return MockExtractionHooks()


@pytest.fixture
def mock_digest_hooks():
    """Mock digest hooks."""
    from tests.integration.mock_hooks import MockDigestHooks

    return MockDigestHooks()


@pytest.fixture
async def paper_machine(
    paper_machine_config: Path,
    test_db_path: str,
    test_trigger_base: str,
    test_project_root: Path,
):
    """Create a paper machine with all mock hooks registered.

    Returns the FlatMachine instance ready for execute().
    """
    from flatmachines import FlatMachine, HooksRegistry
    from flatmachines.persistence import SQLiteCheckpointBackend

    from research_paper_analysis_v3.paper_manager import PaperManagerHooks, _ensure_migration
    from tests.integration.mock_hooks import (
        MockDigestHooks,
        MockExtractionHooks,
        MockRankingHooks,
    )

    # Build hooks registry with paper manager + mock sub-machine hooks
    registry = HooksRegistry()
    registry.register(
        "paper-manager",
        lambda: PaperManagerHooks(
            db_path=test_db_path,
            trigger_base=test_trigger_base,
            project_root=test_project_root,
        ),
    )
    registry.register("mock-ranking", MockRankingHooks)
    registry.register("mock-extraction", MockExtractionHooks)
    registry.register("mock-digest", MockDigestHooks)

    # Use the same DB for both SDK persistence and our hooks
    persistence = SQLiteCheckpointBackend(db_path=test_db_path)

    machine = FlatMachine(
        config_file=str(paper_machine_config),
        hooks_registry=registry,
        persistence=persistence,
    )

    # Run migration so generated columns are available for direct DB queries
    _ensure_migration(test_db_path)

    return machine
