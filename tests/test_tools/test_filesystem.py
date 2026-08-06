"""Tests for filesystem tools."""

from __future__ import annotations

import pytest

from noscope.tools.base import ToolContext
from noscope.tools.filesystem import (
    CreateDirectoryTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)


@pytest.mark.asyncio
class TestEditFileTool:
    async def test_unique_replacement(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "app.py").write_text("x = 1\ny = 2\n")
        tool = EditFileTool()
        result = await tool.execute(
            {"path": "app.py", "old_string": "x = 1", "new_string": "x = 42"}, tool_context
        )
        assert result.status == "ok"
        assert result.data["replacements"] == 1
        assert (tool_context.workspace / "app.py").read_text() == "x = 42\ny = 2\n"

    async def test_missing_old_string(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "app.py").write_text("hello\n")
        tool = EditFileTool()
        result = await tool.execute(
            {"path": "app.py", "old_string": "nope", "new_string": "x"}, tool_context
        )
        assert result.status == "error"
        assert "not found" in result.display

    async def test_ambiguous_match_rejected(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "app.py").write_text("a\na\n")
        tool = EditFileTool()
        result = await tool.execute(
            {"path": "app.py", "old_string": "a", "new_string": "b"}, tool_context
        )
        assert result.status == "error"
        assert "appears 2 times" in result.display
        # File is left untouched on an ambiguous match
        assert (tool_context.workspace / "app.py").read_text() == "a\na\n"

    async def test_replace_all(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "app.py").write_text("a\na\na\n")
        tool = EditFileTool()
        result = await tool.execute(
            {"path": "app.py", "old_string": "a", "new_string": "b", "replace_all": True},
            tool_context,
        )
        assert result.status == "ok"
        assert result.data["replacements"] == 3
        assert (tool_context.workspace / "app.py").read_text() == "b\nb\nb\n"

    async def test_nonexistent_file(self, tool_context: ToolContext) -> None:
        tool = EditFileTool()
        result = await tool.execute(
            {"path": "ghost.py", "old_string": "a", "new_string": "b"}, tool_context
        )
        assert result.status == "error"
        assert "not found" in result.display

    async def test_noop_edit_rejected(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "app.py").write_text("same\n")
        tool = EditFileTool()
        result = await tool.execute(
            {"path": "app.py", "old_string": "same", "new_string": "same"}, tool_context
        )
        assert result.status == "error"


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
        result = await tool.execute({"path": "new.txt", "content": "hello"}, tool_context)
        assert result.status == "ok"
        assert (tool_context.workspace / "new.txt").read_text() == "hello"

    async def test_write_creates_parents(self, tool_context: ToolContext) -> None:
        tool = WriteFileTool()
        result = await tool.execute({"path": "a/b/c.txt", "content": "nested"}, tool_context)
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
