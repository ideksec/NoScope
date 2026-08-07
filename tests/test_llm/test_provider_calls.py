"""Provider request/response shape tests against mocked SDK clients.

These never touch the network. They pin the wire shapes NoScope sends and the
parsing of what comes back — the layer most likely to break silently when a
provider SDK changes, and the one that costs real tokens to discover live.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noscope.llm.base import Message, ToolCall, ToolSchema
from noscope.llm.providers.anthropic import AnthropicProvider
from noscope.llm.providers.openai import OpenAIProvider


class _RecordingMessages:
    """Stands in for client.messages, capturing kwargs and returning a reply."""

    def __init__(self, reply: Any, fail_times: int = 0, fail_with: type | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply = reply
        self._fail_times = fail_times
        self._fail_with = fail_with

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._fail_times > 0:
            self._fail_times -= 1
            assert self._fail_with is not None
            raise self._fail_with.__new__(self._fail_with)
        return self._reply


def _anthropic_reply(
    blocks: list[Any] | None = None,
    stop_reason: str = "end_turn",
    cache_read: int = 0,
    cache_write: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks if blocks is not None else [SimpleNamespace(type="text", text="hi")],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
        stop_reason=stop_reason,
    )


def _provider(reply: Any, **kw: Any) -> tuple[AnthropicProvider, _RecordingMessages]:
    p = AnthropicProvider(api_key="test", **kw)
    msgs = _RecordingMessages(reply)
    p._client = SimpleNamespace(messages=msgs)  # type: ignore[assignment]
    return p, msgs


@pytest.mark.asyncio
class TestAnthropicRequestShape:
    async def test_system_prompt_is_cached_block(self) -> None:
        p, msgs = _provider(_anthropic_reply())
        await p.complete(
            [Message(role="system", content="be helpful"), Message(role="user", content="hi")]
        )
        sent = msgs.calls[0]
        assert sent["system"][0]["text"] == "be helpful"
        # A cache breakpoint on the stable system prompt is the main cost lever.
        assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}

    async def test_tools_are_converted_and_last_one_cached(self) -> None:
        p, msgs = _provider(_anthropic_reply())
        tools = [
            ToolSchema(name="a", description="da", parameters={"type": "object"}),
            ToolSchema(name="b", description="db", parameters={"type": "object"}),
        ]
        await p.complete([Message(role="user", content="hi")], tools=tools)
        sent_tools = msgs.calls[0]["tools"]
        assert [t["name"] for t in sent_tools] == ["a", "b"]
        assert "input_schema" in sent_tools[0]
        assert sent_tools[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in sent_tools[0]

    async def test_thinking_and_effort_are_sent(self) -> None:
        p, msgs = _provider(_anthropic_reply())
        await p.complete([Message(role="user", content="hi")], effort="high")
        sent = msgs.calls[0]
        assert sent["thinking"] == {"type": "adaptive"}
        assert sent["output_config"]["effort"] == "high"

    async def test_structured_output_schema_is_prepared(self) -> None:
        p, msgs = _provider(_anthropic_reply())
        schema = {"type": "object", "title": "X", "properties": {"a": {"type": "string"}}}
        await p.complete([Message(role="user", content="hi")], json_schema=schema)
        fmt = msgs.calls[0]["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        # additionalProperties is required by structured outputs; Pydantic omits it.
        assert fmt["schema"]["additionalProperties"] is False

    async def test_tool_results_become_user_messages(self) -> None:
        p, msgs = _provider(_anthropic_reply())
        await p.complete(
            [
                Message(role="user", content="go"),
                Message(
                    role="assistant",
                    content="calling",
                    tool_calls=[ToolCall(id="c1", name="t", arguments={"x": 1})],
                ),
                Message(role="tool", content="result text", tool_call_id="c1"),
            ]
        )
        sent = msgs.calls[0]["messages"]
        assert sent[1]["role"] == "assistant"
        assert any(b["type"] == "tool_use" for b in sent[1]["content"])
        assert sent[2]["role"] == "user"
        assert sent[2]["content"][0]["type"] == "tool_result"
        assert sent[2]["content"][0]["tool_use_id"] == "c1"


@pytest.mark.asyncio
class TestAnthropicResponseParsing:
    async def test_text_and_tool_use_are_parsed(self) -> None:
        blocks = [
            SimpleNamespace(type="text", text="thinking out loud"),
            SimpleNamespace(type="tool_use", id="c9", name="write_file", input={"path": "a"}),
        ]
        p, _ = _provider(_anthropic_reply(blocks=blocks, stop_reason="tool_use"))
        r = await p.complete([Message(role="user", content="hi")])
        assert r.content == "thinking out loud"
        assert r.tool_calls[0].id == "c9"
        assert r.tool_calls[0].name == "write_file"
        assert r.tool_calls[0].arguments == {"path": "a"}
        assert r.stop_reason == "tool_use"

    async def test_cache_tokens_are_captured(self) -> None:
        p, _ = _provider(_anthropic_reply(cache_read=900, cache_write=100))
        r = await p.complete([Message(role="user", content="hi")])
        assert r.usage.cache_read_input_tokens == 900
        assert r.usage.cache_creation_input_tokens == 100
        assert r.usage.input_tokens == 10


@pytest.mark.asyncio
class TestAnthropicDegradation:
    async def test_falls_back_when_modern_params_rejected(self) -> None:
        import anthropic

        p = AnthropicProvider(api_key="test")
        msgs = _RecordingMessages(
            _anthropic_reply(), fail_times=2, fail_with=anthropic.BadRequestError
        )
        p._client = SimpleNamespace(messages=msgs)  # type: ignore[assignment]

        schema = {"type": "object", "properties": {}}
        r = await p.complete([Message(role="user", content="hi")], json_schema=schema)

        # 1) full modern -> 400, 2) without schema -> 400, 3) plain request succeeds.
        assert len(msgs.calls) == 3
        assert "output_config" not in msgs.calls[2]
        assert "thinking" not in msgs.calls[2]
        assert r.content == "hi"

    async def test_degradation_is_remembered(self) -> None:
        import anthropic

        p = AnthropicProvider(api_key="test")
        # No schema here, so the fallback is just: modern -> plain (one failure).
        msgs = _RecordingMessages(
            _anthropic_reply(), fail_times=1, fail_with=anthropic.BadRequestError
        )
        p._client = SimpleNamespace(messages=msgs)  # type: ignore[assignment]
        await p.complete([Message(role="user", content="hi")])
        first_round = len(msgs.calls)
        assert first_round == 2

        await p.complete([Message(role="user", content="again")])
        # The second call must not re-pay the failed-request tax.
        assert len(msgs.calls) == first_round + 1


class _RecordingCompletions:
    def __init__(self, reply: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply = reply

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._reply


def _openai_reply(
    content: str = "hello",
    finish_reason: str = "stop",
    tool_calls: list[Any] | None = None,
    cached: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


def _openai_provider(reply: Any) -> tuple[OpenAIProvider, _RecordingCompletions]:
    p = OpenAIProvider(api_key="test")
    comps = _RecordingCompletions(reply)
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=comps))  # type: ignore[assignment]
    return p, comps


@pytest.mark.asyncio
class TestOpenAIProvider:
    async def test_stop_reason_is_normalized(self) -> None:
        # The agent loops exit on "end_turn"; OpenAI says "stop".
        p, _ = _openai_provider(_openai_reply(finish_reason="stop"))
        assert (await p.complete([Message(role="user", content="hi")])).stop_reason == "end_turn"

    async def test_tool_calls_finish_reason_maps(self) -> None:
        p, _ = _openai_provider(_openai_reply(finish_reason="tool_calls"))
        assert (await p.complete([Message(role="user", content="hi")])).stop_reason == "tool_use"

    async def test_tools_use_function_envelope(self) -> None:
        p, comps = _openai_provider(_openai_reply())
        await p.complete(
            [Message(role="user", content="hi")],
            tools=[ToolSchema(name="t", description="d", parameters={"type": "object"})],
        )
        tool = comps.calls[0]["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "t"

    async def test_json_schema_is_enforced_not_discarded(self) -> None:
        p, comps = _openai_provider(_openai_reply())
        await p.complete(
            [Message(role="user", content="hi")],
            json_schema={"type": "object", "properties": {}},
        )
        fmt = comps.calls[0]["response_format"]
        # The old code sent bare {"type": "json_object"} and dropped the schema.
        assert fmt["type"] == "json_schema"
        assert "schema" in fmt["json_schema"]

    async def test_cached_tokens_split_out_of_prompt_tokens(self) -> None:
        p, _ = _openai_provider(_openai_reply(cached=60))
        usage = (await p.complete([Message(role="user", content="hi")])).usage
        # OpenAI counts cached tokens inside prompt_tokens; we report the
        # uncached remainder separately to match Anthropic's convention.
        assert usage.input_tokens == 40
        assert usage.cache_read_input_tokens == 60

    async def test_tool_call_arguments_are_parsed(self) -> None:
        tc = SimpleNamespace(
            id="c1", function=SimpleNamespace(name="write_file", arguments='{"path": "a.py"}')
        )
        p, _ = _openai_provider(_openai_reply(content=None, tool_calls=[tc]))
        r = await p.complete([Message(role="user", content="hi")])
        assert r.tool_calls[0].arguments == {"path": "a.py"}

    async def test_malformed_tool_arguments_do_not_crash(self) -> None:
        tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="x", arguments="not json"))
        p, _ = _openai_provider(_openai_reply(content=None, tool_calls=[tc]))
        r = await p.complete([Message(role="user", content="hi")])
        assert r.tool_calls[0].arguments == {}
