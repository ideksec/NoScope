"""LLM provider protocol and shared message types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class Message:
    """A chat message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolCall:
    """A tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolSchema:
    """Schema for a tool the LLM can call."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    """Token usage for one call.

    ``input_tokens`` is the uncached prompt remainder; cached prompt tokens are
    reported separately so cost can credit the cache. Total prompt tokens =
    ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class LLMResponse:
    """Response from a non-streaming LLM call."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""


def prepare_structured_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic-generated JSON schema acceptable to structured outputs.

    Structured outputs require ``additionalProperties: false`` on every object;
    Pydantic omits it. This adds it recursively (into ``$defs`` too) without
    mutating the input, and strips presentation-only ``title`` keys.
    """

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: _walk(v) for k, v in node.items() if k != "title"}
            if out.get("type") == "object" and "additionalProperties" not in out:
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    result: dict[str, Any] = _walk(schema)
    return result


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse: ...
