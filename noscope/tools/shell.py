"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.redaction import redact
from noscope.tools.safety import check_command_safety


def _clean_env() -> dict[str, str]:
    """Build a clean environment that doesn't leak NoScope's own venv."""
    env = os.environ.copy()
    # Remove virtual environment variables so workspace commands use system Python
    env.pop("VIRTUAL_ENV", None)
    # Clean PATH: remove any .venv/bin entries from NoScope's own environment
    if "PATH" in env:
        path_parts = env["PATH"].split(os.pathsep)
        cleaned = [p for p in path_parts if ".venv" not in p]
        env["PATH"] = os.pathsep.join(cleaned)
    return env


class ShellTool(Tool):
    name = "exec_command"
    description = "Execute a shell command within the workspace"
    required_capability = Capability.SHELL_EXEC

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory (relative to workspace)",
                    "default": ".",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 60,
                },
            },
            "required": ["command"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        command = args["command"]
        timeout = min(args.get("timeout", 60), 300)  # Cap at 5 minutes

        # Safety check
        denial = check_command_safety(command, danger_mode=context.danger_mode)
        if denial:
            return ToolResult.error(f"Command denied: {denial}")

        # Resolve working directory
        cwd = context.workspace
        if "cwd" in args and args["cwd"] != ".":
            cwd = context.workspace / args["cwd"]
            if not cwd.is_dir():
                return ToolResult.error(f"Working directory not found: {args['cwd']}")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=_clean_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            return ToolResult.error(f"Command timed out after {timeout}s")
        except OSError as e:
            return ToolResult.error(f"Failed to execute: {e}")

        stdout = redact(stdout_bytes.decode("utf-8", errors="replace"), context.secrets)
        stderr = redact(stderr_bytes.decode("utf-8", errors="replace"), context.secrets)
        exit_code = proc.returncode or 0

        # Truncate very long output
        max_len = 50_000
        if len(stdout) > max_len:
            stdout = stdout[:max_len] + "\n... (truncated)"
        if len(stderr) > max_len:
            stderr = stderr[:max_len] + "\n... (truncated)"

        display = stdout
        if stderr:
            display += f"\n[stderr]\n{stderr}"

        if exit_code != 0:
            return ToolResult(
                status="error",
                data={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
                display=f"Exit code {exit_code}\n{display}",
            )

        return ToolResult.ok(
            display=display,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
