"""Tool registry and capability-gated dispatcher."""

from __future__ import annotations

from typing import Any

from noscope.tools.base import Tool, ToolContext, ToolResult


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
            data={"tool": tool_name, "args": args},
        )

        # Execute
        result = await tool.execute(args, context)

        # Log the result
        context.event_log.emit(
            phase=context.deadline.current_phase.value,
            event_type=f"tool.{tool_name}.result",
            summary=f"{tool_name} → {result.status}",
            data={"tool": tool_name},
            result={"status": result.status, "data": result.data},
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
