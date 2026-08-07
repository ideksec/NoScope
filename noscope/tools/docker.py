"""Docker sandbox for isolated command execution.

The container has NO host filesystem mounts. Workspace files are copied in
at start and copied out at stop, so the agent has full freedom inside the
container with zero risk to the host.
"""

from __future__ import annotations

import asyncio
import base64
import posixpath
import re
import shutil
from pathlib import Path
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult, record_write
from noscope.tools.redaction import redact_text
from noscope.tools.safety import check_command_safety
from noscope.tools.shell import kill_process_group

DOCKER_IMAGE = "python:3.12-slim"
DOCKER_MEMORY_LIMIT = "1g"
DOCKER_CPU_LIMIT = "2.0"

# Container paths are interpolated into shell strings, so restrict them to a
# safe allowlist: no shell metacharacters, no absolute paths, no traversal.
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def safe_container_path(rel_path: str) -> str:
    """Validate a workspace-relative path for safe use in a container command.

    Rejects absolute paths, ``..`` traversal, and anything outside a safe
    character set — closing the path-injection hole where a crafted path could
    escape /workspace or inject a shell command. Returns the normalized path.
    """
    rel = (rel_path or "").strip()
    if rel in ("", ".", "./"):
        return "."
    if rel.startswith("/"):
        raise ValueError(f"Absolute paths are not allowed: {rel_path!r}")
    if rel.startswith("./"):  # strip a single leading "./", nothing more
        rel = rel[2:]
    if not _SAFE_PATH_RE.match(rel):
        raise ValueError(f"Unsafe characters in path: {rel_path!r}")
    if ".." in rel.split("/"):
        raise ValueError(f"Path traversal is not allowed: {rel_path!r}")
    return rel


def build_write_command(rel_path: str, content: str) -> str:
    """Build a shell command that writes content to a container path safely.

    Uses base64 so arbitrary file content — backslashes, quotes, the heredoc
    marker, anything — round-trips exactly, instead of the old heredoc that
    doubled backslashes and could be truncated by the content itself.
    """
    path = safe_container_path(rel_path)
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    parent = posixpath.dirname(path)
    mkdir = f"mkdir -p '/workspace/{parent}' && " if parent else ""
    return f"{mkdir}printf %s '{b64}' | base64 -d > '/workspace/{path}'"


async def preflight_docker(timeout: float = 15.0) -> str | None:
    """Check that ``--sandbox`` can actually work. Returns an error, or None.

    Run this *before* the first model call. A sandbox that only fails once the
    build is underway wastes the timebox and the tokens already spent, and the
    raw daemon error ("Cannot connect to the Docker daemon at
    unix:///var/run/docker.sock") doesn't tell the user what to do.
    """
    if shutil.which("docker") is None:
        return (
            "--sandbox needs Docker, but the `docker` command was not found.\n"
            "  Fix: install Docker Desktop or the Docker Engine, or drop --sandbox\n"
            "       to run on the host (commands are still safety-filtered)."
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            "--format",
            "{{.ServerVersion}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await kill_process_group(proc)
        return (
            f"The Docker daemon did not respond within {timeout:.0f}s.\n"
            "  Fix: check that Docker is running, then retry."
        )
    except OSError as e:
        return f"Could not run docker: {e}"

    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
        first = detail[0] if detail else "unknown error"
        return (
            "The `docker` command exists but the daemon is not reachable.\n"
            "  Fix: start Docker (Docker Desktop, or `sudo systemctl start docker`),\n"
            "       or drop --sandbox to run on the host.\n"
            f"  Daemon said: {first}"
        )

    return None


class DockerSandbox:
    """Manages a fully isolated Docker container.

    The host workspace is NEVER bind-mounted. Instead:
    1. On start: workspace files are copied INTO the container via `docker cp`
    2. The agent runs with full root inside the container — no restrictions
    3. On stop: modified files are copied OUT of the container back to host
    """

    def __init__(self, workspace: Path, image: str = DOCKER_IMAGE) -> None:
        self.workspace = workspace
        self.image = image
        self._container_id: str | None = None

    async def ensure_running(self) -> str:
        """Start the sandbox container (no host mounts). Returns container ID."""
        if self._container_id:
            return self._container_id

        # Create container with NO volume mounts — fully isolated
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "-d",
            "--rm",
            "-w",
            "/workspace",
            "--memory",
            DOCKER_MEMORY_LIMIT,
            "--cpus",
            DOCKER_CPU_LIMIT,
            "--cap-drop=ALL",
            "--cap-add=CHOWN",
            "--cap-add=DAC_OVERRIDE",
            "--cap-add=FOWNER",
            "--cap-add=SETGID",
            "--cap-add=SETUID",
            "--cap-add=NET_BIND_SERVICE",
            "--security-opt",
            "no-new-privileges",
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

        # Create /workspace inside the container
        await self._exec_raw("mkdir -p /workspace")

        # Copy workspace files into the container (if any exist)
        if any(self.workspace.iterdir()):
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "cp",
                f"{self.workspace}/.",
                f"{self._container_id}:/workspace/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, cp_err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to copy workspace into container: {cp_err.decode()}")

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
            start_new_session=True,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            # Reap the `docker exec` client; otherwise it lingers attached to
            # the container, holding its pipes, for the rest of the run.
            await kill_process_group(proc)
            return 124, "", f"Command timed out after {timeout}s"

        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def sync_workspace_out(self) -> None:
        """Copy files from container back to host workspace."""
        if not self._container_id:
            return

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "cp",
            f"{self._container_id}:/workspace/.",
            f"{self.workspace}/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, cp_err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to copy workspace out of container: {cp_err.decode()}")

    async def stop(self) -> None:
        """Sync files out, then stop and remove the container."""
        if self._container_id:
            await self.sync_workspace_out()
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                self._container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            self._container_id = None

    async def _exec_raw(self, command: str) -> None:
        """Run a command inside the container without capturing output."""
        if not self._container_id:
            return
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            self._container_id,
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()


