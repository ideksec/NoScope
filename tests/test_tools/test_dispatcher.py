"""Tests for tool dispatcher logging hygiene."""

from __future__ import annotations

import json
from typing import Any

import pytest

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.dispatcher import ToolDispatcher


class _DummyTool(Tool):
    name = "dummy_tool"
    description = "Dummy tool for dispatcher tests"
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"content": {"type": "string"}}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(
            display="done",
            content=args.get("content", ""),
            stdout="Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456",
        )


@pytest.mark.asyncio
async def test_dispatcher_redacts_and_omits_bulky_fields(tool_context: ToolContext) -> None:
    tool_context.secrets = {"OPENAI_API_KEY": "supersecret123"}
    dispatcher = ToolDispatcher()
    dispatcher.register(_DummyTool())

    payload = {"content": "A" * 5000, "token": "supersecret123"}
    result = await dispatcher.dispatch("dummy_tool", payload, tool_context)
    assert result.status == "ok"

    events_path = tool_context.event_log.run_dir.events_path
    lines = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2

    call_event = lines[0]
    result_event = lines[1]

    assert call_event["data"]["args"]["content"].startswith("[omitted ")
    assert call_event["data"]["args"]["token"] == "[REDACTED:OPENAI_API_KEY]"
    assert result_event["result"]["data"]["content"].startswith("[omitted ")
    assert result_event["result"]["data"]["stdout"].startswith("[omitted ")

    raw = events_path.read_text(encoding="utf-8")
    assert "supersecret123" not in raw
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in raw
