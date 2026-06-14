"""Hooks for Research Paper Analysis V3.

Implements custom FlatMachine actions for the analyzer pipeline:

KV cache actions (kv_cache_machine.yml):
- warmup_kv_cache           — warm up KV cache with paper text (later: pin via proxy)

Analyzer actions (analyzer_machine.yml):
- unpack_fan_out_results    — parse parallel fan-out output into context
- assemble_report           — non-agentic string concatenation of sections
- normalize_judge_decision  — parse judge JSON → PASS/REPAIR/FAIL
- set_repair_attempted      — flag that repair has been attempted
- prepend_frontmatter_v3    — build YAML frontmatter + prepend to report
- save_analyzer_result      — write final digest to disk, update DB
- mark_execution_failed     — record failure in DB
"""

from __future__ import annotations

import json
import re
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from flatmachines import LoggingHooks
from flatagents import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify_title(title: Optional[str]) -> str:
    if not title:
        return "unknown"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())
    return slug.strip("-")[:80]


def _approx_tokens(text: Optional[str]) -> int:
    """Rough token estimate: ~4 chars per token."""
    if not text:
        return 0
    return len(str(text)) // 4


# ---------------------------------------------------------------------------
# V3 Hooks
# ---------------------------------------------------------------------------

class V3Hooks(LoggingHooks):
    """FlatMachine hooks for the v3 analyzer pipeline."""

    def __init__(self, project_root: Path, data_dir: Optional[Path] = None):
        super().__init__()
        self._project_root = project_root
        self._data_dir = data_dir or project_root / "data"

    # ------------------------------------------------------------------
    # Action router
    # ------------------------------------------------------------------

    async def on_action(self, state_name: str, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            # KV cache
            "mark_cache_pinned": self._mark_cache_pinned,
            # Analyzer actions
            "unpack_fan_out_results": self._unpack_fan_out_results,
            "assemble_report": self._assemble_report,
            "normalize_judge_decision": self._normalize_judge_decision,
            "set_repair_attempted": self._set_repair_attempted,
            "prepend_frontmatter_v3": self._prepend_frontmatter_v3,
            "save_analyzer_result": self._save_analyzer_result,
            "mark_execution_failed": self._mark_execution_failed,
        }
        handler = handlers.get(action_name)
        if handler:
            return await handler(context)
        return await super().on_action(state_name, action_name, context)

    # ------------------------------------------------------------------
    # KV cache warmup
    # ------------------------------------------------------------------

    @staticmethod
    async def _mark_cache_pinned(context: Dict[str, Any]) -> Dict[str, Any]:
        """Mark the KV cache as pinned after warmup completes.

        The actual caching happens automatically via the serving backend's
        prefix cache. After the warmup agent processes the shared prefix,
        subsequent requests (fan-out sub-machines) will hit the cache.
        """
        logger.info("KV cache marked as pinned after warmup")
        context["cache_pinned"] = True
        return context

    # ------------------------------------------------------------------
    # Unpack fan-out results from parallel execution
    # ------------------------------------------------------------------

    async def _unpack_fan_out_results(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that all fan-out sections are present in context.

        The parallel `machine` block routes each sub-machine's output to
        context via `output_to_context`. This action just logs and validates.
        """
        # Debug: log context values for the section keys
        for key in ["narrative_lead", "author_uncertainties", "method_results",
                    "why_mechanism", "reproduction", "open_questions", "limits_confidence"]:
            val = context.get(key)
            if val is None:
                logger.warning("Context[%r] = None", key)
            elif isinstance(val, str):
                logger.info("Context[%r] = %d chars", key, len(val))
            else:
                logger.info("Context[%r] = type=%s val=%s", key, type(val).__name__, str(val)[:80])

        sections = [
            ("narrative_lead", context.get("narrative_lead")),
            ("author_uncertainties", context.get("author_uncertainties")),
            ("method_results", context.get("method_results")),
            ("why_mechanism", context.get("why_mechanism")),
            ("reproduction", context.get("reproduction")),
            ("open_questions", context.get("open_questions")),
            ("limits_confidence", context.get("limits_confidence")),
        ]
        for name, content in sections:
            if content:
                logger.info("Section %s: ~%d tokens", name, _approx_tokens(content))
            else:
                logger.warning("Section %s is empty", name)

        return context

    # ------------------------------------------------------------------
    # Non-agentic report assembly
    # ------------------------------------------------------------------

    async def _assemble_report(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Concatenate section outputs with proper markdown headers.

        No LLM call — just string formatting.
        """
        parts = [
            f"# {self._norm(context.get('title'))}",
        ]

        # Narrative lead
        narrative = self._norm(context.get("narrative_lead"))
        if narrative:
            parts.append(narrative)

        # Author Uncertainties — include if present and not already in narrative lead
        author_unc = self._norm(context.get("author_uncertainties"))
        if author_unc:
            already_in_narrative = narrative and "## Author Uncertainties" in narrative
            if not already_in_narrative:
                parts.append(author_unc)

        # Method + Key Results
        method = self._norm(context.get("method_results"))
        if method:
            parts.append(method)

        # Why It Works
        why = self._norm(context.get("why_mechanism"))
        if why:
            parts.append(why)

        # Reproduction (What's Specified + What's Missing)
        repro = self._norm(context.get("reproduction"))
        if repro:
            parts.append(repro)

        # Open Questions
        oq = self._norm(context.get("open_questions"))
        if oq:
            parts.append(oq)

        # Limits + Confidence + Next Checks
        limits = self._norm(context.get("limits_confidence"))
        if limits:
            parts.append(limits)

        context["report_body"] = "\n\n".join(parts)
        logger.info("Assembled report: ~%d tokens", _approx_tokens(context["report_body"]))
        return context

    # ------------------------------------------------------------------
    # Judge decision normalization
    # ------------------------------------------------------------------

    async def _normalize_judge_decision(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Parse judge output (JSON with scores) → PASS/REPAIR/FAIL.

        The judge outputs structured JSON. We extract the decision and
        preserve the raw output for repair feedback.
        """
        raw = self._norm(context.get("judge_decision_raw")) or ""

        # Try to parse as JSON first (structured rubric)
        try:
            parsed = json.loads(raw)
            scores = parsed.get("scores", {})
            feedback = parsed.get("feedback", {})

            # Store for repair use
            context["judge_scores"] = scores
            context["judge_feedback"] = feedback

            # Determine decision from scores
            num_missing = sum(1 for s in scores.values() if s == 1)
            min_score = min(scores.values()) if scores else 4

            if num_missing >= 3:
                context["judge_decision"] = "FAIL"
            elif min_score >= 3:
                context["judge_decision"] = "PASS"
            else:
                context["judge_decision"] = "REPAIR"

            # Compute weak sections for targeted repair
            context["weak_sections"] = [
                k for k, v in scores.items() if v < 3
            ]

            logger.info("Judge decision: %s (scores: %s, weak: %s)",
                        context["judge_decision"], scores, context["weak_sections"])
            return context
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Fallback: look for PASS/REPAIR/FAIL token in raw output
        match = re.search(r"\b(PASS|REPAIR|FAIL)\b", raw.strip().upper())
        context["judge_decision"] = match.group(1) if match else "REPAIR"
        logger.info("Judge decision (fallback): %s", context["judge_decision"])

        # Store raw for repair
        if "judge_feedback" not in context:
            context["judge_feedback"] = {}

        return context

    @staticmethod
    async def _set_repair_attempted(context: Dict[str, Any]) -> Dict[str, Any]:
        context["repair_attempted"] = True
        return context

    # ------------------------------------------------------------------
    # Frontmatter
    # ------------------------------------------------------------------

    async def _prepend_frontmatter_v3(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Build YAML frontmatter and prepend to report body.

        Frontmatter includes structured fields for machine readability:
        domain, task, key_metric, novelty_level, reproducibility, etc.
        """
        report_body = self._norm(context.get("report_body"))
        if not report_body:
            context["frontmatter"] = ""
            context["formatted_report"] = report_body or ""
            return context

        # Extract key info from report body for frontmatter
        key_metric = self._extract_key_metric(report_body)
        baseline_beaten = self._extract_baseline(report_body)

        # Resolve model name for provenance
        model_used = context.get("model") or context.get("last_model") or ""

        frontmatter = {
            "ver": "rpa3",
            "title": self._norm(context.get("title")),
            "arxiv_id": self._norm(context.get("arxiv_id")),
            "source_url": self._norm(context.get("source_url")),
            "model": model_used,
            "key_metric": key_metric,
            "baseline_beaten": baseline_beaten,
        }

        fm_text = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).strip()
        fm = f"---\n{fm_text}\n---\n\n"
        context["frontmatter"] = fm
        context["formatted_report"] = f"{fm}{report_body}"
        return context

    def _extract_key_metric(self, report_body: str) -> str:
        """Try to extract the key metric from Key Results section."""
        results_match = re.search(r"## Key Results\s*\n(.*?)(?=\n## |\Z)", report_body, re.DOTALL)
        if results_match:
            text = results_match.group(1).strip()
            # Extract first line with numbers (likely the key metric)
            for line in text.split("\n"):
                line = line.strip()
                if re.search(r"\d+\.?\d*", line) and not line.startswith("|") and not line.startswith("-"):
                    return line[:120]
        return ""

    def _extract_baseline(self, report_body: str) -> str:
        """Try to extract what baseline was beaten from the narrative lead."""
        lead_match = re.search(r"## What This Paper Did\s*\n(.*?)(?=\n## |\Z)", report_body, re.DOTALL)
        if lead_match:
            text = lead_match.group(1).strip()
            # Look for "beating" or "surpassing" patterns
            baseline_match = re.search(r"(?:beat|surpass|outperform|improve)?(?:d|s)?\s+([^.,;]+)", text, re.IGNORECASE)
            if baseline_match:
                return baseline_match.group(1).strip()[:80]
        return ""

    # ------------------------------------------------------------------
    # Save result
    # ------------------------------------------------------------------

    async def _save_analyzer_result(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Write final digest to disk."""
        execution_id = self._norm(context.get("execution_id"))
        formatted_report = context.get("formatted_report")
        title = self._norm(context.get("title"))
        arxiv_id = self._norm(context.get("arxiv_id"))

        result_path = None
        if formatted_report:
            try:
                self._data_dir.mkdir(parents=True, exist_ok=True)
                safe_id = arxiv_id.replace("/", "_") if arxiv_id else "unknown"
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{safe_id}_{_slugify_title(title)}_{timestamp}.md"
                file_path = self._data_dir / filename
                file_path.write_text(str(formatted_report), encoding="utf-8")
                result_path = str(file_path)
                logger.info("Digest written: %s (~%d tokens)",
                           result_path, _approx_tokens(formatted_report))
            except Exception as exc:
                logger.exception("Failed writing digest file")
                context["last_error"] = str(exc)

        context["result_path"] = result_path
        return context

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    async def _mark_execution_failed(self, context: Dict[str, Any]) -> Dict[str, Any]:
        execution_id = self._norm(context.get("execution_id"))
        arxiv_id = self._norm(context.get("arxiv_id"))
        error_str = self._norm(context.get("last_error")) or "unknown error"

        logger.error("Execution %s (arXiv: %s) failed: %s",
                     execution_id, arxiv_id, error_str)

        context["error"] = error_str
        return context

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(value: Any) -> Optional[str]:
        """Normalize to string, returning None for empty values."""
        if value is None:
            return None
        s = str(value).strip()
        return s if s else None
