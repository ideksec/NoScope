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

MAX_BUILD_ITERATIONS = 200
MAX_VERIFY_ITERATIONS = 50


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
            f"- [{t.id}] {t.title} ({t.kind}, {t.priority}): {t.description}" for t in all_tasks
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
        for _iteration in range(MAX_BUILD_ITERATIONS):
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
        total_tasks = len(plan.tasks)
        mvp_count = sum(1 for t in plan.tasks if t.priority == "mvp")
        stretch_count = total_tasks - mvp_count

        return f"""\
You are an autonomous software builder. You have a fixed time budget and must build a working MVP.

Workspace: {workspace}

NON-NEGOTIABLE: The app MUST RUN at the end. A running demo beats perfect code. If you have to choose between more features and a working app, always choose working.

You have {total_tasks} tasks ({mvp_count} MVP + {stretch_count} stretch). Complete ALL MVP tasks, then tackle stretch tasks if time allows. When time is plentiful, build something impressive — good styling, thoughtful UX, proper error handling. When time is tight, cut corners on polish but never on functionality.

Rules:
- Write clean, working code — build something you'd be proud to demo
- After completing each task, call mark_task_complete with the task ID
- If something fails, fix it or work around it — don't skip it
- Use "python3 -m pip" instead of bare "pip" for installing packages
- Use "python3" instead of "python" for running scripts
- Install dependencies EARLY — don't leave this for the last task
- Test that the app starts after writing the core files
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
            results.append(
                {
                    "name": name,
                    "cmd": cmd,
                    "passed": passed,
                    "output": result.display[:1000],
                }
            )

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
            phase=Phase.VERIFY.value,
            event_type="verify.start",
            summary="Starting MVP verification",
        )

        system = f"""\
You are the FINAL VERIFICATION agent. Your ONE job is to make this project RUN.
The project is in: {context.workspace}

This is a live demo. The user NEEDS a clickable working app. BE FAST.

DO THIS IN ORDER — no unnecessary steps:
1. Check for package.json or requirements.txt (ONE list_directory call)
2. Install deps immediately (npm install OR python3 -m pip install -r requirements.txt)
3. Start the app in background and test it:
   - Node.js: Run "node server.js &" or "npm start &", wait 2s, curl localhost
   - Python/Flask: Run "python3 app.py &", wait 2s, curl localhost:5000
   - If it fails, READ THE ERROR, fix the code, try again
4. Once the server responds to curl, immediately respond with VERIFIED

DO NOT:
- Read every file — you don't need to understand all the code
- Spend time on file listings beyond the root directory
- Over-analyze — if curl gets a response, it works

FIXING (if needed):
- Missing module → install it
- Import error → fix the import
- Missing file → create it
- Max 3 fix attempts, then FAILED

Use python3 (not python) and python3 -m pip (not pip).

RESPOND WITH EXACTLY ONE OF:
- "VERIFIED: <one-line description>" — the app runs
- "FAILED: <what's broken>" — unfixable after 3 attempts
"""

        messages: list[Message] = [
            Message(role="system", content=system),
            Message(
                role="user",
                content=f"Get {spec.name} running NOW. Install deps, start server, curl it. Go.",
            ),
        ]

        tool_schemas = [
            ToolSchema(name=s["name"], description=s["description"], parameters=s["parameters"])
            for s in dispatcher.to_schemas()
        ]

        # Aggressive agent loop — more iterations than build phase gets
        for _i in range(MAX_VERIFY_ITERATIONS):
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
                    msg = response.content[idx + 9 :].strip()
                    event_log.emit(
                        phase=Phase.VERIFY.value,
                        event_type="verify.pass",
                        summary=f"MVP verified: {msg}",
                    )
                    return True, msg
                if "FAILED:" in content_upper:
                    idx = response.content.upper().index("FAILED:")
                    msg = response.content[idx + 7 :].strip()
                    event_log.emit(
                        phase=Phase.VERIFY.value,
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
            except Exception as e:
                event_log.emit(
                    phase=Phase.HANDOFF.value,
                    event_type="handoff.warning",
                    summary=f"Could not list workspace files: {e}",
                )
                file_listing = "(could not list)"

        # Check for key project files to determine stack
        has_requirements = (
            workspace and (workspace / "requirements.txt").exists() if workspace else False
        )
        has_package_json = (
            workspace and (workspace / "package.json").exists() if workspace else False
        )

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
{chr(10).join(f"- {t.title}" for t in completed) or "(none)"}

Incomplete tasks:
{chr(10).join(f"- {t.title}" for t in incomplete) or "(none)"}

Acceptance results:
{chr(10).join(f"- {'✓' if r.get('passed') else '✗'} {r['name']}" for r in acceptance_results) or "(none)"}

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
                Message(
                    role="system",
                    content="You write clear, concise project handoff reports in markdown.",
                ),
                Message(role="user", content=report_data),
            ]
            response = await provider.complete(messages)
            if tokens:
                tokens.add(response.usage)
            report = response.content
        except Exception as e:
            event_log.emit(
                phase=Phase.HANDOFF.value,
                event_type="handoff.warning",
                summary=f"LLM handoff report failed, using fallback: {e}",
            )
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
            status = (
                "✓ Pass" if r.get("passed") else ("⊘ Skipped" if r.get("skipped") else "✗ Fail")
            )
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
        return str(cmd[:80]) if len(cmd) <= 80 else str(cmd[:77]) + "..."
    if name == "list_directory":
        return f"listing {args.get('path', '.')}"
    if name == "create_directory":
        return f"creating {args.get('path', '?')}"
    if name in ("git_init", "git_status", "git_add", "git_commit", "git_diff"):
        return name.replace("_", " ")
    return name
