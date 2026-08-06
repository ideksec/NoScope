"""Tests for search and discovery tools."""

from __future__ import annotations

import pytest

from noscope.tools.base import ToolContext
from noscope.tools.search import FindFilesTool, SearchFilesTool


@pytest.mark.asyncio
class TestSearchFilesTool:
    async def test_finds_matches_with_locations(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "app.py").write_text("import os\ndef main():\n    pass\n")
        (tool_context.workspace / "util.py").write_text("def helper():\n    return 1\n")
        tool = SearchFilesTool()
        result = await tool.execute({"pattern": r"def \w+\("}, tool_context)
        assert result.status == "ok"
        assert "app.py:2:" in result.display
        assert "util.py:1:" in result.display
        assert result.data["count"] == 2

    async def test_glob_filter(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "a.py").write_text("target\n")
        (tool_context.workspace / "b.txt").write_text("target\n")
        tool = SearchFilesTool()
        result = await tool.execute({"pattern": "target", "glob": "*.py"}, tool_context)
        assert "a.py" in result.display
        assert "b.txt" not in result.display

    async def test_no_matches(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "a.py").write_text("nothing here\n")
        tool = SearchFilesTool()
        result = await tool.execute({"pattern": "zzz"}, tool_context)
        assert result.status == "ok"
        assert result.data["count"] == 0

    async def test_invalid_regex(self, tool_context: ToolContext) -> None:
        tool = SearchFilesTool()
        result = await tool.execute({"pattern": "("}, tool_context)
        assert result.status == "error"

    async def test_skips_ignored_dirs(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / ".git").mkdir()
        (tool_context.workspace / ".git" / "config").write_text("target\n")
        (tool_context.workspace / "real.py").write_text("target\n")
        tool = SearchFilesTool()
        result = await tool.execute({"pattern": "target"}, tool_context)
        assert "real.py" in result.display
        assert ".git" not in result.display

    async def test_respects_max_results(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "many.txt").write_text("x\n" * 10)
        tool = SearchFilesTool()
        result = await tool.execute({"pattern": "x", "max_results": 3}, tool_context)
        assert result.data["count"] == 3
        assert result.data["truncated"] is True


@pytest.mark.asyncio
class TestFindFilesTool:
    async def test_glob_recursive(self, tool_context: ToolContext) -> None:
        (tool_context.workspace / "src").mkdir()
        (tool_context.workspace / "src" / "a.py").write_text("")
        (tool_context.workspace / "b.py").write_text("")
        (tool_context.workspace / "c.txt").write_text("")
        tool = FindFilesTool()
        result = await tool.execute({"pattern": "**/*.py"}, tool_context)
        assert "b.py" in result.display
        assert "src/a.py" in result.display
        assert "c.txt" not in result.display
        assert result.data["count"] == 2

    async def test_no_match(self, tool_context: ToolContext) -> None:
        tool = FindFilesTool()
        result = await tool.execute({"pattern": "**/*.rs"}, tool_context)
        assert result.status == "ok"
        assert result.data["count"] == 0
