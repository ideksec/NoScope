"""Phase implementations — PLAN, REQUEST, BUILD, HARDEN, HANDOFF, VERIFY."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noscope.capabilities import (
    CapabilityGrant,
    CapabilityRequest,
)
from noscope.deadline import Deadline, Phase
from noscope.llm.base import LLMProvider, Message, ToolSchema, Usage
from noscope.logging.events import EventLog
from noscope.planning.models import PlanOutput, PlanTask
from noscope.planning.planner import plan as generate_plan
from noscope.spec.models import SpecInput
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher

if TYPE_CHECKING:
    from noscope.ui.console import ConsoleUI


class TokenTracker:
    """Accumulates token usage across all LLM calls."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens


class PlanPhase:
    """Generate a build plan from the spec using an LLM."""

    async def run(
        self,
        spec: SpecInput,
        provider: LLMProvider,
        event_log: EventLog,
        deadline: Deadline,
        tokens: TokenTracker | None = None,
    ) -> PlanOutput:
        event_log.emit(
            phase=Phase.PLAN.value,
            event_type="phase.start",
            summary="Starting PLAN phase",
        )
        deadline.advance_phase(Phase.PLAN)

        plan_output = await generate_plan(spec, provider, tokens=tokens)

        event_log.emit(
            phase=Phase.PLAN.value,
            event_type="phase.complete",
            summary=f"Plan generated: {len(plan_output.tasks)} tasks",
            data={
                "task_count": len(plan_output.tasks),
                "capabilities_requested": len(plan_output.requested_capabilities),
            },
        )

        return plan_output


class RequestPhase:
    """Present capability requests and collect user approvals."""

    async def run(
        self,
        plan: PlanOutput,
        event_log: EventLog,
        deadline: Deadline,
        auto_approve: bool = False,
    ) -> list[CapabilityGrant]:
        event_log.emit(
            phase=Phase.REQUEST.value,
            event_type="phase.start",
            summary="Starting REQUEST phase",
        )
        deadline.advance_phase(Phase.REQUEST)

        grants: list[CapabilityGrant] = []

        for req in plan.requested_capabilities:
            if auto_approve:
                approved = True
            else:
                approved = await self._prompt_user(req)

            grants.append(CapabilityGrant(cap=req.cap, approved=approved))

        event_log.emit(
            phase=Phase.REQUEST.value,
            event_type="phase.complete",
            summary="Capability grants collected",
            data={
                "grants": [g.model_dump() for g in grants],
            },
        )

        return grants

    async def _prompt_user(self, req: CapabilityRequest) -> bool:
        """Interactive prompt for capability approval."""
        from rich.console import Console
        from rich.prompt import Confirm

        console = Console()
        risk_colors = {"low": "green", "medium": "yellow", "high": "red"}
        color = risk_colors.get(req.risk, "white")

        console.print(f"\n  [{color}]●[/{color}] {req.cap}", style="bold")
        console.print(f"    Justification: {req.why}")
        console.print(f"    Risk: [{color}]{req.risk}[/{color}]")

        return Confirm.ask("    Approve?", default=True)


