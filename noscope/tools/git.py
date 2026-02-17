"""Git tools — init, status, add, commit, diff."""

from __future__ import annotations

import asyncio
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult


async def _run_git(
    args: list[str], cwd: str, timeout: int = 30
) -> tuple[int, str, str]:
    """Run a git command and return (exit_code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode or 0,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


class GitInitTool(Tool):
    name = "git_init"
    description = "Initialize a git repository in the workspace"
    required_capability = Capability.GIT

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        code, stdout, stderr = await _run_git(["init"], str(context.workspace))
        if code != 0:
            return ToolResult.error(f"git init failed: {stderr}")
        return ToolResult.ok(display=stdout.strip())


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the working tree status"
    required_capability = Capability.GIT

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        code, stdout, stderr = await _run_git(["status", "--short"], str(context.workspace))
        if code != 0:
            return ToolResult.error(f"git status failed: {stderr}")
        return ToolResult.ok(display=stdout.strip() or "(clean)")


class GitAddTool(Tool):
    name = "git_add"
    description = "Stage files for commit"
    required_capability = Capability.GIT

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to stage (use '.' for all)",
                },
            },
            "required": ["paths"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        paths = args["paths"]
        code, stdout, stderr = await _run_git(["add", *paths], str(context.workspace))
        if code != 0:
            return ToolResult.error(f"git add failed: {stderr}")
        return ToolResult.ok(display=f"Staged: {', '.join(paths)}")


class GitCommitTool(Tool):
    name = "git_commit"
    description = "Create a git commit"
    required_capability = Capability.GIT

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
            },
            "required": ["message"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        code, stdout, stderr = await _run_git(
            ["commit", "-m", args["message"]], str(context.workspace)
        )
        if code != 0:
            return ToolResult.error(f"git commit failed: {stderr}")
        return ToolResult.ok(display=stdout.strip())


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show changes in the working tree"
    required_capability = Capability.GIT

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        code, stdout, stderr = await _run_git(["diff"], str(context.workspace))
        if code != 0:
            return ToolResult.error(f"git diff failed: {stderr}")
        return ToolResult.ok(display=stdout.strip() or "(no changes)")