class DockerFileTool:
    """Mixin that routes file operations through a Docker container."""

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._sandbox = sandbox

    async def _read_in_container(self, rel_path: str) -> tuple[bool, str]:
        """Read a file inside the container. Returns (success, content_or_error)."""
        try:
            path = safe_container_path(rel_path)
        except ValueError as e:
            return False, str(e)
        code, stdout, stderr = await self._sandbox.execute(f"cat '/workspace/{path}'", timeout=10)
        if code != 0:
            return False, stderr or f"File not found: {rel_path}"
        return True, stdout

    async def _write_in_container(self, rel_path: str, content: str) -> tuple[bool, str]:
        """Write a file inside the container. Returns (success, error_msg)."""
        try:
            command = build_write_command(rel_path, content)
        except ValueError as e:
            return False, str(e)
        code, _, stderr = await self._sandbox.execute(command, timeout=30)
        if code != 0:
            return False, stderr
        return True, ""

    async def _list_in_container(self, rel_path: str) -> tuple[bool, str]:
        """List a directory inside the container. Returns (success, listing_or_error)."""
        try:
            path = safe_container_path(rel_path)
        except ValueError as e:
            return False, str(e)
        code, stdout, stderr = await self._sandbox.execute(
            f"ls -1F '/workspace/{path}'", timeout=10
        )
        if code != 0:
            return False, stderr or f"Directory not found: {rel_path}"
        return True, stdout

    async def _mkdir_in_container(self, rel_path: str) -> tuple[bool, str]:
        """Create a directory inside the container."""
        try:
            path = safe_container_path(rel_path)
        except ValueError as e:
            return False, str(e)
        code, _, stderr = await self._sandbox.execute(f"mkdir -p '/workspace/{path}'", timeout=5)
        if code != 0:
            return False, stderr
        return True, ""


class DockerReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file within the workspace"
    required_capability = Capability.WORKSPACE_RW

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._docker = DockerFileTool(sandbox)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
            },
            "required": ["path"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        ok, result = await self._docker._read_in_container(args["path"])
        if not ok:
            return ToolResult.error(result)
        return ToolResult.ok(display=result, content=result, path=args["path"])


class DockerWriteFileTool(Tool):
    name = "write_file"
    description = "Write or create a file within the workspace"
    required_capability = Capability.WORKSPACE_RW

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._docker = DockerFileTool(sandbox)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        ok, err = await self._docker._write_in_container(args["path"], args["content"])
        if not ok:
            return ToolResult.error(f"Failed to write: {err}")
        warning = record_write(context, args["path"], args["content"])
        return ToolResult.ok(display=f"Wrote {args['path']}{warning}", path=args["path"])


class DockerListDirectoryTool(Tool):
    name = "list_directory"
    description = "List contents of a directory within the workspace"
    required_capability = Capability.WORKSPACE_RW

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._docker = DockerFileTool(sandbox)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace",
                    "default": ".",
                },
            },
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        rel_path = args.get("path", ".")
        ok, result = await self._docker._list_in_container(rel_path)
        if not ok:
            return ToolResult.error(result)
        # Parse ls -1F output into entries
        entries = [
            line.rstrip("/").rstrip("*").rstrip("@")
            for line in result.strip().split("\n")
            if line.strip()
        ]
        listing = []
        for line in result.strip().split("\n"):
            if not line.strip():
                continue
            if line.endswith("/"):
                listing.append(f"d {line.rstrip('/')}")
            else:
                listing.append(f"f {line.rstrip('*')}")
        display = "\n".join(listing) if listing else "(empty directory)"
        return ToolResult.ok(display=display, entries=entries)


class DockerCreateDirectoryTool(Tool):
    name = "create_directory"
    description = "Create a directory (and parents) within the workspace"
    required_capability = Capability.WORKSPACE_RW

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._docker = DockerFileTool(sandbox)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to workspace"},
            },
            "required": ["path"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        ok, err = await self._docker._mkdir_in_container(args["path"])
        if not ok:
            return ToolResult.error(f"Failed to create directory: {err}")
        return ToolResult.ok(display=f"Created {args['path']}", path=args["path"])


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
        # Match the host shell tool: never let one container command run past a
        # meaningful share of the remaining timebox.
        remaining = context.deadline.remaining()
        dynamic_cap = max(30, int(remaining * 0.15))
        timeout = min(args.get("timeout", 60), 300, dynamic_cap)
        cwd = args.get("cwd", "/workspace")

        # Safety filters still apply unless --danger is set
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