class BuildPhase:
    """Execute the build plan via an LLM agent loop."""

    async def run(
        self,
        plan: PlanOutput,
        provider: LLMProvider,
        dispatcher: ToolDispatcher,
        context: ToolContext,
        event_log: EventLog,
        deadline: Deadline,
        ui: ConsoleUI | None = None,
        tokens: TokenTracker | None = None,
    ) -> list[PlanTask]:
        event_log.emit(
            phase=Phase.BUILD.value,
            event_type="phase.start",
            summary="Starting BUILD phase",
        )
        deadline.advance_phase(Phase.BUILD)

        mvp_tasks = [t for t in plan.tasks if t.priority == "mvp"]
        stretch_tasks = [t for t in plan.tasks if t.priority == "stretch"]
        all_tasks = mvp_tasks + stretch_tasks

        # Track tasks by ID for completion marking
        task_map = {t.id: t for t in all_tasks}

        # Build system prompt
        system = self._build_system_prompt(plan, context.workspace)
        messages: list[Message] = [Message(role="system", content=system)]

        # Initial user message with the plan
        task_list = "\n".join(
            f"- [{t.id}] {t.title} ({t.kind}, {t.priority}): {t.description}"
            for t in all_tasks
        )
        messages.append(
            Message(
                role="user",
                content=f"Execute this build plan. Work through each task in order.\n\n{task_list}\n\nStart with task {all_tasks[0].id if all_tasks else 'none'}.",
            )
        )

        tool_schemas = [
            ToolSchema(name=s["name"], description=s["description"], parameters=s["parameters"])
            for s in dispatcher.to_schemas()
        ]
        # Add mark_task_complete tool for explicit task tracking
        tool_schemas.append(
            ToolSchema(
                name="mark_task_complete",
                description="Mark a task as completed. Call this after finishing each task.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "The task ID (e.g., t1, t2)"},
                    },
                    "required": ["task_id"],
                },
            )
        )

        panic_shown = False

        # Agent loop
        max_iterations = 200
        for _iteration in range(max_iterations):
            if deadline.is_expired():
                event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="deadline.expired",
                    summary="Deadline expired during build",
                )
                break

            if deadline.is_panic_mode() and not panic_shown:
                panic_shown = True
                if ui:
                    ui.panic_warning()
                remaining = deadline.format_remaining()
                messages.append(
                    Message(
                        role="user",
                        content=f"⚠️ PANIC MODE: Only {remaining} remaining. Stop adding features. Focus on making what exists runnable. Skip stretch tasks.",
                    )
                )

            if deadline.should_transition(Phase.BUILD):
                event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="phase.time_exhausted",
                    summary="BUILD phase time budget exhausted",
                )
                break

            # Call LLM
            response = await provider.complete(messages, tools=tool_schemas)
            if tokens:
                tokens.add(response.usage)

            # Add assistant response to conversation
            messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            if response.content:
                event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="llm.response",
                    summary=response.content[:200],
                )
                if ui:
                    ui.llm_thinking(response.content[:200], deadline)

            # If no tool calls, the LLM is done (or confused)
            if not response.tool_calls:
                if response.stop_reason == "end_turn":
                    break
                continue

            # Execute tool calls
            for tc in response.tool_calls:
                # Handle the virtual mark_task_complete tool
                if tc.name == "mark_task_complete":
                    task_id = tc.arguments.get("task_id", "")
                    if task_id in task_map:
                        task_map[task_id].completed = True
                        event_log.emit(
                            phase=Phase.BUILD.value,
                            event_type="task.complete",
                            summary=f"Task {task_id} completed: {task_map[task_id].title}",
                            data={"task_id": task_id},
                        )
                        if ui:
                            ui.task_complete(task_id, task_map[task_id].title, deadline)
                        messages.append(
                            Message(
                                role="tool",
                                content=f"Task {task_id} marked as complete.",
                                tool_call_id=tc.id,
                            )
                        )
                    else:
                        messages.append(
                            Message(
                                role="tool",
                                content=f"Unknown task ID: {task_id}",
                                tool_call_id=tc.id,
                            )
                        )
                    continue

                # Show tool activity in UI
                tool_summary = _tool_summary(tc.name, tc.arguments)
                if ui:
                    ui.tool_activity(tc.name, tool_summary, deadline)

                result = await dispatcher.dispatch(tc.name, tc.arguments, context)
                messages.append(
                    Message(
                        role="tool",
                        content=result.display or json.dumps(result.data),
                        tool_call_id=tc.id,
                    )
                )

        return all_tasks

    def _build_system_prompt(self, plan: PlanOutput, workspace: Path) -> str:
        return f"""\
You are an autonomous software builder. You have a fixed time budget and must build a working MVP.

Workspace: {workspace}

THE #1 PRIORITY: The app MUST RUN at the end. A running demo beats perfect code. If you have to choose between features and a working app, always choose working.

Rules:
- Write clean, working code
- Focus on MVP tasks first, stretch tasks only if time permits
- Use the tools provided to create files, run commands, etc.
- After completing each task, call mark_task_complete with the task ID
- If something fails, try to fix it or work around it
- Prefer simple, working solutions over complex ones
- Use "python3 -m pip" instead of bare "pip" for installing packages
- Use "python3" instead of "python" for running scripts
- Install ALL dependencies as you go — don't leave this for later
- Test that imports work after creating files
- When done with all tasks, say "BUILD COMPLETE" in your response

MVP definition: {json.dumps(plan.mvp_definition)}
Exclusions: {json.dumps(plan.exclusions)}
"""


