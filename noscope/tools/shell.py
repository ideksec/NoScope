"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.redaction import redact_text
from noscope.tools.safety import check_command_safety, resolve_workspace_path

_EXPLICIT_SENSITIVE_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "NOSCOPE_ANTHROPIC_API_KEY",
    "NOSCOPE_OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "HF_TOKEN",
    "SLACK_BOT_TOKEN",
}

_SENSITIVE_ENV_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?|PRIVATE[_-]?KEY|COOKIE|AUTH)(?:$|_)",
    re.IGNORECASE,
)

MAX_OUTPUT_LENGTH = 50_000


def build_execution_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an execution environment with sensitive values stripped."""
    env = dict(base_env) if base_env is not None else os.environ.copy()

    # Remove sensitive credentials from subprocess visibility.
    for key in list(env):
        if key in _EXPLICIT_SENSITIVE_ENV_KEYS or _SENSITIVE_ENV_KEY_PATTERN.search(key):
            env.pop(key, None)

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
        # Cap timeout: hard max 300s, and never more than 15% of remaining build time
        hard_cap = 300
        remaining = context.deadline.remaining()
        dynamic_cap = max(30, int(remaining * 0.15))  # At least 30s, at most 15% of remaining
        timeout = min(args.get("timeout", 60), hard_cap, dynamic_cap)

        # Safety check
        denial = check_command_safety(command, danger_mode=context.danger_mode)
        if denial:
            return ToolResult.error(f"Command denied: {denial}")

        # Resolve working directory
        cwd = context.workspace
        if "cwd" in args and args["cwd"] != ".":
            try:
                cwd = resolve_workspace_path(args["cwd"], context.workspace)
            except ValueError as e:
                return ToolResult.error(str(e))
            if not cwd.is_dir():
                return ToolResult.error(f"Working directory not found: {args['cwd']}")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=build_execution_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return ToolResult.error(f"Command timed out after {timeout}s")
        except OSError as e:
            return ToolResult.error(f"Failed to execute: {e}")

        stdout = redact_text(stdout_bytes.decode("utf-8", errors="replace"), context.secrets)
        stderr = redact_text(stderr_bytes.decode("utf-8", errors="replace"), context.secrets)
        exit_code = proc.returncode or 0

        # Truncate very long output
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"
        if len(stderr) > MAX_OUTPUT_LENGTH:
            stderr = stderr[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"

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
