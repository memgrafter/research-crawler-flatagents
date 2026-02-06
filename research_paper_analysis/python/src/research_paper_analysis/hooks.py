"""Hooks for JSON validation and fixer routing in research_paper_analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import litellm
litellm.drop_params = True

from flatmachines import LoggingHooks
from flatagents import get_logger, setup_logging

DEFAULT_LOG_DIR = Path(__file__).parent.parent.parent / "logs"

if "FLATAGENTS_LOG_LEVEL" not in os.environ:
    os.environ["FLATAGENTS_LOG_LEVEL"] = "INFO"
if "FLATAGENTS_LOG_FORMAT" not in os.environ:
    os.environ["FLATAGENTS_LOG_FORMAT"] = "standard"

# Use FLATAGENTS_LOG_DIR as our default log dir if set, but don't pop it —
# let the library create its baseline file handler.  configure_log_file()
# (called from on_machine_start) will replace it with our custom-named one.
_env_log_dir = os.environ.get("FLATAGENTS_LOG_DIR")
if _env_log_dir:
    DEFAULT_LOG_DIR = Path(_env_log_dir)

# Configure library loggers (each uses its own namespace now).
setup_logging()
logger = get_logger(__name__)

# Set up a root-level handler for our own app loggers (research_paper_analysis.*).
# The libraries no longer touch the root logger — that's our responsibility.
_root = logging.getLogger()
if not _root.handlers:
    _fmt = os.environ.get("FLATAGENTS_LOG_FORMAT", "standard")
    if _fmt == "standard":
        _formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    elif _fmt == "simple":
        _formatter = logging.Formatter("%(levelname)s - %(message)s")
    else:
        _formatter = logging.Formatter(_fmt, datefmt="%Y-%m-%d %H:%M:%S")
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_formatter)
    _root.addHandler(_sh)
    _root.setLevel(logging.INFO)


def configure_log_file(
    arxiv_id: str = "unknown",
    log_dir: Optional[Path] = None,
) -> Path:
    """Add a file handler with name: {date}_{time}_{arxiv_id}_{pid}.log.

    Safe to call multiple times — removes any previous file handler we added
    so the worker can upgrade its log name after claiming a paper.

    Returns the resolved log file path.
    """
    log_dir = log_dir or DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    safe_id = arxiv_id.replace("/", "_")
    pid = os.getpid()
    log_file = log_dir / f"{date_str}_{time_str}_{safe_id}_{pid}.log"

    fmt = os.environ.get("FLATAGENTS_LOG_FORMAT", "standard")
    if fmt == "standard":
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    elif fmt == "simple":
        formatter = logging.Formatter(
            "%(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # All three loggers: root (app), flatagents, flatmachines
    all_loggers = [
        logging.getLogger(),
        logging.getLogger("flatagents"),
        logging.getLogger("flatmachines"),
    ]

    # Remove any existing file handlers from all loggers
    for lgr in all_loggers:
        for h in list(lgr.handlers):
            if isinstance(h, logging.FileHandler):
                lgr.removeHandler(h)
                h.close()

    # Add our file handler to all loggers so everything goes to one file
    fh = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    fh.setFormatter(formatter)
    fh._rpa_log_file = True  # tag so we can find it later
    for lgr in all_loggers:
        lgr.addHandler(fh)

    logger.info(f"Logging to file: {log_file}")
    return log_file

DEFAULT_DB_PATH = str(
    Path(__file__).parent.parent.parent.parent.parent
    / "arxiv_crawler"
    / "data"
    / "arxiv.sqlite"
)
RATE_LIMIT_KEYS = {
    "minute": "rate_limit_minute_until",
    "hour": "rate_limit_hour_until",
    "day": "rate_limit_day_until",
}
RATE_LIMIT_DAY_RESET_HOUR = 16
RATE_LIMIT_DAY_TZ = ZoneInfo("America/Los_Angeles")


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
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self._db_path = os.environ.get("ARXIV_DB_PATH", DEFAULT_DB_PATH)

    def on_machine_start(self, context: Dict[str, Any]) -> Dict[str, Any]:
        context = super().on_machine_start(context)
        arxiv_id = str(
            context.get("paper_id")
            or context.get("arxiv_id")
            or context.get("worker_id")
            or "unknown"
        )
        configure_log_file(arxiv_id=arxiv_id)
        return context

    def _get_db_path(self) -> Optional[str]:
        return getattr(self, "db_path", None) or self._db_path

    def _get_conn(self) -> Optional[sqlite3.Connection]:
        db_path = self._get_db_path()
        if not db_path:
            return None
        if not Path(db_path).exists():
            return None
        if self._conn is None:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def on_action(self, action_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if action_name == "rate_limit_gate":
            return await self._rate_limit_gate(context)
        if action_name == "prepend_frontmatter":
            return await self._prepend_frontmatter(context)
        if action_name == "noop":
            return context
        return super().on_action(action_name, context)

    @staticmethod
    def _has_frontmatter(report: str) -> bool:
        if not isinstance(report, str):
            return False
        stripped = report.lstrip()
        if not stripped.startswith("---\n"):
            return False
        return stripped.find("\n---", 4) != -1

    @staticmethod
    def _get_config_dir() -> Path:
        return Path(__file__).parent.parent.parent.parent / "config"

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, float) and math.isnan(value):
            return ""
        return str(value).strip()

    def _load_model_profiles(self, config_dir: Path) -> Dict[str, Dict[str, str]]:
        """Load model profiles from config/profiles.yml."""
        profiles_path = config_dir / "profiles.yml"
        if not profiles_path.exists():
            return {}
        import yaml
        data = yaml.safe_load(profiles_path.read_text()) or {}
        return (data.get("data") or {}).get("model_profiles") or {}

    def _find_profiles_used(self, config_dir: Path) -> list[str]:
        """Find profile names referenced by flatagent configs in config/."""
        import yaml
        profiles = set()
        for path in config_dir.glob("*.yml"):
            if path.name == "profiles.yml":
                continue
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except Exception:
                continue
            if data.get("spec") != "flatagent":
                continue
            model_name = ((data.get("data") or {}).get("model") or "").strip()
            if model_name:
                profiles.add(model_name)
        return sorted(profiles)

    def _build_frontmatter(self, context: Dict[str, Any]) -> str:
        """Build YAML frontmatter for the report."""
        import yaml

        config_dir = self._get_config_dir()
        profiles_used = self._find_profiles_used(config_dir)
        model_profiles = self._load_model_profiles(config_dir)
        used_profiles = {
            name: model_profiles.get(name, {}) for name in profiles_used
        }

        arxiv_id = self._normalize_text(context.get("arxiv_id"))
        source_url = self._normalize_text(context.get("source_url"))
        if not source_url and arxiv_id:
            source_url = f"https://arxiv.org/abs/{arxiv_id}"

        citation_count = context.get("citation_count")
        if citation_count is None:
            citation_count = context.get("reference_count")

        frontmatter = {
            "title": self._normalize_text(context.get("title")),
            "arxiv_id": arxiv_id,
            "source_url": source_url,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "quality_score": context.get("quality_score"),
            "citation_count": citation_count,
            "model_profiles_used": profiles_used,
            "model_profiles": used_profiles,
        }

        return f"---\n{yaml.safe_dump(frontmatter, sort_keys=False).strip()}\n---\n\n"

    async def _prepend_frontmatter(self, context: Dict[str, Any]) -> Dict[str, Any]:
        formatted_report = context.get("formatted_report")
        if not formatted_report or not isinstance(formatted_report, str):
            return {"frontmatter": "", "formatted_report": formatted_report}

        if self._has_frontmatter(formatted_report):
            return {"frontmatter": "", "formatted_report": formatted_report}

        frontmatter = self._build_frontmatter(context)
        return {
            "frontmatter": frontmatter,
            "formatted_report": f"{frontmatter}{formatted_report}",
        }

    async def on_error(
        self,
        state_name: str,
        error: Exception,
        context: Dict[str, Any]
    ) -> Optional[str]:
        worker = context.get("worker_id", "-")
        arxiv = context.get("paper_id") or context.get("arxiv_id", "-")
        err_short = str(error)[:150].replace("\n", " ")
        status_code = self._extract_status_code(error)
        logger.error(
            "ERROR %s | worker=%s | arxiv=%s | %s | status_code=%s | %s",
            state_name, worker, arxiv, type(error).__name__, status_code, err_short,
        )
        headers = self._extract_headers(error)
        self._record_llm_error(
            context,
            error_text=str(error),
            error_type=type(error).__name__,
            status_code=status_code,
            headers=headers,
            source=state_name,
        )

        rate_limit = self._detect_rate_limit(error)
        if not rate_limit:
            return None

        cause, retry_after = rate_limit
        now = datetime.now(timezone.utc)
        until = self._compute_rate_limit_until(cause, now, retry_after)
        await self._store_rate_limit_state(context, cause, until)

        context["rate_limit_cause"] = cause
        context["rate_limit_until"] = until.isoformat()
        context["_rate_limit_next_state"] = state_name

        logger.warning(
            "Rate limit (%s) hit in %s; holding until %s",
            cause,
            state_name,
            until.isoformat(),
        )
        return "rate_limit_gate"

    def on_transition(self, from_state: str, to_state: str, context: Dict[str, Any]) -> str:
        agent_states = set(context.get("rate_limit_agent_states") or [])

        # Check for rate limit redirect from on_state_exit (structured agent error)
        redirect = context.pop("_rate_limit_redirect", None)
        if redirect:
            return super().on_transition(from_state, redirect, context)

        if from_state == "rate_limit_gate" and to_state == "rate_limit_resume":
            next_state = context.pop("_rate_limit_next_state", None)
            if next_state:
                return super().on_transition(from_state, next_state, context)
            return super().on_transition(from_state, to_state, context)

        if to_state in agent_states and from_state != "rate_limit_gate":
            context["_rate_limit_next_state"] = to_state
            return super().on_transition(from_state, "rate_limit_gate", context)

        return super().on_transition(from_state, to_state, context)

    def on_state_exit(
        self,
        state_name: str,
        context: Dict[str, Any],
        output: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        output = super().on_state_exit(state_name, context, output)
        
        # --- Concise per-state telemetry line ---
        self._log_state_summary(state_name, context, output)
        
        # Check for structured agent errors (from AgentResult.error)
        redirect = self._handle_agent_result_error(state_name, context, output)
        if redirect:
            # Store redirect for on_transition to handle
            context["_rate_limit_redirect"] = redirect
        
        self._capture_output_error(state_name, context, output)

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

    # ---- concise telemetry ------------------------------------------------

    @staticmethod
    def _log_state_summary(
        state_name: str,
        context: Dict[str, Any],
        output: Optional[Dict[str, Any]],
    ) -> None:
        """Emit a single INFO line per state exit:

        STATE state | worker=w | arxiv=id | status=ok/error | keys=a,b,c | error=…
        """
        worker = context.get("worker_id", "-")
        arxiv = context.get("paper_id") or context.get("arxiv_id", "-")

        # Determine status
        last_err = context.get("last_error")
        has_output = bool(output and isinstance(output, dict) and output)
        if last_err:
            status = "error"
        elif has_output:
            status = "ok"
        else:
            status = "empty"

        parts = [
            f"STATE {state_name}",
            f"worker={worker}",
            f"arxiv={arxiv}",
            f"status={status}",
        ]

        if has_output:
            keys = ",".join(sorted(output.keys())[:8])
            preview_len = sum(len(str(v)) for v in output.values())
            parts.append(f"keys={keys}")
            parts.append(f"size={preview_len}")

        if last_err:
            # Truncate long error messages
            err_short = str(last_err)[:120].replace("\n", " ")
            parts.append(f"error={err_short}")

        logger.info(" | ".join(parts))

    # -----------------------------------------------------------------------

    def _handle_agent_result_error(
        self,
        state_name: str,
        context: Dict[str, Any],
        output: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Handle structured errors from AgentResult.
        
        Returns state name to redirect to, or None to continue normally.
        """
        if not output or not isinstance(output, dict):
            return None
        
        # Check for AgentResult.error structure
        error = output.get("error")
        if not error or not isinstance(error, dict):
            return None
        
        error_code = error.get("code", "")
        error_type = error.get("type", "")
        error_message = error.get("message", "")
        status_code = error.get("status_code")
        
        # Record the error
        self._record_llm_error(
            context,
            error_text=error_message,
            error_type=error_type,
            status_code=status_code,
            headers=self._get_raw_headers_from_output(output),
            source=state_name,
        )
        
        # Check for rate limit
        if error_code != "rate_limit" and status_code != 429:
            return None
        
        # Detect rate limit cause from AgentResult.rate_limit
        rate_limit = output.get("rate_limit")
        cause, retry_after = self._detect_rate_limit_from_result(rate_limit, output)
        
        if not cause:
            cause = "minute"  # Default if we can't determine
        
        now = datetime.now(timezone.utc)
        until = self._compute_rate_limit_until(cause, now, retry_after)
        
        # Store state synchronously (will be awaited in gate)
        asyncio.create_task(self._store_rate_limit_state(context, cause, until))
        
        context["rate_limit_cause"] = cause
        context["rate_limit_until"] = until.isoformat()
        context["_rate_limit_next_state"] = state_name
        
        logger.warning(
            "Rate limit (%s) hit in %s; holding until %s",
            cause,
            state_name,
            until.isoformat(),
        )
        return "rate_limit_gate"
    
    def _detect_rate_limit_from_result(
        self,
        rate_limit: Optional[Dict[str, Any]],
        output: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Detect rate limit cause and retry_after from AgentResult.rate_limit.
        
        Returns (cause, retry_after) tuple.
        """
        if not rate_limit:
            # Fall back to raw headers
            raw_headers = self._get_raw_headers_from_output(output)
            if raw_headers:
                return self._detect_rate_limit_from_headers(raw_headers)
            return None, None
        
        retry_after = rate_limit.get("retry_after")
        
        # Check windows for exhausted limits
        windows = rate_limit.get("windows") or []
        for window in windows:
            if window.get("remaining") == 0:
                name = window.get("name", "")
                # Map window name to cause
                if "day" in name:
                    return "day", retry_after
                elif "hour" in name:
                    return "hour", retry_after
                else:
                    return "minute", retry_after
        
        # If limited but no exhausted window found, use retry_after to guess
        if rate_limit.get("limited"):
            if retry_after and retry_after >= 3600:
                return "hour", retry_after
            return "minute", retry_after
        
        return None, retry_after
    
    def _detect_rate_limit_from_headers(
        self,
        headers: Dict[str, str],
    ) -> Tuple[Optional[str], Optional[int]]:
        """Detect rate limit from raw headers (fallback)."""
        retry_after = self._parse_retry_after(headers.get("retry-after"))
        
        def header_zero(key: str) -> bool:
            return headers.get(key) == "0"
        
        if header_zero("x-ratelimit-remaining-tokens-day") or header_zero(
            "x-ratelimit-remaining-requests-day"
        ):
            return "day", retry_after
        if header_zero("x-ratelimit-remaining-tokens-hour") or header_zero(
            "x-ratelimit-remaining-requests-hour"
        ):
            return "hour", retry_after
        if header_zero("x-ratelimit-remaining-tokens-minute") or header_zero(
            "x-ratelimit-remaining-requests-minute"
        ):
            return "minute", retry_after
        
        return None, retry_after
    
    def _get_raw_headers_from_output(self, output: Dict[str, Any]) -> Dict[str, str]:
        """Extract raw_headers from AgentResult.provider_data."""
        provider_data = output.get("provider_data")
        if provider_data and isinstance(provider_data, dict):
            raw_headers = provider_data.get("raw_headers")
            if raw_headers and isinstance(raw_headers, dict):
                return raw_headers
        return {}

    def on_machine_end(self, context: Dict[str, Any], final_output: Dict[str, Any]) -> Dict[str, Any]:
        final_output = super().on_machine_end(context, final_output)
        if not isinstance(final_output, dict):
            return final_output

        llm_fields = (
            "llm_error",
            "llm_error_type",
            "llm_error_status_code",
            "llm_error_headers",
            "llm_error_source",
        )
        for key in llm_fields:
            if key in context and key not in final_output:
                final_output[key] = context[key]

        return final_output

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

    @staticmethod
    def _coerce_status_code(value: Optional[Any]) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _normalize_headers(raw_headers: Optional[Any]) -> Dict[str, str]:
        if raw_headers is None:
            return {}

        if isinstance(raw_headers, dict):
            items = raw_headers.items()
        elif hasattr(raw_headers, "items"):
            items = raw_headers.items()
        elif isinstance(raw_headers, (list, tuple)):
            items = raw_headers
        else:
            return {}

        normalized: Dict[str, str] = {}
        for key, value in items:
            if key is None:
                continue
            key_text = str(key).lower()
            if isinstance(value, (list, tuple)):
                value_text = ",".join(str(item) for item in value)
            else:
                value_text = str(value)
            normalized[key_text] = value_text

        return normalized

    def _extract_status_code(self, error: Exception) -> Optional[int]:
        for attr in ("status_code", "status", "http_status", "statusCode"):
            code = self._coerce_status_code(getattr(error, attr, None))
            if code is not None:
                return code

        response = getattr(error, "response", None)
        if response is not None:
            for attr in ("status_code", "status", "http_status", "statusCode"):
                code = self._coerce_status_code(getattr(response, attr, None))
                if code is not None:
                    return code
            if isinstance(response, dict):
                for key in ("status_code", "status", "http_status", "statusCode"):
                    code = self._coerce_status_code(response.get(key))
                    if code is not None:
                        return code

        match = re.search(r"\b([4-5]\d{2})\b", str(error))
        if match:
            return int(match.group(1))
        return None

    def _extract_headers(self, error: Exception) -> Dict[str, str]:
        response = getattr(error, "response", None)
        headers: Dict[str, str] = {}

        if response is not None:
            headers.update(self._normalize_headers(getattr(response, "headers", None)))
            if not headers and isinstance(response, dict):
                headers.update(self._normalize_headers(response.get("headers")))

        headers.update(self._normalize_headers(getattr(error, "headers", None)))
        return headers

    def _record_llm_error(
        self,
        context: Dict[str, Any],
        error_text: Optional[str],
        error_type: Optional[str] = None,
        status_code: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        source: Optional[str] = None,
    ) -> None:
        if error_text:
            context["llm_error"] = error_text
        if error_type:
            context["llm_error_type"] = error_type
        if status_code is not None:
            context["llm_error_status_code"] = status_code
        if headers:
            context["llm_error_headers"] = headers
        if source:
            context["llm_error_source"] = source

    def _capture_output_error(
        self,
        state_name: str,
        context: Dict[str, Any],
        output: Optional[Dict[str, Any]],
    ) -> None:
        if not output or not isinstance(output, dict):
            return
        if (
            "_error" not in output
            and "_error_status_code" not in output
            and "_error_headers" not in output
        ):
            return

        error_text = output.get("_error")
        error_type = output.get("_error_type")
        status_code = self._coerce_status_code(output.get("_error_status_code"))
        headers = self._normalize_headers(output.get("_error_headers"))
        self._record_llm_error(
            context,
            error_text=str(error_text) if error_text is not None else None,
            error_type=str(error_type) if error_type is not None else None,
            status_code=status_code,
            headers=headers,
            source=state_name,
        )

    def _detect_rate_limit(self, error: Exception) -> Optional[tuple[str, Optional[int]]]:
        status_code = self._extract_status_code(error)
        headers = self._extract_headers(error)

        retry_after = self._parse_retry_after(headers.get("retry-after"))

        def header_zero(key: str) -> bool:
            return headers.get(key) == "0"

        if header_zero("x-ratelimit-remaining-tokens-day") or header_zero(
            "x-ratelimit-remaining-requests-day"
        ):
            return "day", retry_after
        if header_zero("x-ratelimit-remaining-tokens-hour") or header_zero(
            "x-ratelimit-remaining-requests-hour"
        ):
            return "hour", retry_after
        if header_zero("x-ratelimit-remaining-tokens-minute") or header_zero(
            "x-ratelimit-remaining-requests-minute"
        ):
            return "minute", retry_after

        error_text = str(error).lower()
        if "rate limit" in error_text or "too many requests" in error_text or "429" in error_text:
            if "per day" in error_text or "daily" in error_text or "tokens per day" in error_text:
                return "day", retry_after
            if "per hour" in error_text or "hour" in error_text:
                return "hour", retry_after
            return "minute", retry_after

        if status_code == 429 or type(error).__name__ == "RateLimitError":
            return "minute", retry_after

        return None

    def _compute_rate_limit_until(
        self,
        cause: str,
        now: datetime,
        retry_after: Optional[int],
    ) -> datetime:
        if cause == "day":
            return self._compute_day_until(now)
        if cause == "hour":
            return self._compute_hour_until(now, retry_after)
        return self._compute_minute_until(now)

    @staticmethod
    def _compute_minute_until(now: datetime) -> datetime:
        return now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    @staticmethod
    def _compute_hour_until(now: datetime, retry_after: Optional[int]) -> datetime:
        if retry_after:
            return now + timedelta(seconds=retry_after)
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    @staticmethod
    def _compute_day_until(now: datetime) -> datetime:
        pacific_now = now.astimezone(RATE_LIMIT_DAY_TZ)
        reset_time = pacific_now.replace(
            hour=RATE_LIMIT_DAY_RESET_HOUR, minute=0, second=0, microsecond=0
        )
        if pacific_now >= reset_time:
            reset_time = reset_time + timedelta(days=1)
        return reset_time.astimezone(timezone.utc)

    async def _store_rate_limit_state(
        self,
        context: Dict[str, Any],
        cause: str,
        until: datetime,
    ) -> None:
        local = context.setdefault("_rate_limit_local", {})
        local[cause] = until.isoformat()

        key = RATE_LIMIT_KEYS.get(cause)
        conn = self._get_conn()
        if not conn or not key:
            return

        async with self._lock:
            try:
                row = conn.execute(
                    "SELECT value FROM crawler_state WHERE key = ?",
                    (key,),
                ).fetchone()
                existing = self._parse_timestamp(row[0]) if row else None
                if existing and existing >= until:
                    return
                conn.execute(
                    "INSERT OR REPLACE INTO crawler_state (key, value) VALUES (?, ?)",
                    (key, until.isoformat()),
                )
                conn.commit()
            except sqlite3.Error as exc:
                logger.warning("Failed to store rate limit state: %s", exc)

    async def _get_active_rate_limit_state(
        self,
        context: Dict[str, Any]
    ) -> Optional[tuple[str, datetime]]:
        now = datetime.now(timezone.utc)

        for cause in ("day", "hour", "minute"):
            until = await self._get_rate_limit_until_db(cause)
            if until is None:
                until = self._get_rate_limit_until_local(context, cause)

            if until is None:
                continue
            if until <= now:
                await self._clear_rate_limit_state(context, cause)
                continue
            return cause, until

        return None

    async def _get_rate_limit_until_db(self, cause: str) -> Optional[datetime]:
        key = RATE_LIMIT_KEYS.get(cause)
        conn = self._get_conn()
        if not conn or not key:
            return None

        async with self._lock:
            try:
                row = conn.execute(
                    "SELECT value FROM crawler_state WHERE key = ?",
                    (key,),
                ).fetchone()
            except sqlite3.Error as exc:
                logger.warning("Failed to load rate limit state: %s", exc)
                return None

        if not row:
            return None
        return self._parse_timestamp(row[0])

    def _get_rate_limit_until_local(self, context: Dict[str, Any], cause: str) -> Optional[datetime]:
        local = context.get("_rate_limit_local") or {}
        value = local.get(cause)
        if not value:
            return None
        return self._parse_timestamp(value)

    async def _clear_rate_limit_state(self, context: Dict[str, Any], cause: str) -> None:
        local = context.get("_rate_limit_local")
        if isinstance(local, dict):
            local.pop(cause, None)

        key = RATE_LIMIT_KEYS.get(cause)
        conn = self._get_conn()
        if not conn or not key:
            return

        async with self._lock:
            try:
                conn.execute("DELETE FROM crawler_state WHERE key = ?", (key,))
                conn.commit()
            except sqlite3.Error as exc:
                logger.warning("Failed to clear rate limit state: %s", exc)

    async def _rate_limit_gate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        suspended_worker = False

        while True:
            active = await self._get_active_rate_limit_state(context)
            if not active:
                break

            cause, until = active
            if cause in ("hour", "day"):
                if not suspended_worker:
                    await self._set_worker_status(context.get("worker_id"), "suspended")
                    suspended_worker = True
            elif suspended_worker:
                await self._set_worker_status(context.get("worker_id"), "active")
                suspended_worker = False

            wait_seconds = max(
                (until - datetime.now(timezone.utc)).total_seconds(),
                0,
            )
            if wait_seconds:
                logger.warning(
                    "Rate limit %s active; waiting %.0f seconds",
                    cause,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            else:
                await asyncio.sleep(0)

        if suspended_worker:
            await self._set_worker_status(context.get("worker_id"), "active")

        return context

    async def _set_worker_status(self, worker_id: Optional[str], status: str) -> None:
        if not worker_id:
            return

        conn = self._get_conn()
        if not conn:
            return

        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            try:
                conn.execute(
                    "UPDATE worker_registry SET status = ?, last_heartbeat = ? WHERE worker_id = ?",
                    (status, now, worker_id),
                )
                conn.commit()
            except sqlite3.Error as exc:
                logger.warning("Failed to update worker status: %s", exc)

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
