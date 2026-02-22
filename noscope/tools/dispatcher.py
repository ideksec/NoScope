"""Tool registry and capability-gated dispatcher."""

from __future__ import annotations

from typing import Any

from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.redaction import redact_structured

_MAX_LOG_STRING = 2_000
_OMIT_FIELDS = {"content", "stdout", "stderr"}


class ToolDispatcher:
    """Registers tools and dispatches calls with capability checks."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def dispatch(
        self, tool_name: str, args: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Dispatch a tool call with capability checking and event logging."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult.error(f"Unknown tool: {tool_name}")

        # Check capability
        if not context.capabilities.check(tool.required_capability):
            return ToolResult.error(
                f"Capability '{tool.required_capability.value}' not granted for tool '{tool_name}'"
            )

        # Log the call
        context.event_log.emit(
            phase=context.deadline.current_phase.value,
            event_type=f"tool.{tool_name}",
            summary=f"Calling {tool_name}",
            data={"tool": tool_name, "args": _sanitize_for_log(args, context)},
        )

        # Execute
        result = await tool.execute(args, context)

        # Log the result
        context.event_log.emit(
            phase=context.deadline.current_phase.value,
            event_type=f"tool.{tool_name}.result",
            summary=f"{tool_name} → {result.status}",
            data={"tool": tool_name},
            result={
                "status": result.status,
                "data": _sanitize_for_log(result.data, context),
            },
        )

        return result

    def to_schemas(self) -> list[dict[str, Any]]:
        """Convert all registered tools to LLM function/tool schemas."""
        schemas = []
        for tool in self._tools.values():
            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema(),
            })
        return schemas


def _sanitize_for_log(payload: Any, context: ToolContext) -> Any:
    """Redact secrets and trim bulky fields before logging."""
    redacted = redact_structured(payload, context.secrets)
    return _trim_payload(redacted)


def _trim_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        trimmed: dict[Any, Any] = {}
        for key, value in payload.items():
            if (
                key in _OMIT_FIELDS
                and isinstance(value, str)
            ):
                trimmed[key] = f"[omitted {len(value)} chars]"
            else:
                trimmed[key] = _trim_payload(value)
        return trimmed

    if isinstance(payload, list):
        return [_trim_payload(item) for item in payload]

    if isinstance(payload, tuple):
        return tuple(_trim_payload(item) for item in payload)

    if isinstance(payload, str) and len(payload) > _MAX_LOG_STRING:
        return payload[:_MAX_LOG_STRING] + "... [truncated]"

    return payload
