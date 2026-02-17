"""Tests for filesystem tools."""

from __future__ import annotations

import pytest

from noscope.tools.base import ToolContext
from noscope.tools.filesystem import (
    CreateDirectoryTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)


@pytest.mark.asyncio
class TestReadFileTool:
    async def test_read_existing(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "test.txt").write_text("hello world")
        tool = ReadFileTool()
        result = await tool.execute({"path": "test.txt"}, tool_context)
        assert result.status == "ok"
        assert "hello world" in result.display

    async def test_read_nonexistent(self, tool_context: ToolContext) -> None:
        tool = ReadFileTool()
        result = await tool.execute({"path": "nope.txt"}, tool_context)
        assert result.status == "error"

    async def test_read_directory_fails(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "subdir").mkdir()
        tool = ReadFileTool()
        result = await tool.execute({"path": "subdir"}, tool_context)
        assert result.status == "error"


@pytest.mark.asyncio
class TestWriteFileTool:
    async def test_write_new(self, tool_context: ToolContext) -> None:
        tool = WriteFileTool()
        result = await tool.execute(
            {"path": "new.txt", "content": "hello"}, tool_context
        )
        assert result.status == "ok"
        assert (tool_context.workspace / "new.txt").read_text() == "hello"

    async def test_write_creates_parents(self, tool_context: ToolContext) -> None:
        tool = WriteFileTool()
        result = await tool.execute(
            {"path": "a/b/c.txt", "content": "nested"}, tool_context
        )
        assert result.status == "ok"
        assert (tool_context.workspace / "a/b/c.txt").read_text() == "nested"


@pytest.mark.asyncio
class TestListDirectoryTool:
    async def test_list_empty(self, tool_context: ToolContext) -> None:
        tool = ListDirectoryTool()
        result = await tool.execute({"path": "."}, tool_context)
        assert result.status == "ok"

    async def test_list_with_files(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "a.txt").write_text("a")
        (tool_context.workspace / "b.txt").write_text("b")
        tool = ListDirectoryTool()
        result = await tool.execute({"path": "."}, tool_context)
        assert "a.txt" in result.display
        assert "b.txt" in result.display


@pytest.mark.asyncio
class TestCreateDirectoryTool:
    async def test_create(self, tool_context: ToolContext) -> None:
        tool = CreateDirectoryTool()
        result = await tool.execute({"path": "newdir"}, tool_context)
        assert result.status == "ok"
        assert (tool_context.workspace / "newdir").is_dir()

    async def test_create_nested(self, tool_context: ToolContext) -> None:
        tool = CreateDirectoryTool()
        result = await tool.execute({"path": "a/b/c"}, tool_context)
        assert result.status == "ok"
        assert (tool_context.workspace / "a/b/c").is_dir()