class HardenPhase:
    """Run acceptance checks and validation."""

    async def run(
        self,
        plan: PlanOutput,
        spec: SpecInput,
        dispatcher: ToolDispatcher,
        context: ToolContext,
        event_log: EventLog,
        deadline: Deadline,
        ui: ConsoleUI | None = None,
    ) -> list[dict[str, Any]]:
        event_log.emit(
            phase=Phase.HARDEN.value,
            event_type="phase.start",
            summary="Starting HARDEN phase",
        )
        deadline.advance_phase(Phase.HARDEN)

        results: list[dict[str, Any]] = []

        # Collect all cmd: checks from spec and plan
        checks: list[tuple[str, str]] = []

        for ac in spec.acceptance:
            if ac.is_cmd and ac.command:
                checks.append((ac.raw, ac.command))

        for ap in plan.acceptance_plan:
            if ap.cmd:
                checks.append((ap.name, ap.cmd))

        for name, cmd in checks:
            if deadline.is_expired() or deadline.should_transition(Phase.HARDEN):
                results.append({"name": name, "cmd": cmd, "passed": False, "skipped": True})
                continue

            if ui:
                ui.tool_activity("check", name, deadline)

            result = await dispatcher.dispatch(
                "exec_command", {"command": cmd, "timeout": 30}, context
            )
            passed = result.status == "ok"
            results.append({
                "name": name,
                "cmd": cmd,
                "passed": passed,
                "output": result.display[:1000],
            })

            event_log.emit(
                phase=Phase.HARDEN.value,
                event_type="acceptance.check",
                summary=f"{'✓' if passed else '✗'} {name}",
                data={"name": name, "cmd": cmd},
                result={"passed": passed},
            )

        event_log.emit(
            phase=Phase.HARDEN.value,
            event_type="phase.complete",
            summary=f"Harden complete: {sum(1 for r in results if r.get('passed'))}/{len(results)} passed",
        )

        return results


