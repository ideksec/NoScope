"""Anthropic Claude provider."""

from __future__ import annotations

from typing import Any

import anthropic

from noscope.llm.base import (
    LLMResponse,
    Message,
    ToolCall,
    ToolSchema,
    Usage,
    prepare_structured_schema,
)

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8192
# Retries handled by the SDK (honors Retry-After, covers 429/5xx/timeouts/conn).
DEFAULT_MAX_RETRIES = 4


class AnthropicProvider:
    """LLM provider using the Anthropic SDK.

    Uses modern API features on current models — adaptive thinking, the effort
    control, prompt caching, and structured outputs — and transparently falls
    back to a plain request if a (typically older, user-overridden) model
    rejects any of them.
    """

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = "high",
        thinking: bool = True,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=DEFAULT_MAX_RETRIES)
        self._default_model = model or DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._effort = effort
        self._thinking = thinking
        # Set once a model rejects thinking/effort, so we don't pay the
        # failed-request tax on every subsequent call in the run. Structured
        # output is tracked separately — a rejected schema shouldn't cost us
        # thinking and effort for the whole run.
        self._modern_unsupported = False
        self._structured_unsupported = False

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        model = model or self._default_model
        system_blocks, api_messages = _split_messages(messages)

        base_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "messages": api_messages,
        }
        if system_blocks:
            base_kwargs["system"] = system_blocks
        if tools:
            base_kwargs["tools"] = _convert_tools(tools)

        want_format = json_schema is not None and not self._structured_unsupported
        response = await self._create_with_fallback(base_kwargs, effort, want_format, json_schema)

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

    async def _create_with_fallback(
        self,
        base_kwargs: dict[str, Any],
        effort: str | None,
        want_format: bool,
        json_schema: dict[str, Any] | None,
    ) -> Any:
        """Send the request, degrading modern params only as far as needed.

        Order of attempts: full modern (thinking + effort + format) → drop the
        structured format → drop thinking/effort entirely. Each degradation is
        remembered for the rest of the run so we pay the failed-request tax at
        most once per capability.
        """
        # Attempt 1: everything the model is believed to support.
        if not self._modern_unsupported:
            kwargs = self._modern_kwargs(base_kwargs, effort, want_format, json_schema)
            try:
                return await self._client.messages.create(**kwargs)
            except anthropic.BadRequestError:
                if want_format:
                    # Retry without the schema; keep thinking/effort.
                    self._structured_unsupported = True
                    try:
                        kwargs = self._modern_kwargs(base_kwargs, effort, False, None)
                        return await self._client.messages.create(**kwargs)
                    except anthropic.BadRequestError:
                        pass
                self._modern_unsupported = True

        # Attempt N: plain request — works on any model.
        return await self._client.messages.create(**base_kwargs)

    def _modern_kwargs(
        self,
        base_kwargs: dict[str, Any],
        effort: str | None,
        want_format: bool,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs = dict(base_kwargs)
        if self._thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        config: dict[str, Any] = {}
        chosen_effort = effort or self._effort
        if chosen_effort:
            config["effort"] = chosen_effort
        if want_format and json_schema is not None:
            config["format"] = {
                "type": "json_schema",
                "schema": prepare_structured_schema(json_schema),
            }
        if config:
            kwargs["output_config"] = config
        return kwargs


def _split_messages(messages: list[Message]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split system message from conversation messages for Anthropic API.

    Returns the system prompt as a list of text blocks (so a cache breakpoint
    can be attached) plus the conversation messages.
    """
    system_text = ""
    api_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_text += msg.content + "\n"
        elif msg.role == "assistant":
            content: list[dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
            api_messages.append({"role": "assistant", "content": content or msg.content})
        elif msg.role == "tool":
            api_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }
            )
        else:
            api_messages.append({"role": msg.role, "content": msg.content})

    system_blocks: list[dict[str, Any]] = []
    stripped = system_text.strip()
    if stripped:
        # Cache the system prompt — it is stable across every turn of an agent
        # loop, so this turns a full-price re-send into a ~0.1x cache read.
        system_blocks.append(
            {"type": "text", "text": stripped, "cache_control": {"type": "ephemeral"}}
        )
    return system_blocks, api_messages


def _convert_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert tool schemas to Anthropic tool format.

    A cache breakpoint on the final tool caches the whole (deterministic) tool
    list, which is re-sent unchanged on every turn.
    """
    converted: list[dict[str, Any]] = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]
    if converted:
        converted[-1]["cache_control"] = {"type": "ephemeral"}
    return converted
