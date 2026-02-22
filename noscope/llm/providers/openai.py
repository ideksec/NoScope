"""OpenAI provider."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import openai

from noscope.llm.base import (
    LLMResponse,
    Message,
    StreamChunk,
    ToolCall,
    ToolSchema,
    Usage,
)

DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider:
    """LLM provider using the OpenAI SDK."""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._default_model = model or DEFAULT_MODEL

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        model = model or self._default_model
        api_messages = _convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)
        if json_schema:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

        usage = Usage()
        if response.usage:
            usage = Usage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )

        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=choice.finish_reason or "",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        model = model or self._default_model
        api_messages = _convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                # Final chunk with usage
                if chunk.usage:
                    yield StreamChunk(
                        is_final=True,
                        usage=Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                        ),
                    )
                continue

            delta = chunk.choices[0].delta
            if delta.content:
                yield StreamChunk(delta_text=delta.content)


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert messages to OpenAI format."""
    api_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            tool_calls_api = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in msg.tool_calls
            ]
            api_messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": tool_calls_api,
                }
            )
        elif msg.role == "tool":
            api_messages.append(
                {
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id or "",
                }
            )
        else:
            api_messages.append({"role": msg.role, "content": msg.content})

    return api_messages


def _convert_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert tool schemas to OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]