class VerifyPhase:
    """Verify the MVP actually runs — install deps, fix issues, confirm it works.

    This is the most critical phase. The whole point of NoScope is producing a
    running demo. This phase will aggressively fix issues until the app runs.
    """

    async def run(
        self,
        spec: SpecInput,
        provider: LLMProvider,
        dispatcher: ToolDispatcher,
        context: ToolContext,
        event_log: EventLog,
        deadline: Deadline,
        ui: ConsoleUI | None = None,
        tokens: TokenTracker | None = None,
    ) -> tuple[bool, str]:
        """Returns (success, message)."""
        event_log.emit(
            phase=Phase.HARDEN.value,
            event_type="verify.start",
            summary="Starting MVP verification",
        )

        system = f"""\
You are the FINAL VERIFICATION agent. Your ONE job is to make this project RUN.
The project is in: {context.workspace}

This is a live demo scenario. The user NEEDS a working app at the end. Failure is not acceptable.

Your process:
1. List all files to understand the project structure
2. Read the main entry point to understand how to run it
3. Install ALL dependencies (use python3 -m pip install -r requirements.txt, npm install, etc.)
4. Try to run/import the main application
5. If it fails, READ THE ERROR, FIX THE CODE, and try again
6. Keep fixing until it works — you have multiple attempts
7. For web apps: verify routes work using the test client or curl
8. For CLI tools: run with example input and verify output

FIXING RULES:
- If a module is missing: install it
- If an import fails: fix the import
- If a file is missing: create it
- If the code has a bug: fix the bug
- If a dependency version conflicts: adjust it
- Try up to 5 fix attempts before giving up

Use python3 (not python) and python3 -m pip (not pip).

After verification, respond with EXACTLY one of:
- "VERIFIED: <what works and how to run it>" if the MVP runs
- "FAILED: <what went wrong after all fix attempts>" only if truly unfixable
"""

        messages: list[Message] = [
            Message(role="system", content=system),
            Message(
                role="user",
                content=f"Make sure the {spec.name} project runs. Install deps, fix any issues, confirm it works end-to-end. This is a live demo — it MUST work.",
            ),
        ]

        tool_schemas = [
            ToolSchema(name=s["name"], description=s["description"], parameters=s["parameters"])
            for s in dispatcher.to_schemas()
        ]

        # Aggressive agent loop — more iterations than build phase gets
        for _i in range(50):
            if deadline.is_expired():
                return False, "Deadline expired during verification"

            response = await provider.complete(messages, tools=tool_schemas)
            if tokens:
                tokens.add(response.usage)

            messages.append(
                Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )

            if response.content:
                if ui:
                    ui.tool_activity("verify", response.content[:80], deadline)

                # Check for final verdict
                content_upper = response.content.upper()
                if "VERIFIED:" in content_upper:
                    idx = response.content.upper().index("VERIFIED:")
                    msg = response.content[idx + 9:].strip()
                    event_log.emit(
                        phase=Phase.HARDEN.value,
                        event_type="verify.pass",
                        summary=f"MVP verified: {msg}",
                    )
                    return True, msg
                if "FAILED:" in content_upper:
                    idx = response.content.upper().index("FAILED:")
                    msg = response.content[idx + 7:].strip()
                    event_log.emit(
                        phase=Phase.HARDEN.value,
                        event_type="verify.fail",
                        summary=f"MVP failed: {msg}",
                    )
                    return False, msg

            if not response.tool_calls:
                if response.stop_reason == "end_turn":
                    break
                continue

            for tc in response.tool_calls:
                if ui:
                    ui.tool_activity(tc.name, _tool_summary(tc.name, tc.arguments), deadline)
                result = await dispatcher.dispatch(tc.name, tc.arguments, context)
                messages.append(
                    Message(
                        role="tool",
                        content=result.display or json.dumps(result.data),
                        tool_call_id=tc.id,
                    )
                )

        return False, "Verification did not complete"


