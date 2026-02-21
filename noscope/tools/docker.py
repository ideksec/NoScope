"""Docker sandbox for isolated command execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.redaction import redact_text
from noscope.tools.safety import check_command_safety

DOCKER_IMAGE = "python:3.12-slim"


class DockerSandbox:
    """Manages a Docker container for sandboxed execution."""

    def __init__(self, workspace: Path, image: str = DOCKER_IMAGE) -> None:
        self.workspace = workspace
        self.image = image
        self._container_id: str | None = None

    async def ensure_running(self) -> str:
        """Ensure the sandbox container is running. Returns container ID."""
        if self._container_id:
            return self._container_id

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "-d",
            "--rm",
            "-v",
            f"{self.workspace}:/workspace",
            "-w",
            "/workspace",
            self.image,
            "sleep",
            "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to start Docker sandbox: {stderr.decode()}")

        self._container_id = stdout.decode().strip()
        return self._container_id

    async def execute(
        self, command: str, timeout: int = 60, cwd: str = "/workspace"
    ) -> tuple[int, str, str]:
        """Execute a command inside the sandbox container."""
        container_id = await self.ensure_running()

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-w",
            cwd,
            container_id,
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            # Kill the exec, not the container
            return 124, "", f"Command timed out after {timeout}s"

        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def stop(self) -> None:
        """Stop and remove the sandbox container."""
        if self._container_id:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                self._container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            self._container_id = None


class DockerShellTool(Tool):
    """Shell tool that executes inside a Docker container."""

    name = "exec_command"
    description = "Execute a shell command inside a Docker sandbox"
    required_capability = Capability.SHELL_EXEC

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._sandbox = sandbox

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory inside container",
                    "default": "/workspace",
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
        timeout = min(args.get("timeout", 60), 300)
        cwd = args.get("cwd", "/workspace")

        denial = check_command_safety(command, danger_mode=context.danger_mode)
        if denial:
            return ToolResult.error(f"Command denied: {denial}")

        try:
            exit_code, stdout, stderr = await self._sandbox.execute(command, timeout, cwd)
        except RuntimeError as e:
            return ToolResult.error(str(e))

        stdout = redact_text(stdout, context.secrets)
        stderr = redact_text(stderr, context.secrets)

        display = stdout
        if stderr:
            display += f"\n[stderr]\n{stderr}"

        if exit_code != 0:
            return ToolResult(
                status="error",
                data={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
                display=f"Exit code {exit_code}\n{display}",
            )

        return ToolResult.ok(display=display, stdout=stdout, stderr=stderr, exit_code=exit_code)
