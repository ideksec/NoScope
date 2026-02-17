"""Anthropic Claude provider."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from noscope.llm.base import (
    LLMResponse,
    Message,
    StreamChunk,
    ToolCall,
    ToolSchema,
    Usage,
)

DEFAULT_MODEL = "claude-sonnet-4-20250514"


class AnthropicProvider:
    """LLM provider using the Anthropic SDK."""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._default_model = model or DEFAULT_MODEL

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        model = model or self._default_model
        system_msg, api_messages = _split_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "messages": api_messages,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = _convert_tools(tools)

        response = await self._client.messages.create(**kwargs)

        content = ""
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            stop_reason=response.stop_reason or "",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        model = model or self._default_model
        system_msg, api_messages = _split_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "messages": api_messages,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = _convert_tools(tools)

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            yield StreamChunk(delta_text=event.delta.text)
                        elif hasattr(event.delta, "partial_json"):
                            yield StreamChunk(delta_text=event.delta.partial_json)
                    elif event.type == "message_stop":
                        final_msg = await stream.get_final_message()
                        yield StreamChunk(
                            is_final=True,
                            usage=Usage(
                                input_tokens=final_msg.usage.input_tokens,
                                output_tokens=final_msg.usage.output_tokens,
                            ),
                        )


def _split_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Split system message from conversation messages for Anthropic API."""
    system = ""
    api_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system += msg.content + "\n"
        elif msg.role == "assistant":
            content: list[dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
            api_messages.append({"role": "assistant", "content": content or msg.content})
        elif msg.role == "tool":
            api_messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }
                ],
            })
        else:
            api_messages.append({"role": msg.role, "content": msg.content})

    return system.strip(), api_messages


def _convert_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert tool schemas to Anthropic tool format."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]
