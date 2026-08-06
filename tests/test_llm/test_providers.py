"""Tests for LLM providers — mock SDK calls."""

from __future__ import annotations

from noscope.llm.base import LLMResponse, Message, ToolCall, ToolSchema, Usage


class TestMessageModel:
    def test_user_message(self) -> None:
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_assistant_with_tool_calls(self) -> None:
        msg = Message(
            role="assistant",
            content="Let me do that.",
            tool_calls=[ToolCall(id="tc1", name="read_file", arguments={"path": "test.txt"})],
        )
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "read_file"

    def test_tool_result(self) -> None:
        msg = Message(role="tool", content="file contents", tool_call_id="tc1")
        assert msg.tool_call_id == "tc1"


class TestLLMResponse:
    def test_text_response(self) -> None:
        r = LLMResponse(content="Hello!", usage=Usage(input_tokens=10, output_tokens=5))
        assert r.content == "Hello!"
        assert r.tool_calls == []
        assert r.usage.input_tokens == 10

    def test_tool_call_response(self) -> None:
        r = LLMResponse(
            tool_calls=[
                ToolCall(id="tc1", name="write_file", arguments={"path": "a.txt", "content": "hi"})
            ],
            stop_reason="tool_use",
        )
        assert len(r.tool_calls) == 1
        assert r.stop_reason == "tool_use"


class TestToolSchema:
    def test_schema(self) -> None:
        s = ToolSchema(
            name="read_file",
            description="Read a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        assert s.name == "read_file"


class TestProviderDefaults:
    def test_default_models_are_current(self) -> None:
        from noscope.llm.providers.anthropic import DEFAULT_MODEL as ANTHROPIC_DEFAULT
        from noscope.llm.providers.openai import DEFAULT_MODEL as OPENAI_DEFAULT

        # Guard against shipping retired model IDs again (the Feb 2026 defaults
        # were retired within four months)
        assert ANTHROPIC_DEFAULT == "claude-sonnet-5"
        assert OPENAI_DEFAULT == "gpt-5.6-terra"

    def test_openai_stop_reasons_normalized(self) -> None:
        from noscope.llm.providers.openai import _STOP_REASON_MAP

        # Agent loops exit on "end_turn"; OpenAI's finish reasons must map onto
        # the Anthropic-style vocabulary or the loop never terminates normally
        assert _STOP_REASON_MAP["stop"] == "end_turn"
        assert _STOP_REASON_MAP["tool_calls"] == "tool_use"
        assert _STOP_REASON_MAP["length"] == "max_tokens"
