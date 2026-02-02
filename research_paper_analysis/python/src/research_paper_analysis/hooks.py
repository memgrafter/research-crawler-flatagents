"""Hooks for JSON validation and fixer routing in research_paper_analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from flatagents import LoggingHooks, get_logger

logger = get_logger(__name__)

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
        if action_name == "noop":
            return context
        return super().on_action(action_name, context)

    async def on_error(
        self,
        state_name: str,
        error: Exception,
        context: Dict[str, Any]
    ) -> Optional[str]:
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

    def _detect_rate_limit(self, error: Exception) -> Optional[tuple[str, Optional[int]]]:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None) if response else None
        headers = {}
        if response is not None and hasattr(response, "headers"):
            try:
                headers = {k.lower(): v for k, v in response.headers.items()}
            except Exception:
                headers = {}

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
