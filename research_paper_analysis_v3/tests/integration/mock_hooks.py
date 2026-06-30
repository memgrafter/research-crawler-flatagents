"""Mock hooks for integration tests.

Provides deterministic sub-machine behavior:
- ranking: assigns fmr_score = 0.85 (above 0.6 threshold)
- extraction: returns a fake docling_json_path
- digest: returns a fake digest_path
"""

from __future__ import annotations

from typing import Any, Dict

from flatmachines.hooks import LoggingHooks


class MockRankingHooks(LoggingHooks):
    """Mock ranking sub-machine — always assigns fmr_score = 0.85."""

    async def on_action(
        self, state_name: str, action_name: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        if action_name == "assign_score":
            context["fmr_score"] = 0.85
            context["phase"] = "ranked"
            context["ranking_complete"] = True
        elif action_name == "after_ranking":
            # Mark ranking complete after sub-machine returns
            context["phase"] = "ranked"
            context["ranking_complete"] = True
        return context


class MockExtractionHooks(LoggingHooks):
    """Mock extraction sub-machine — returns a fake docling path."""

    async def on_action(
        self, state_name: str, action_name: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        if action_name == "extract":
            paper_id = context.get("paper_id", "unknown")
            context["docling_json_path"] = f"data/papers_docling_json/{paper_id}.json"
            context["phase"] = "extracted"
            context["extraction_complete"] = True
        elif action_name == "after_extraction":
            # Mark extraction complete after sub-machine returns
            context["phase"] = "extracted"
            context["extraction_complete"] = True
        return context


class MockDigestHooks(LoggingHooks):
    """Mock digest sub-machine — returns a fake digest path."""

    async def on_action(
        self, state_name: str, action_name: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        if action_name == "digest":
            paper_id = context.get("paper_id", "unknown")
            context["digest_path"] = f"data/digests/{paper_id}.md"
            context["phase"] = "done"
            context["digest_complete"] = True
        elif action_name == "after_digest":
            # Mark digest complete after sub-machine returns
            context["phase"] = "done"
            context["digest_complete"] = True
        return context
