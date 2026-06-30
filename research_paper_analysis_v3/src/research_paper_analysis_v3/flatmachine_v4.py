"""FlatMachine v4 hooks registry and persistence for v3."""

from __future__ import annotations

import os
from pathlib import Path

from flatmachines.hooks import HooksRegistry
from flatmachines.persistence import SQLiteCheckpointBackend

from research_paper_analysis_v3.hooks import V3Hooks

V3_HOOKS_NAME = "v3-hooks"

# Singleton checkpoint backend (shared across machines in one process)
_CHECKPOINT_BACKEND: SQLiteCheckpointBackend | None = None


def create_v3_hooks_registry(project_root: Path | None = None) -> HooksRegistry:
    """Create the FlatMachines v4 hooks registry required by config/*.yml."""
    project_root = project_root or Path(__file__).resolve().parent.parent.parent
    data_dir = project_root / "data"
    db_path = os.environ.get(
        "V3_PAPERS_DB_PATH",
        str(data_dir / "v3_papers.sqlite"),
    )

    registry = HooksRegistry()
    registry.register(
        V3_HOOKS_NAME,
        lambda: V3Hooks(project_root=project_root, data_dir=data_dir, db_path=db_path),
    )
    return registry


def get_checkpoint_backend(db_path: str | None = None) -> SQLiteCheckpointBackend:
    """Return a process-wide DB-backed checkpoint backend (singleton)."""
    global _CHECKPOINT_BACKEND
    if _CHECKPOINT_BACKEND is not None:
        return _CHECKPOINT_BACKEND

    project_root = Path(__file__).resolve().parent.parent.parent
    db_path = db_path or os.environ.get(
        "V3_EXECUTIONS_DB_PATH",
        str(project_root / "data" / "v3_checkpoints.sqlite"),
    )
    _CHECKPOINT_BACKEND = SQLiteCheckpointBackend(db_path=db_path)
    return _CHECKPOINT_BACKEND
