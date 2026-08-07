"""Filesystem tools — read, write, edit, list, mkdir within workspace."""

from __future__ import annotations

import difflib
from typing import Any

from noscope.capabilities import Capability
from noscope.tools.base import Tool, ToolContext, ToolResult, record_write
from noscope.tools.safety import resolve_workspace_path


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read the contents of a file within the workspace. For large files, "
        "pass offset (1-based start line) and limit (line count) to read only "
        "a window instead of the whole file."
    )
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "offset": {
                    "type": "integer",
                    "description": "1-based line to start reading from (default: start of file)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: all)",
                },
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

        offset = args.get("offset")
        limit = args.get("limit")
        if offset is not None or limit is not None:
            lines = content.splitlines()
            start = max(0, (int(offset) - 1)) if offset is not None else 0
            end = start + int(limit) if limit is not None else len(lines)
            window = lines[start:end]
            display = "\n".join(window)
            return ToolResult.ok(
                display=display,
                content=display,
                path=str(path),
                lines=f"{start + 1}-{start + len(window)} of {len(lines)}",
            )

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
        warning = record_write(context, args["path"], args["content"])
        return ToolResult.ok(display=f"Wrote {path}{warning}", path=str(path))


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit an existing file by replacing an exact string. Prefer this over "
        "write_file for changes to existing files — it only touches the target "
        "region and returns a diff. old_string must match exactly (including "
        "whitespace and indentation) and must be unique in the file unless "
        "replace_all is true."
    )
    required_capability = Capability.WORKSPACE_RW

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace (include enough context to be unique)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence instead of requiring a unique match",
                    "default": False,
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(args["path"], context.workspace)
        if not path.exists():
            return ToolResult.error(f"File not found: {args['path']}")
        if not path.is_file():
            return ToolResult.error(f"Not a file: {args['path']}")

        old_string = args["old_string"]
        new_string = args["new_string"]
        replace_all = bool(args.get("replace_all", False))

        if old_string == new_string:
            return ToolResult.error("old_string and new_string are identical — nothing to do")

        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.error(f"Cannot edit binary file: {args['path']}")

        count = original.count(old_string)
        if count == 0:
            return ToolResult.error(
                f"old_string not found in {args['path']} — it must match exactly, "
                "including whitespace and indentation"
            )
        if count > 1 and not replace_all:
            return ToolResult.error(
                f"old_string appears {count} times in {args['path']}; add surrounding "
                "context to make it unique, or set replace_all to replace every occurrence"
            )

        updated = original.replace(old_string, new_string)
        path.write_text(updated, encoding="utf-8")
        warning = record_write(context, args["path"], updated)

        diff = _unified_diff(original, updated, args["path"])
        replaced = count if replace_all else 1
        return ToolResult.ok(
            display=f"Edited {args['path']} ({replaced} replacement(s)){warning}\n{diff}",
            path=str(path),
            replacements=replaced,
        )


def _unified_diff(before: str, after: str, path: str) -> str:
    """A compact unified diff, capped so large edits don't flood the context."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=path,
            tofile=path,
            lineterm="",
            n=2,
        )
    )
    if len(lines) > 60:
        lines = lines[:60] + ["... (diff truncated)"]
    return "\n".join(lines)


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
