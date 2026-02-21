"""JSONL event log and run directory management."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noscope.tools.redaction import redact_structured


def _generate_run_id() -> str:
    """Generate a run ID in format YYYYMMDDTHHMMZ_<8hex>."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    hex_part = uuid.uuid4().hex[:8]
    return f"{ts}_{hex_part}"


class RunDir:
    """Manages the .noscope/runs/<run_id>/ directory structure."""

    def __init__(self, base: Path | None = None, run_id: str | None = None) -> None:
        self.run_id = run_id or _generate_run_id()
        base = base or Path(".noscope/runs")
        self.path = base / self.run_id
        self.path.mkdir(parents=True, exist_ok=True)

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"

    @property
    def plan_path(self) -> Path:
        return self.path / "plan.json"

    @property
    def contract_path(self) -> Path:
        return self.path / "contract.json"

    @property
    def capabilities_request_path(self) -> Path:
        return self.path / "capabilities_request.json"

    @property
    def capabilities_grant_path(self) -> Path:
        return self.path / "capabilities_grant.json"

    @property
    def handoff_path(self) -> Path:
        return self.path / "handoff.md"


class EventLog:
    """Append-only JSONL event log."""

    def __init__(self, run_dir: RunDir) -> None:
        self.run_dir = run_dir
        self._seq = 0
        fd = os.open(run_dir.events_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        self._file = os.fdopen(fd, "a", encoding="utf-8")
        # Best effort; some filesystems may not support chmod semantics.
        with suppress(OSError):
            os.chmod(run_dir.events_path, 0o600)

    def emit(
        self,
        phase: str,
        event_type: str,
        summary: str,
        data: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an event to the log. Returns the event dict."""
        self._seq += 1
        safe_summary = _sanitize_value(summary)
        event = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_dir.run_id,
            "phase": phase,
            "seq": self._seq,
            "type": event_type,
            "summary": safe_summary,
            "data": _sanitize_value(data or {}),
        }
        if result is not None:
            event["result"] = _sanitize_value(result)

        self._file.write(json.dumps(event) + "\n")
        self._file.flush()
        return event

    def close(self) -> None:
        """Flush and close the log file."""
        self._file.flush()
        self._file.close()


def _sanitize_value(value: Any) -> Any:
    """Apply automatic secret redaction to event payloads."""
    return redact_structured(value, {})
