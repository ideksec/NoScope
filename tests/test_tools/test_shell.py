"""Tests for shell tool."""

from __future__ import annotations

import pytest

from noscope.tools.base import ToolContext
from noscope.tools.shell import ShellTool, build_execution_env


@pytest.mark.asyncio
class TestShellTool:
    async def test_echo(self, tool_context: ToolContext) -> None:
        tool = ShellTool()
        result = await tool.execute({"command": "echo hello"}, tool_context)
        assert result.status == "ok"
        assert "hello" in result.display

    async def test_exit_code(self, tool_context: ToolContext) -> None:
        tool = ShellTool()
        result = await tool.execute({"command": "exit 1"}, tool_context)
        assert result.status == "error"
        assert result.data["exit_code"] == 1

    async def test_timeout(self, tool_context: ToolContext) -> None:
        tool = ShellTool()
        result = await tool.execute({"command": "sleep 10", "timeout": 1}, tool_context)
        assert result.status == "error"
        assert "timed out" in result.display.lower()

    async def test_timeout_kills_whole_process_tree(self, tool_context: ToolContext) -> None:
        # A timed-out command must not leave orphans behind. Killing only the
        # shell leaves grandchildren running, still holding the output pipes —
        # which also made the timeout path block for the command's full runtime.
        import time

        started = time.monotonic()
        result = await ShellTool().execute(
            # The subshell forces a grandchild: `sh -c` cannot exec-optimize it.
            {"command": "(sleep 30) & wait", "timeout": 1},
            tool_context,
        )
        elapsed = time.monotonic() - started

        assert result.status == "error"
        assert "timed out" in result.display.lower()
        # Generous, but far below the 30s the orphan would have kept us waiting.
        assert elapsed < 10, f"timeout path blocked on an orphan for {elapsed:.1f}s"

    async def test_denied_command(self, tool_context: ToolContext) -> None:
        tool = ShellTool()
        result = await tool.execute({"command": "sudo rm -rf /"}, tool_context)
        assert result.status == "error"
        assert "denied" in result.display.lower()

    async def test_cwd(self, tool_context: ToolContext) -> None:
        subdir = tool_context.workspace / "subdir"
        subdir.mkdir()
        tool = ShellTool()
        result = await tool.execute({"command": "pwd", "cwd": "subdir"}, tool_context)
        assert result.status == "ok"
        assert "subdir" in result.display

    async def test_cwd_outside_workspace_denied(self, tool_context: ToolContext) -> None:
        tool = ShellTool()
        result = await tool.execute(
            {"command": "pwd", "cwd": "/"},
            tool_context,
        )
        assert result.status == "error"
        assert "outside workspace" in result.display.lower()

    async def test_secret_redaction(self, tool_context: ToolContext) -> None:
        tool_context.secrets = {"MY_SECRET": "supersecret123"}
        tool = ShellTool()
        result = await tool.execute({"command": "echo supersecret123"}, tool_context)
        assert "supersecret123" not in result.display
        assert "[REDACTED:MY_SECRET]" in result.display


def test_build_execution_env_strips_sensitive_values() -> None:
    env = {
        "PATH": "/usr/bin:/tmp/project/.venv/bin:/bin",
        "OPENAI_API_KEY": "sk-test-secret",
        "GITHUB_TOKEN": "ghp_verysecret",
        "HOME": "/home/test",
    }
    cleaned = build_execution_env(env)
    assert "OPENAI_API_KEY" not in cleaned
    assert "GITHUB_TOKEN" not in cleaned
    assert "/tmp/project/.venv/bin" not in cleaned["PATH"]
    assert cleaned["HOME"] == "/home/test"
