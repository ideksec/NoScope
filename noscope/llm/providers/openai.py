"""OpenAI provider."""

from __future__ import annotations

import json
from typing import Any

import openai

from noscope.llm.base import (
    LLMResponse,
    Message,
    ToolCall,
    ToolSchema,
    Usage,
    prepare_structured_schema,
)

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_RETRIES = 4

# Normalize OpenAI finish reasons to the Anthropic-style stop reasons the
# agent loops branch on (they exit on "end_turn", never on OpenAI's "stop").
_STOP_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


class OpenAIProvider:
    """LLM provider using the OpenAI SDK."""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, max_retries=DEFAULT_MAX_RETRIES)
        self._default_model = model or DEFAULT_MODEL

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        model = model or self._default_model
        api_messages = _convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)
        if json_schema is not None:
            # Enforce the schema rather than discarding it with bare json_object.
            # strict=False: Pydantic schemas don't meet strict mode's
            # all-fields-required rule (optional fields carry defaults).
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": prepare_structured_schema(json_schema),
                    "strict": False,
                },
            }

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
            # OpenAI reports cached prompt tokens inside prompt_tokens (not in
            # addition to it), so subtract them out to mirror Anthropic's
            # "input_tokens is the uncached remainder" convention.
            details = getattr(response.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0 if details else 0
            usage = Usage(
                input_tokens=max(0, response.usage.prompt_tokens - cached),
                output_tokens=response.usage.completion_tokens,
                cache_read_input_tokens=cached,
            )

        finish_reason = choice.finish_reason or ""
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=_STOP_REASON_MAP.get(finish_reason, finish_reason),
        )


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
