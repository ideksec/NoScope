"""Tool base classes and context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from noscope.capabilities import Capability, CapabilityStore
from noscope.deadline import Deadline
from noscope.logging.events import EventLog


@dataclass
class ToolContext:
    """Shared context passed to every tool execution."""

    workspace: Path
    capabilities: CapabilityStore
    event_log: EventLog
    deadline: Deadline
    secrets: dict[str, str] = field(default_factory=dict)
    danger_mode: bool = False


@dataclass
class ToolResult:
    """Result from a tool execution."""

    status: Literal["ok", "error"]
    data: dict[str, Any] = field(default_factory=dict)
    display: str = ""

    @classmethod
    def ok(cls, display: str = "", **data: Any) -> ToolResult:
        return cls(status="ok", data=data, display=display)

    @classmethod
    def error(cls, message: str, **data: Any) -> ToolResult:
        return cls(status="error", data=data, display=message)


class Tool(ABC):
    """Abstract base class for all agent tools."""

    name: str
    description: str
    required_capability: Capability

    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """Return JSON Schema for the tool's parameters."""
        ...

    @abstractmethod
    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool with given arguments."""
        ...
