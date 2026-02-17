"""Spec input models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class AcceptanceCheck(BaseModel):
    """A single acceptance criterion from the spec."""

    raw: str
    is_cmd: bool = False
    command: str | None = None

    @classmethod
    def from_string(cls, s: str) -> AcceptanceCheck:
        s = s.strip()
        if s.lower().startswith("cmd:"):
            cmd = s[4:].strip()
            return cls(raw=s, is_cmd=True, command=cmd)
        return cls(raw=s)


class SpecInput(BaseModel):
    """Parsed and validated spec input."""

    name: str
    timebox: str
    timebox_seconds: int = 0
    constraints: list[str] = []
    acceptance: list[AcceptanceCheck] = []
    body: str = ""

    # Optional fields
    stack_prefs: list[str] | None = None
    repo_mode: Literal["new", "existing"] = "new"
    risk_policy: Literal["strict", "default", "permissive"] = "default"

    @field_validator("timebox_seconds", mode="before")
    @classmethod
    def parse_timebox(cls, v: int, info: object) -> int:
        if v != 0:
            return v
        return 0  # Will be set by parser

    def model_post_init(self, __context: object) -> None:
        if self.timebox_seconds == 0:
            self.timebox_seconds = _parse_duration(self.timebox)


def _parse_duration(s: str) -> int:
    """Parse a duration string like '30m', '1h', '1h30m', '90s' into seconds."""
    s = s.strip().lower()
    total = 0
    current = ""
    for c in s:
        if c.isdigit():
            current += c
        elif c == "h":
            if not current:
                raise ValueError(f"Invalid duration: {s}")
            total += int(current) * 3600
            current = ""
        elif c == "m":
            if not current:
                raise ValueError(f"Invalid duration: {s}")
            total += int(current) * 60
            current = ""
        elif c == "s":
            if not current:
                raise ValueError(f"Invalid duration: {s}")
            total += int(current)
            current = ""
        else:
            raise ValueError(f"Invalid duration character: {c}")

    # Bare number defaults to minutes
    if current:
        total += int(current) * 60

    if total <= 0:
        raise ValueError(f"Duration must be positive: {s}")

    return total
