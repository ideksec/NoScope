"""Tests for conversation context management."""

from __future__ import annotations

from noscope.context import (
    MAX_TOOL_RESULT_CHARS,
    trim_history,
    truncate_tool_output,
)
from noscope.llm.base import Message, ToolCall


class TestTruncateToolOutput:
    def test_short_output_untouched(self) -> None:
        assert truncate_tool_output("hello") == "hello"

    def test_long_output_keeps_head_and_tail(self) -> None:
        text = "START" + ("x" * 50_000) + "END"
        out = truncate_tool_output(text)
        assert len(out) < len(text)
        # Errors usually live at the start or end, so both survive.
        assert out.startswith("START")
        assert out.endswith("END")
        assert "characters omitted" in out

    def test_respects_custom_limit(self) -> None:
        assert len(truncate_tool_output("y" * 1000, limit=100)) < 200

    def test_default_limit_is_applied(self) -> None:
        out = truncate_tool_output("z" * (MAX_TOOL_RESULT_CHARS * 3))
        assert len(out) < MAX_TOOL_RESULT_CHARS * 2


def _conversation(turns: int, filler: int = 5_000) -> list[Message]:
    """system + briefing, then `turns` of (assistant tool_call -> tool result)."""
    msgs: list[Message] = [
        Message(role="system", content="You are a builder."),
        Message(role="user", content="Build the thing."),
    ]
    for i in range(turns):
        msgs.append(
            Message(
                role="assistant",
                content=f"step {i}",
                tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={"path": "a.py"})],
            )
        )
        msgs.append(Message(role="tool", content="x" * filler, tool_call_id=f"c{i}"))
    return msgs


class TestTrimHistory:
    def test_short_history_untouched(self) -> None:
        msgs = _conversation(2, filler=10)
        assert trim_history(msgs) is msgs

    def test_long_history_is_trimmed(self) -> None:
        msgs = _conversation(200)
        trimmed = trim_history(msgs)
        assert len(trimmed) < len(msgs)

    def test_preserves_system_and_briefing(self) -> None:
        msgs = _conversation(200)
        trimmed = trim_history(msgs)
        assert trimmed[0].role == "system"
        assert trimmed[1].role == "user"
        assert trimmed[1].content == "Build the thing."

    def test_never_orphans_a_tool_result(self) -> None:
        # A tool result whose assistant turn was dropped is an API error, so the
        # kept tail must never begin with a tool message.
        for turns in range(20, 120, 7):
            trimmed = trim_history(_conversation(turns), max_chars=20_000)
            after_preamble = [m for m in trimmed[2:] if m.role != "user" or m.content]
            first_non_note = next(
                (m for m in after_preamble if "were dropped" not in (m.content or "")), None
            )
            if first_non_note is not None:
                assert first_non_note.role != "tool"

    def test_keeps_the_most_recent_turns(self) -> None:
        msgs = _conversation(50)
        msgs.append(Message(role="user", content="LATEST INSTRUCTION"))
        trimmed = trim_history(msgs, max_chars=20_000)
        assert trimmed[-1].content == "LATEST INSTRUCTION"

    def test_notes_the_elision(self) -> None:
        trimmed = trim_history(_conversation(100), max_chars=20_000)
        assert any("were dropped" in (m.content or "") for m in trimmed)

    def test_oversized_preamble_still_returns_valid_history(self) -> None:
        msgs = [
            Message(role="system", content="s" * 60_000),
            Message(role="user", content="u" * 60_000),
            Message(
                role="assistant",
                content="go",
                tool_calls=[ToolCall(id="c", name="t", arguments={})],
            ),
            Message(role="tool", content="result", tool_call_id="c"),
        ]
        trimmed = trim_history(msgs, max_chars=1_000)
        assert trimmed[0].role == "system"
        # Whatever survives must not start with an orphaned tool result.
        tail = trimmed[2:]
        if tail:
            assert tail[0].role != "tool"
