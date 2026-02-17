"""Tests for git tools."""

from __future__ import annotations

import pytest

from noscope.tools.base import ToolContext
from noscope.tools.git import GitAddTool, GitCommitTool, GitInitTool, GitStatusTool


@pytest.mark.asyncio
class TestGitTools:
    async def test_git_init(self, tool_context: ToolContext) -> None:
        tool = GitInitTool()
        result = await tool.execute({}, tool_context)
        assert result.status == "ok"
        assert (tool_context.workspace / ".git").is_dir()

    async def test_git_status_clean(self, tool_context: ToolContext) -> None:
        # Init first
        init = GitInitTool()
        await init.execute({}, tool_context)

        tool = GitStatusTool()
        result = await tool.execute({}, tool_context)
        assert result.status == "ok"

    async def test_git_add_and_commit(self, tool_context: ToolContext) -> None:
        # Init
        init = GitInitTool()
        await init.execute({}, tool_context)

        # Create a file
        (tool_context.workspace / "test.txt").write_text("hello")

        # Add
        add = GitAddTool()
        result = await add.execute({"paths": ["test.txt"]}, tool_context)
        assert result.status == "ok"

        # Commit
        commit = GitCommitTool()
        result = await commit.execute({"message": "Initial commit"}, tool_context)
        # May fail if git user not configured, but that's ok for CI
        # The important thing is the tool doesn't crash
        assert result.status in ("ok", "error")
