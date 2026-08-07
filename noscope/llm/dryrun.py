"""A no-cost provider that exercises the full pipeline without an API key.

``noscope run --dry-run`` swaps this in for a real provider. Every phase runs
for real — the workspace is created, tools execute, acceptance checks and the
verification command actually run in the shell, the event log and handoff
report are written — but no network call is made and no tokens are spent.

It's a smoke test for the harness itself: if a dry run completes, the CLI,
capability gating, tool dispatch, phase budgets, and reporting all work, and
anything that fails on a live run is about the model or the API, not the
plumbing.
"""

from __future__ import annotations

import json
from typing import Any

from noscope.llm.base import LLMResponse, Message, ToolCall, ToolSchema, Usage

# The artifact a dry run "builds" — deliberately trivial and dependency-free.
DRY_RUN_FILE = "index.html"
_DRY_RUN_HTML = (
    "<!doctype html>\n"
    "<html><head><title>NoScope dry run</title></head>\n"
    "<body><h1>NoScope dry run</h1>\n"
    "<p>This file was produced without calling any model.</p></body></html>\n"
)

_PLAN = {
    "requested_capabilities": [
        {"cap": "workspace_rw", "why": "write the artifact", "risk": "low"},
        {"cap": "shell_exec", "why": "run acceptance checks", "risk": "medium"},
    ],
    "tasks": [
        {
            "id": "t1",
            "title": "Set up project structure and install dependencies",
            "kind": "edit",
            "priority": "mvp",
            "description": f"Create {DRY_RUN_FILE}",
            "depends_on": [],
        }
    ],
    "mvp_definition": [f"{DRY_RUN_FILE} exists"],
    "exclusions": ["Anything requiring a real model"],
    "acceptance_plan": [
        {"name": "artifact exists", "cmd": f"test -f {DRY_RUN_FILE}", "must_pass": True}
    ],
}


class DryRunProvider:
    """Scripted provider: enough behavior to drive every phase to completion."""

    def __init__(self) -> None:
        self._wrote = False

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        system = messages[0].content if messages else ""
        names = {t.name for t in (tools or [])}
        usage = Usage(input_tokens=0, output_tokens=0)

        # PLAN — the only structured-output call in the pipeline.
        if json_schema is not None:
            return LLMResponse(content=json.dumps(_PLAN), usage=usage)

        # VERIFY — propose a proof command the harness will really execute.
        if "confirm_running" in names:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="dry-verify",
                        name="confirm_running",
                        arguments={
                            "command": f"test -f {DRY_RUN_FILE}",
                            "summary": f"{DRY_RUN_FILE} was created (dry run — no model used)",
                        },
                    )
                ],
                usage=usage,
            )

        # BUILD — the structure agent writes the artifact, then marks the task.
        if "mark_task_complete" in names:
            if "DEPS agent" in system:
                return LLMResponse(
                    content="No dependencies to install.", stop_reason="end_turn", usage=usage
                )
            if not self._wrote:
                self._wrote = True
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="dry-write",
                            name="write_file",
                            arguments={"path": DRY_RUN_FILE, "content": _DRY_RUN_HTML},
                        )
                    ],
                    usage=usage,
                )
            return LLMResponse(
                tool_calls=[
                    ToolCall(id="dry-done", name="mark_task_complete", arguments={"task_id": "t1"})
                ],
                usage=usage,
            )

        # HARDEN repair (shouldn't be needed) and HANDOFF land here.
        return LLMResponse(
            content=(
                "# Handoff Report (dry run)\n\n"
                "This run used `--dry-run`, so no model was called and no tokens "
                "were spent.\n\n"
                "## What Was Built\n\n"
                f"- `{DRY_RUN_FILE}` — a placeholder artifact\n\n"
                "## How to Run It\n\n"
                f"Open `{DRY_RUN_FILE}` in a browser.\n\n"
                "## Next Steps\n\n"
                "Re-run without `--dry-run` (and with an API key) to build for real.\n"
            ),
            stop_reason="end_turn",
            usage=usage,
        )
