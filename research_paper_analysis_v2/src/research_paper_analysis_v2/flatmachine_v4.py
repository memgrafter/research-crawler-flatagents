from __future__ import annotations

from flatmachines.hooks import HooksRegistry

from research_paper_analysis_v2.hooks import V2Hooks

V2_HOOKS_NAME = "v2-hooks"


def create_v2_hooks_registry() -> HooksRegistry:
    """Create the FlatMachines v4 hooks registry required by config/*.yml."""
    registry = HooksRegistry()
    registry.register(V2_HOOKS_NAME, V2Hooks)
    return registry
