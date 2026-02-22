"""Tests for the tool dispatcher."""

from __future__ import annotations

import json
from typing import Any

import pytest

from noscope.capabilities import Capability, CapabilityStore
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.dispatcher import ToolDispatcher


class FakeTool(Tool):
    name = "fake_tool"
    description = "A fake tool for testing"
    required_capability = Capability.SHELL_EXEC

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"msg": {"type": "string"}}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(display=f"got: {args.get('msg', '')}", msg=args.get("msg", ""))


class _DummyTool(Tool):
    """Tool that returns known secret-like content for redaction testing."""

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


class TestToolDispatcher:
    def test_register_and_get(self) -> None:
        dispatcher = ToolDispatcher()
        tool = FakeTool()
        dispatcher.register(tool)
        assert dispatcher.get_tool("fake_tool") is tool

    def test_get_unknown_returns_none(self) -> None:
        dispatcher = ToolDispatcher()
        assert dispatcher.get_tool("nonexistent") is None

    def test_register_all(self) -> None:
        dispatcher = ToolDispatcher()
        dispatcher.register_all([FakeTool()])
        assert dispatcher.get_tool("fake_tool") is not None

    def test_to_schemas(self) -> None:
        dispatcher = ToolDispatcher()
        dispatcher.register(FakeTool())
        schemas = dispatcher.to_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "fake_tool"
        assert schemas[0]["description"] == "A fake tool for testing"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self, tool_context: ToolContext) -> None:
        dispatcher = ToolDispatcher()
        result = await dispatcher.dispatch("nonexistent", {}, tool_context)
        assert result.status == "error"
        assert "Unknown tool" in result.display

    @pytest.mark.asyncio
    async def test_dispatch_success(self, tool_context: ToolContext) -> None:
        dispatcher = ToolDispatcher()
        dispatcher.register(FakeTool())
        result = await dispatcher.dispatch("fake_tool", {"msg": "hello"}, tool_context)
        assert result.status == "ok"
        assert "hello" in result.display

    @pytest.mark.asyncio
    async def test_dispatch_capability_denied(self, tool_context: ToolContext) -> None:
        tool_context.capabilities = CapabilityStore()
        dispatcher = ToolDispatcher()
        dispatcher.register(FakeTool())
        result = await dispatcher.dispatch("fake_tool", {"msg": "hello"}, tool_context)
        assert result.status == "error"
        assert "not granted" in result.display

    @pytest.mark.asyncio
    async def test_dispatch_logs_events(self, tool_context: ToolContext) -> None:
        dispatcher = ToolDispatcher()
        dispatcher.register(FakeTool())
        await dispatcher.dispatch("fake_tool", {"msg": "test"}, tool_context)
        events_path = tool_context.event_log.run_dir.events_path
        lines = events_path.read_text().strip().split("\n")
        assert len(lines) >= 2

    @pytest.mark.asyncio
    async def test_dispatch_redacts_secrets(self, tool_context: ToolContext) -> None:
        tool_context.secrets = {"api_key": "sk-secret-12345"}
        dispatcher = ToolDispatcher()
        dispatcher.register(FakeTool())
        await dispatcher.dispatch("fake_tool", {"msg": "key is sk-secret-12345"}, tool_context)
        events_path = tool_context.event_log.run_dir.events_path
        content = events_path.read_text()
        assert "sk-secret-12345" not in content

    @pytest.mark.asyncio
    async def test_capability_denial_logged(self, tool_context: ToolContext) -> None:
        tool_context.capabilities = CapabilityStore()
        dispatcher = ToolDispatcher()
        dispatcher.register(FakeTool())
        await dispatcher.dispatch("fake_tool", {}, tool_context)
        events_path = tool_context.event_log.run_dir.events_path
        content = events_path.read_text()
        assert "denied" in content.lower()


@pytest.mark.asyncio
async def test_dispatcher_redacts_and_omits_bulky_fields(tool_context: ToolContext) -> None:
    """Comprehensive test: secrets redacted, bulky fields omitted in logs."""
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
