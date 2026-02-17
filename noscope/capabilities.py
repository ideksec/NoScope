"""Capability model — gating agent actions behind explicit grants."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Capability(StrEnum):
    WORKSPACE_RW = "workspace_rw"
    SHELL_EXEC = "shell_exec"
    NET_HTTP = "net_http"
    GIT = "git"
    DOCKER = "docker"


class CapabilityRequest(BaseModel):
    """A single capability request from the planner."""

    cap: str  # Capability enum value or "secrets:<NAME>"
    why: str
    risk: Literal["low", "medium", "high"]


class CapabilityGrant(BaseModel):
    """User's decision on a capability request."""

    cap: str
    approved: bool


class CapabilityStore:
    """Holds granted capabilities and provides access checks."""

    def __init__(self, grants: list[CapabilityGrant] | None = None) -> None:
        self._grants: dict[str, bool] = {}
        if grants:
            for g in grants:
                self._grants[g.cap] = g.approved

    def grant(self, cap: str) -> None:
        self._grants[cap] = True

    def deny(self, cap: str) -> None:
        self._grants[cap] = False

    def check(self, cap: str | Capability) -> bool:
        """Check if a capability has been granted."""
        key = cap.value if isinstance(cap, Capability) else cap
        return self._grants.get(key, False)

    def get_secret(self, name: str) -> bool:
        """Check if a named secret has been granted."""
        return self.check(f"secrets:{name}")

    def to_grants(self) -> list[CapabilityGrant]:
        return [CapabilityGrant(cap=k, approved=v) for k, v in self._grants.items()]
