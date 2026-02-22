"""Autonomous build agents — worker and audit agents for parallel execution."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from noscope.deadline import Deadline, Phase
from noscope.llm.base import LLMProvider, Message, ToolCall, ToolSchema
from noscope.logging.events import EventLog
from noscope.planning.models import PlanTask
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher

if TYPE_CHECKING:
    from noscope.phases import TokenTracker
    from noscope.ui.console import ConsoleUI

MAX_AGENT_ITERATIONS = 200
TIME_STATUS_INTERVAL = 3  # Inject time status every N tool calls


class BuildAgent:
    """An autonomous agent that works on assigned tasks.

    Each agent runs its own LLM conversation loop, executing tool calls
    and tracking task completion. Multiple agents can run in parallel
    on non-overlapping task sets.
    """

    def __init__(
        self,
        agent_id: str,
        provider: LLMProvider,
        dispatcher: ToolDispatcher,
        context: ToolContext,
        event_log: EventLog,
        deadline: Deadline,
        ui: ConsoleUI | None = None,
        tokens: TokenTracker | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.provider = provider
        self.dispatcher = dispatcher
        self.context = context
        self.event_log = event_log
        self.deadline = deadline
        self.ui = ui
        self.tokens = tokens
        self._tool_call_count = 0

    async def run(
        self,
        tasks: list[PlanTask],
        system_prompt: str,
    ) -> list[PlanTask]:
        """Execute assigned tasks. Returns tasks with completion status updated."""
        task_map = {t.id: t for t in tasks}

        messages: list[Message] = [Message(role="system", content=system_prompt)]

        task_list = "\n".join(
            f"- [{t.id}] {t.title} ({t.kind}, {t.priority}): {t.description}" for t in tasks
        )
        messages.append(
            Message(
                role="user",
                content=(
                    f"Execute these tasks. Work through each in order.\n\n"
                    f"{task_list}\n\nStart with task {tasks[0].id if tasks else 'none'}."
                ),
            )
        )

        tool_schemas = [
            ToolSchema(name=s["name"], description=s["description"], parameters=s["parameters"])
            for s in self.dispatcher.to_schemas()
        ]
        tool_schemas.append(
            ToolSchema(
                name="mark_task_complete",
                description="Mark a task as completed. Call this after finishing each task.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The task ID (e.g., t1, t2)",
                        },
                    },
                    "required": ["task_id"],
                },
            )
        )

        for _iteration in range(MAX_AGENT_ITERATIONS):
            if self.deadline.is_expired() or self.deadline.should_transition(Phase.BUILD):
                break

            # Check if all assigned tasks are done
            if all(t.completed for t in tasks):
                self.event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="agent.tasks_complete",
                    summary=f"Agent {self.agent_id}: all {len(tasks)} tasks complete",
                    data={"agent_id": self.agent_id},
                )
                break

            response = await self.provider.complete(messages, tools=tool_schemas)
            if self.tokens:
                self.tokens.add(response.usage)

            messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            if response.content:
                self.event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="llm.response",
                    summary=f"[{self.agent_id}] {response.content[:200]}",
                )
                if self.ui:
                    self.ui.llm_thinking(
                        f"[{self.agent_id}] {response.content[:150]}", self.deadline
                    )

            if not response.tool_calls:
                if response.stop_reason == "end_turn":
                    break
                continue

            # Execute tool calls — parallel for file ops, sequential for shell
            messages.extend(await self._execute_tool_calls(response.tool_calls, task_map))

            # Inject time status periodically
            self._tool_call_count += len(response.tool_calls)
            if self._tool_call_count % TIME_STATUS_INTERVAL == 0:
                completed = sum(1 for t in tasks if t.completed)
                remaining = self.deadline.format_remaining()
                messages.append(
                    Message(
                        role="user",
                        content=(f"⏱ {remaining} remaining | {completed}/{len(tasks)} tasks done"),
                    )
                )

        return tasks

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        task_map: dict[str, PlanTask],
    ) -> list[Message]:
        """Execute tool calls with file ops in parallel, shell commands sequential."""
        results: list[Message] = []

        # Separate virtual, file, and shell calls
        virtual_calls: list[ToolCall] = []
        file_calls: list[ToolCall] = []
        shell_calls: list[ToolCall] = []

        for tc in tool_calls:
            if tc.name == "mark_task_complete":
                virtual_calls.append(tc)
            elif tc.name in ("write_file", "read_file", "list_directory", "create_directory"):
                file_calls.append(tc)
            else:
                shell_calls.append(tc)

        # Handle virtual calls immediately
        for tc in virtual_calls:
            task_id = tc.arguments.get("task_id", "")
            if task_id in task_map:
                task_map[task_id].completed = True
                self.event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="task.complete",
                    summary=f"[{self.agent_id}] Task {task_id}: {task_map[task_id].title}",
                    data={"task_id": task_id, "agent_id": self.agent_id},
                )
                if self.ui:
                    self.ui.task_complete(task_id, task_map[task_id].title, self.deadline)
                results.append(
                    Message(
                        role="tool",
                        content=f"Task {task_id} marked as complete.",
                        tool_call_id=tc.id,
                    )
                )
            else:
                results.append(
                    Message(role="tool", content=f"Unknown task ID: {task_id}", tool_call_id=tc.id)
                )

        # Execute file operations in parallel
        if file_calls:
            file_coros = [self._dispatch_and_wrap(tc) for tc in file_calls]
            file_results = await asyncio.gather(*file_coros)
            results.extend(file_results)

        # Execute shell commands sequentially (they may depend on each other)
        for tc in shell_calls:
            if self.ui:
                self.ui.tool_activity(tc.name, _tool_summary(tc.name, tc.arguments), self.deadline)
            result = await self.dispatcher.dispatch(tc.name, tc.arguments, self.context)
            results.append(
                Message(
                    role="tool",
                    content=result.display or json.dumps(result.data),
                    tool_call_id=tc.id,
                )
            )

        return results

    async def _dispatch_and_wrap(self, tc: ToolCall) -> Message:
        """Dispatch a tool call and wrap the result as a Message."""
        if self.ui:
            self.ui.tool_activity(tc.name, _tool_summary(tc.name, tc.arguments), self.deadline)
        result = await self.dispatcher.dispatch(tc.name, tc.arguments, self.context)
        return Message(
            role="tool",
            content=result.display or json.dumps(result.data),
            tool_call_id=tc.id,
        )


class AuditAgent:
    """Continuously validates build quality while workers execute.

    Runs periodic checks (syntax, build, imports) and collects findings.
    """

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        context: ToolContext,
        event_log: EventLog,
        deadline: Deadline,
        ui: ConsoleUI | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.context = context
        self.event_log = event_log
        self.deadline = deadline
        self.ui = ui

    async def run_continuous(self, check_interval: float = 20.0) -> list[dict[str, Any]]:
        """Run periodic validation checks. Returns list of findings."""
        findings: list[dict[str, Any]] = []

        # Wait for workers to write some files first
        await asyncio.sleep(min(check_interval, self.deadline.phase_remaining(Phase.BUILD) / 3))

        while not self.deadline.is_expired() and self.deadline.phase_remaining(Phase.BUILD) > 30:
            check_result = await self._run_checks()
            if check_result:
                findings.extend(check_result)
                self.event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="audit.finding",
                    summary=f"Audit found {len(check_result)} issue(s)",
                    data={"findings": check_result},
                )
            await asyncio.sleep(check_interval)

        return findings

    async def _run_checks(self) -> list[dict[str, Any]]:
        """Run quick validation checks on the workspace."""
        findings: list[dict[str, Any]] = []
        workspace = self.context.workspace

        # Check if key project files exist
        has_package_json = (workspace / "package.json").exists()
        has_requirements = (workspace / "requirements.txt").exists()
        has_app = any(
            (workspace / f).exists()
            for f in ("app.py", "main.py", "server.js", "index.js", "src/App.tsx", "src/App.jsx")
        )

        if not has_app and not has_package_json and not has_requirements:
            findings.append({"type": "missing_files", "message": "No app entry point found yet"})
            return findings

        # Try a syntax/build check
        if has_package_json:
            result = await self.dispatcher.dispatch(
                "exec_command",
                {
                    "command": 'node -e \'JSON.parse(require("fs").readFileSync("package.json"))\'',
                    "timeout": 10,
                },
                self.context,
            )
            if result.status == "error":
                findings.append({"type": "invalid_json", "message": "package.json is invalid"})

        if has_requirements:
            result = await self.dispatcher.dispatch(
                "exec_command",
                {"command": "python3 -c 'open(\"requirements.txt\").read()'", "timeout": 5},
                self.context,
            )
            if result.status == "error":
                findings.append(
                    {"type": "invalid_requirements", "message": "requirements.txt unreadable"}
                )

        if self.ui and not findings:
            self.ui.tool_activity("audit", "checks passed", self.deadline)

        return findings


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
