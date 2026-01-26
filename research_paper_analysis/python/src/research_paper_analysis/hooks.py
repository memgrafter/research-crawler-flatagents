"""Hooks for JSON validation and fixer routing in research_paper_analysis."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from flatagents import LoggingHooks, get_logger

logger = get_logger(__name__)


class JsonValidationHooks(LoggingHooks):
    """Validate extractor JSON and route to fixer states when invalid."""

    _VALIDATION_STATES: Dict[str, Dict[str, Any]] = {
        "extract_abstract": {
            "required_keys": ["key_findings", "methodology", "contributions"],
            "source_context_key": "raw_abstract_analysis",
        },
        "extract_sections": {
            "required_keys": ["technical_details", "results"],
            "source_context_key": "raw_section_analysis",
        },
        "extract_summary": {
            "required_keys": ["summary"],
            "source_context_key": "raw_summary",
        },
        "extract_critique": {
            "required_keys": ["quality_score", "critique"],
            "source_context_key": "raw_critique",
        },
        "extract_report": {
            "required_keys": ["report"],
            "source_context_key": "raw_report",
        },
    }

    _FIX_STATES = {
        "fix_abstract": _VALIDATION_STATES["extract_abstract"]["required_keys"],
        "fix_sections": _VALIDATION_STATES["extract_sections"]["required_keys"],
        "fix_summary": _VALIDATION_STATES["extract_summary"]["required_keys"],
        "fix_critique": _VALIDATION_STATES["extract_critique"]["required_keys"],
        "fix_report": _VALIDATION_STATES["extract_report"]["required_keys"],
    }

    def __init__(self, log_level: int = logging.INFO):
        super().__init__(log_level=log_level)

    def on_state_exit(
        self,
        state_name: str,
        context: Dict[str, Any],
        output: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        output = super().on_state_exit(state_name, context, output)

        if state_name in self._VALIDATION_STATES:
            spec = self._VALIDATION_STATES[state_name]
            required_keys = spec["required_keys"]
            valid, error, raw = self._validate_output(output, required_keys)

            if not valid:
                context["json_fix_required"] = True
                context["json_fix_failed"] = False
                context["json_fix_state"] = state_name
                context["json_fix_error"] = error
                context["json_fix_raw"] = raw
                source_key = spec.get("source_context_key")
                context["json_fix_source"] = context.get(source_key, "") if source_key else ""
                logger.warning("Invalid JSON in %s: %s", state_name, error)
            else:
                self._clear_fix_flags(context)

        elif state_name in self._FIX_STATES:
            required_keys = self._FIX_STATES[state_name]
            valid, error, raw = self._validate_output(output, required_keys)

            if not valid:
                context["json_fix_failed"] = True
                context["json_fix_error"] = error
                context["json_fix_raw"] = raw
                logger.warning("Fixer JSON invalid in %s: %s", state_name, error)
            else:
                self._clear_fix_flags(context)

        return output

    @staticmethod
    def _validate_output(
        output: Optional[Dict[str, Any]],
        required_keys: list[str],
    ) -> Tuple[bool, str, str]:
        errors = []
        raw = None

        if output is None:
            errors.append("No output returned")
            return False, "; ".join(errors), "null"

        if not isinstance(output, dict):
            errors.append("Output is not a JSON object")
            return False, "; ".join(errors), str(output)

        if "_raw" in output:
            raw = output.get("_raw")
            errors.append("JSON parse failed")

        for key in required_keys:
            if key not in output:
                errors.append(f"Missing '{key}'")
            else:
                value = output.get(key)
                if value is None or (isinstance(value, str) and not value.strip()):
                    errors.append(f"Empty '{key}'")

        if "quality_score" in required_keys and "quality_score" in output:
            try:
                int(output.get("quality_score"))
            except (TypeError, ValueError):
                errors.append("quality_score is not an int")

        if errors:
            raw_value = raw if raw is not None else json.dumps(output, ensure_ascii=True)
            return False, "; ".join(errors), raw_value

        return True, "", ""

    @staticmethod
    def _clear_fix_flags(context: Dict[str, Any]) -> None:
        context["json_fix_required"] = False
        context["json_fix_failed"] = False
        context["json_fix_state"] = None
        context["json_fix_error"] = None
        context["json_fix_raw"] = None
        context["json_fix_source"] = None
