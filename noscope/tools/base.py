"""Tool base classes and context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from noscope.capabilities import Capability, CapabilityStore
from noscope.conflicts import WriteLedger
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
    # Who is acting. Build agents each get a copy of the context carrying their
    # own id, so writes can be attributed. The ledger itself stays shared.
    agent_id: str = "main"
    write_ledger: WriteLedger | None = None


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


def tool_summary(name: str, args: dict[str, Any]) -> str:
    """Create a brief human-readable summary of a tool call."""
    if name == "write_file":
        return f"writing {args.get('path', '?')}"
    if name == "edit_file":
        return f"editing {args.get('path', '?')}"
    if name == "read_file":
        return f"reading {args.get('path', '?')}"
    if name == "exec_command":
        cmd = args.get("command", "")
        return str(cmd[:80]) if len(cmd) <= 80 else str(cmd[:77]) + "..."
    if name == "list_directory":
        return f"listing {args.get('path', '.')}"
    if name == "search_files":
        return f"searching /{args.get('pattern', '')}/"
    if name == "find_files":
        return f"finding {args.get('pattern', '')}"
    if name == "create_directory":
        return f"creating {args.get('path', '?')}"
    if name in ("git_init", "git_status", "git_add", "git_commit", "git_diff"):
        return name.replace("_", " ")
    return name


def record_write(context: ToolContext, rel_path: str, content: str) -> str:
    """Record a file write and return a warning to append to the tool output.

    Returns "" in the common case. When another agent wrote the same file
    first, the warning goes straight back into the writing agent's context so
    it can re-read before continuing, and the conflict is logged for the report.
    """
    if context.write_ledger is None:
        return ""
    conflict = context.write_ledger.record(rel_path, context.agent_id, content)
    if conflict is None:
        return ""

    context.event_log.emit(
        phase=context.deadline.current_phase.value,
        event_type="write.conflict",
        summary=conflict.describe(),
        data={
            "path": conflict.path,
            "previous_agent": conflict.previous_agent,
            "current_agent": conflict.current_agent,
        },
    )
    return (
        f"\n[warning] {conflict.previous_agent} also wrote {rel_path}. You may have "
        "discarded their work — read the file back before making further edits, "
        "and prefer edit_file over write_file on shared files."
    )
