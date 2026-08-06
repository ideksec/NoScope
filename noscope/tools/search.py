"""Search and discovery tools — grep and glob within the workspace."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.safety import resolve_workspace_path

# Directories never worth searching — build output, vendored deps, VCS, caches.
_IGNORED_DIRS = {
    ".git",
    ".noscope",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
}
_MAX_FILE_BYTES = 1_000_000  # skip files larger than ~1 MB


def _is_ignored(path: Path, workspace: Path) -> bool:
    return any(part in _IGNORED_DIRS for part in path.relative_to(workspace).parts)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:1024]


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Search file contents across the workspace with a regular expression. "
        "Returns matching lines as 'path:line: text' so you can locate code "
        "without reading whole files. Optionally restrict by a subdirectory or "
        "a filename glob (e.g. '*.py')."
    )
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for"},
                "path": {
                    "type": "string",
                    "description": "Subdirectory to search under (default: whole workspace)",
                    "default": ".",
                },
                "glob": {
                    "type": "string",
                    "description": "Only search files whose name matches this glob, e.g. '*.py'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matching lines to return (default 100)",
                    "default": 100,
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            regex = re.compile(args["pattern"])
        except re.error as e:
            return ToolResult.error(f"Invalid regular expression: {e}")

        base = resolve_workspace_path(args.get("path", "."), context.workspace)
        if not base.exists():
            return ToolResult.error(f"Path not found: {args.get('path', '.')}")

        workspace = context.workspace.resolve()
        name_glob = args.get("glob")
        max_results = int(args.get("max_results", 100))

        matches: list[str] = []
        truncated = False
        for file in sorted(base.rglob("*")):
            if not file.is_file() or _is_ignored(file, workspace):
                continue
            if name_glob and not file.match(name_glob):
                continue
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
                raw = file.read_bytes()
            except OSError:
                continue
            if _looks_binary(raw):
                continue

            rel = file.relative_to(workspace)
            for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        if not matches:
            return ToolResult.ok(display="(no matches)", count=0)
        display = "\n".join(matches)
        if truncated:
            display += f"\n... (stopped at {max_results} matches)"
        return ToolResult.ok(display=display, count=len(matches), truncated=truncated)


class FindFilesTool(Tool):
    name = "find_files"
    description = (
        "Find files in the workspace by glob pattern (e.g. '**/*.py', "
        "'src/**/*.ts'). Returns matching paths — use it to discover the "
        "project layout instead of listing directories one at a time."
    )
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern relative to the workspace, e.g. '**/*.py'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum paths to return (default 200)",
                    "default": 200,
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        workspace = context.workspace.resolve()
        max_results = int(args.get("max_results", 200))

        paths: list[str] = []
        truncated = False
        for match in sorted(workspace.glob(args["pattern"])):
            if not match.is_file() or _is_ignored(match, workspace):
                continue
            paths.append(str(match.relative_to(workspace)))
            if len(paths) >= max_results:
                truncated = True
                break

        if not paths:
            return ToolResult.ok(display="(no files matched)", count=0)
        display = "\n".join(paths)
        if truncated:
            display += f"\n... (stopped at {max_results} files)"
        return ToolResult.ok(display=display, count=len(paths), truncated=truncated)
