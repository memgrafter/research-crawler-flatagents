"""Paper Machine Registry — hooks and persistence for v3 paper orchestrator."""

from __future__ import annotations

import os
from pathlib import Path

from flatmachines.hooks import HooksRegistry
from flatmachines.persistence import SQLiteCheckpointBackend

from research_paper_analysis_v3.paper_manager import PaperManagerHooks

PAPER_MANAGER_HOOKS_NAME = "paper-manager"

# Singleton checkpoint backend (shared across machines in one process)
_CHECKPOINT_BACKEND: SQLiteCheckpointBackend | None = None


def create_paper_manager_hooks_registry(
    project_root: Path | None = None,
) -> HooksRegistry:
    """Create the FlatMachines v4 hooks registry for paper machine orchestration."""
    project_root = project_root or Path(__file__).resolve().parent.parent.parent
    data_dir = project_root / "data"

    db_path = os.environ.get(
        "V3_PAPERS_DB_PATH",
        str(data_dir / "v3_papers.sqlite"),
    )
    trigger_base = os.environ.get(
        "V3_TRIGGER_BASE",
        str(data_dir / "trigger"),
    )

    registry = HooksRegistry()
    registry.register(
        PAPER_MANAGER_HOOKS_NAME,
        lambda: PaperManagerHooks(
            db_path=db_path,
            trigger_base=trigger_base,
            project_root=project_root,
        ),
    )
    return registry


def get_checkpoint_backend(db_path: str | None = None) -> SQLiteCheckpointBackend:
    """Return a process-wide DB-backed checkpoint backend (singleton)."""
    global _CHECKPOINT_BACKEND
    if _CHECKPOINT_BACKEND is not None:
        return _CHECKPOINT_BACKEND

    project_root = Path(__file__).resolve().parent.parent.parent
    db_path = db_path or os.environ.get(
        "V3_PAPERS_DB_PATH",
        str(project_root / "data" / "v3_papers.sqlite"),
    )
    _CHECKPOINT_BACKEND = SQLiteCheckpointBackend(db_path=db_path)
    return _CHECKPOINT_BACKEND