class HandoffPhase:
    """Generate the handoff report — ALWAYS runs."""

    async def run(
        self,
        spec: SpecInput,
        plan: PlanOutput,
        tasks: list[PlanTask],
        acceptance_results: list[dict[str, Any]],
        provider: LLMProvider,
        event_log: EventLog,
        deadline: Deadline,
        output_path: Path,
        tokens: TokenTracker | None = None,
        workspace: Path | None = None,
        verify_result: tuple[bool, str] | None = None,
    ) -> str:
        event_log.emit(
            phase=Phase.HANDOFF.value,
            event_type="phase.start",
            summary="Starting HANDOFF phase",
        )
        deadline.advance_phase(Phase.HANDOFF)

        completed = [t for t in tasks if t.completed]
        incomplete = [t for t in tasks if not t.completed]
        passed = [r for r in acceptance_results if r.get("passed")]

        # Get actual file listing from workspace
        file_listing = "(unknown)"
        if workspace and workspace.exists():
            try:
                files = sorted(
                    str(p.relative_to(workspace))
                    for p in workspace.rglob("*")
                    if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
                )
                file_listing = "\n".join(f"- {f}" for f in files[:50])
            except Exception:
                file_listing = "(could not list)"

        # Check for key project files to determine stack
        has_requirements = workspace and (workspace / "requirements.txt").exists() if workspace else False
        has_package_json = workspace and (workspace / "package.json").exists() if workspace else False

        stack_hint = ""
        if has_requirements and not has_package_json:
            stack_hint = "This is a Python project. Use pip/python3 commands, NOT npm."
        elif has_package_json and not has_requirements:
            stack_hint = "This is a Node.js project. Use npm commands."

        verify_info = ""
        if verify_result:
            verified, msg = verify_result
            verify_info = f"\nMVP Verification: {'PASSED' if verified else 'FAILED'} — {msg}"

        report_data = f"""\
Generate a concise handoff report in markdown for this build run.

Project: {spec.name}
Timebox: {spec.timebox}
Tasks completed: {len(completed)}/{len(tasks)}
Acceptance checks passed: {len(passed)}/{len(acceptance_results)}
{verify_info}

{stack_hint}

Files in workspace:
{file_listing}

Completed tasks:
{chr(10).join(f'- {t.title}' for t in completed) or '(none)'}

Incomplete tasks:
{chr(10).join(f'- {t.title}' for t in incomplete) or '(none)'}

Acceptance results:
{chr(10).join(f'- {"✓" if r.get("passed") else "✗"} {r["name"]}' for r in acceptance_results) or '(none)'}

IMPORTANT: Base the "How to Run It" section ONLY on the actual files listed above. Do NOT guess — if you see requirements.txt, use pip. If you see package.json, use npm. Never mix them up.

Write the report with these sections:
1. Contract Summary
2. What Was Built
3. How to Run It (exact commands based on actual files)
4. Acceptance Results (pass/fail table)
5. Known Gaps and Risks
6. Next Recommended Steps
"""

        try:
            messages = [
                Message(role="system", content="You write clear, concise project handoff reports in markdown."),
                Message(role="user", content=report_data),
            ]
            response = await provider.complete(messages)
            if tokens:
                tokens.add(response.usage)
            report = response.content
        except Exception:
            report = self._fallback_report(spec, completed, incomplete, acceptance_results)

        output_path.write_text(report, encoding="utf-8")

        event_log.emit(
            phase=Phase.HANDOFF.value,
            event_type="phase.complete",
            summary="Handoff report generated",
        )

        return report

    def _fallback_report(
        self,
        spec: SpecInput,
        completed: list[PlanTask],
        incomplete: list[PlanTask],
        acceptance_results: list[dict[str, Any]],
    ) -> str:
        lines = [
            f"# Handoff Report: {spec.name}",
            "",
            "## Contract Summary",
            f"- **Timebox**: {spec.timebox}",
            f"- **Constraints**: {', '.join(spec.constraints) or 'none'}",
            "",
            "## What Was Built",
        ]
        for t in completed:
            lines.append(f"- ✓ {t.title}")
        if not completed:
            lines.append("- (no tasks completed)")
        lines.append("")

        if incomplete:
            lines.append("## Incomplete Tasks")
            for t in incomplete:
                lines.append(f"- ✗ {t.title}")
            lines.append("")

        lines.append("## Acceptance Results")
        lines.append("| Check | Result |")
        lines.append("|-------|--------|")
        for r in acceptance_results:
            status = "✓ Pass" if r.get("passed") else ("⊘ Skipped" if r.get("skipped") else "✗ Fail")
            lines.append(f"| {r['name']} | {status} |")
        lines.append("")

        lines.append("## Known Gaps and Risks")
        lines.append("- Refer to incomplete tasks above")
        lines.append("")
        lines.append("## Next Recommended Steps")
        lines.append("- Review generated code")
        lines.append("- Run acceptance checks manually")
        lines.append("- Address incomplete tasks")

        return "\n".join(lines)


def _tool_summary(name: str, args: dict[str, Any]) -> str:
    """Create a brief human-readable summary of a tool call."""
    if name == "write_file":
        return f"writing {args.get('path', '?')}"
    if name == "read_file":
        return f"reading {args.get('path', '?')}"
    if name == "exec_command":
        cmd = args.get("command", "")
        return cmd[:80] if len(cmd) <= 80 else cmd[:77] + "..."
    if name == "list_directory":
        return f"listing {args.get('path', '.')}"
    if name == "create_directory":
        return f"creating {args.get('path', '?')}"
    if name in ("git_init", "git_status", "git_add", "git_commit", "git_diff"):
        return name.replace("_", " ")
    return name
