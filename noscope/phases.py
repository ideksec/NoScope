"""Phase implementations — PLAN, REQUEST, BUILD, HARDEN, HANDOFF."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noscope.capabilities import (
    CapabilityGrant,
    CapabilityRequest,
)
from noscope.deadline import Deadline, Phase
from noscope.llm.base import LLMProvider, Message, ToolSchema
from noscope.logging.events import EventLog
from noscope.planning.models import PlanOutput, PlanTask
from noscope.planning.planner import plan as generate_plan
from noscope.spec.models import SpecInput
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher


class PlanPhase:
    """Generate a build plan from the spec using an LLM."""

    async def run(
        self,
        spec: SpecInput,
        provider: LLMProvider,
        event_log: EventLog,
        deadline: Deadline,
    ) -> PlanOutput:
        event_log.emit(
            phase=Phase.PLAN.value,
            event_type="phase.start",
            summary="Starting PLAN phase",
        )
        deadline.advance_phase(Phase.PLAN)

        plan_output = await generate_plan(spec, provider)

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
        # Import here to avoid circular dependency and allow non-interactive usage
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

            if deadline.is_panic_mode():
                # Inject panic mode notice
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

            # If no tool calls, the LLM is done (or confused)
            if not response.tool_calls:
                # Check if all MVP tasks could be considered done
                if response.stop_reason == "end_turn":
                    # LLM thinks it's done
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

                result = await dispatcher.dispatch(tc.name, tc.arguments, context)
                messages.append(
                    Message(
                        role="tool",
                        content=result.display or json.dumps(result.data),
                        tool_call_id=tc.id,
                    )
                )

        # Mark remaining tasks based on what was actually built
        return all_tasks

    def _build_system_prompt(self, plan: PlanOutput, workspace: Path) -> str:
        return f"""\
You are an autonomous software builder. You have a fixed time budget and must build a working MVP.

Workspace: {workspace}

Rules:
- Write clean, working code
- Focus on MVP tasks first, stretch tasks only if time permits
- Use the tools provided to create files, run commands, etc.
- After completing each task, call mark_task_complete with the task ID
- If something fails, try to fix it or work around it
- Prefer simple, working solutions over complex ones
- Use "python3 -m pip" instead of bare "pip" for installing packages
- Use "python3" instead of "python" for running scripts
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

        # Build the report with structured data + LLM prose
        report_data = f"""\
Generate a concise handoff report in markdown for this build run.

Project: {spec.name}
Timebox: {spec.timebox}
Tasks completed: {len(completed)}/{len(tasks)}
Acceptance checks passed: {len(passed)}/{len(acceptance_results)}

Completed tasks:
{chr(10).join(f'- {t.title}' for t in completed) or '(none)'}

Incomplete tasks:
{chr(10).join(f'- {t.title}' for t in incomplete) or '(none)'}

Acceptance results:
{chr(10).join(f'- {"✓" if r.get("passed") else "✗"} {r["name"]}' for r in acceptance_results) or '(none)'}

Write the report with these sections:
1. Contract Summary
2. What Was Built
3. How to Run It (exact commands)
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
            report = response.content
        except Exception:
            # Fallback: generate a basic report without LLM
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
