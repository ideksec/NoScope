"""Filesystem tools — read, write, list, mkdir within workspace."""

from __future__ import annotations

from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult
from noscope.tools.safety import resolve_workspace_path


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file within the workspace"
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
            },
            "required": ["path"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(args["path"], context.workspace)
        if not path.exists():
            return ToolResult.error(f"File not found: {args['path']}")
        if not path.is_file():
            return ToolResult.error(f"Not a file: {args['path']}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.error(f"Cannot read binary file: {args['path']}")

        return ToolResult.ok(display=content, content=content, path=str(path))


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write or create a file within the workspace"
    required_capability = Capability.WORKSPACE_RW

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
        path = resolve_workspace_path(args["path"], context.workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return ToolResult.ok(display=f"Wrote {path}", path=str(path))


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "List contents of a directory within the workspace"
    required_capability = Capability.WORKSPACE_RW

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
        path = resolve_workspace_path(args.get("path", "."), context.workspace)
        if not path.exists():
            return ToolResult.error(f"Directory not found: {args.get('path', '.')}")
        if not path.is_dir():
            return ToolResult.error(f"Not a directory: {args.get('path', '.')}")

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        listing = []
        for entry in entries:
            prefix = "d " if entry.is_dir() else "f "
            listing.append(prefix + entry.name)

        display = "\n".join(listing) if listing else "(empty directory)"
        return ToolResult.ok(display=display, entries=[e.name for e in entries])


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = "Create a directory (and parents) within the workspace"
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to workspace"},
            },
            "required": ["path"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(args["path"], context.workspace)
        path.mkdir(parents=True, exist_ok=True)
        return ToolResult.ok(display=f"Created {path}", path=str(path))
