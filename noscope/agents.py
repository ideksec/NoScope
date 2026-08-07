"""Autonomous build agents — worker and audit agents for parallel execution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noscope.context import trim_history, truncate_tool_output
from noscope.deadline import Deadline, Phase
from noscope.llm.base import LLMProvider, Message, ToolCall, ToolSchema
from noscope.logging.events import EventLog
from noscope.planning.models import PlanTask
from noscope.tools.base import ToolContext, tool_summary
from noscope.tools.dispatcher import ToolDispatcher

if TYPE_CHECKING:
    from noscope.phases import TokenTracker
    from noscope.ui.console import ConsoleUI

MAX_AGENT_ITERATIONS = 200
TIME_STATUS_INTERVAL = 3  # Inject time status every N tool calls


class AuditFeed:
    """Shared channel between the audit agent and build agents.

    The audit agent publishes findings; each worker polls for findings it
    hasn't seen yet and injects them into its own conversation. Findings are
    deduplicated by (type, message) so workers aren't re-alerted every audit
    cycle for the same unresolved issue.
    """

    def __init__(self) -> None:
        self._findings: list[dict[str, Any]] = []
        self._seen_keys: set[tuple[str, str]] = set()
        self._cursors: dict[str, int] = {}

    def publish(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add findings to the feed. Returns only the findings that are new."""
        new: list[dict[str, Any]] = []
        for finding in findings:
            key = (str(finding.get("type", "")), str(finding.get("message", "")))
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            self._findings.append(finding)
            new.append(finding)
        return new

    def poll(self, consumer_id: str) -> list[dict[str, Any]]:
        """Return findings published since this consumer's last poll."""
        cursor = self._cursors.get(consumer_id, 0)
        self._cursors[consumer_id] = len(self._findings)
        return self._findings[cursor:]

    @property
    def all_findings(self) -> list[dict[str, Any]]:
        return list(self._findings)


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
        feed: AuditFeed | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.provider = provider
        self.dispatcher = dispatcher
        self.context = context
        self.event_log = event_log
        self.deadline = deadline
        self.ui = ui
        self.tokens = tokens
        self.feed = feed
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
            # Token spend cap — stop like a deadline so the run still hands off.
            if self.tokens is not None and self.tokens.exceeded():
                self.event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="budget.exceeded",
                    summary=f"[{self.agent_id}] Token budget reached — stopping",
                    data={"agent_id": self.agent_id},
                )
                break

            # Check if all assigned tasks are done (skip check if no tasks assigned)
            if tasks and all(t.completed for t in tasks):
                self.event_log.emit(
                    phase=Phase.BUILD.value,
                    event_type="agent.tasks_complete",
                    summary=f"Agent {self.agent_id}: all {len(tasks)} tasks complete",
                    data={"agent_id": self.agent_id},
                )
                break

            # Surface new audit findings before the next LLM turn so the
            # worker can course-correct instead of building on broken files
            audit_message = self._poll_audit_feed()
            if audit_message:
                messages.append(Message(role="user", content=audit_message))

            # Keep the conversation inside the context budget — a request that
            # fails on length would cost this worker its remaining tasks.
            messages = trim_history(messages)

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

    def _poll_audit_feed(self) -> str | None:
        """Check the audit feed for new findings and format them as a correction prompt."""
        if self.feed is None:
            return None
        findings = self.feed.poll(self.agent_id)
        if not findings:
            return None

        self.event_log.emit(
            phase=Phase.BUILD.value,
            event_type="audit.feedback",
            summary=f"[{self.agent_id}] Received {len(findings)} audit finding(s)",
            data={"agent_id": self.agent_id, "findings": findings},
        )

        lines = "\n".join(f"- [{f.get('type', 'issue')}] {f.get('message', '')}" for f in findings)
        return (
            f"⚠ AUDIT ALERT — the audit agent found issue(s) in the workspace:\n{lines}\n"
            "If any of these relate to files you wrote or your assigned tasks, "
            "fix them now before continuing. Otherwise, continue your tasks."
        )

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        task_map: dict[str, PlanTask],
    ) -> list[Message]:
        """Execute tool calls: read-only file ops in parallel, mutations and
        shell sequentially so same-file edits and dependent commands don't race."""
        results: list[Message] = []

        # Read-only file ops are safe to run concurrently; mutations are not
        # (edit_file is read-modify-write, so two edits to one file could lose
        # a change), and shell commands may depend on each other.
        virtual_calls: list[ToolCall] = []
        read_calls: list[ToolCall] = []
        sequential_calls: list[ToolCall] = []

        for tc in tool_calls:
            if tc.name == "mark_task_complete":
                virtual_calls.append(tc)
            elif tc.name in ("read_file", "list_directory", "search_files", "find_files"):
                read_calls.append(tc)
            else:
                # write_file, edit_file, create_directory, shell, git, ...
                sequential_calls.append(tc)

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

        # Execute read-only file operations in parallel
        if read_calls:
            read_coros = [self._dispatch_and_wrap(tc) for tc in read_calls]
            results.extend(await asyncio.gather(*read_coros))

        # Execute mutations and shell commands sequentially
        for tc in sequential_calls:
            if self.ui:
                self.ui.tool_activity(tc.name, tool_summary(tc.name, tc.arguments), self.deadline)
            result = await self.dispatcher.dispatch(tc.name, tc.arguments, self.context)
            results.append(
                Message(
                    role="tool",
                    content=truncate_tool_output(result.display or json.dumps(result.data)),
                    tool_call_id=tc.id,
                )
            )

        return results

    async def _dispatch_and_wrap(self, tc: ToolCall) -> Message:
        """Dispatch a tool call and wrap the result as a Message."""
        if self.ui:
            self.ui.tool_activity(tc.name, tool_summary(tc.name, tc.arguments), self.deadline)
        result = await self.dispatcher.dispatch(tc.name, tc.arguments, self.context)
        return Message(
            role="tool",
            content=truncate_tool_output(result.display or json.dumps(result.data)),
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
        feed: AuditFeed | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.context = context
        self.event_log = event_log
        self.deadline = deadline
        self.ui = ui
        self.feed = feed if feed is not None else AuditFeed()

    async def run_continuous(self, check_interval: float = 20.0) -> list[dict[str, Any]]:
        """Run periodic validation checks. Returns list of findings.

        New findings are published to the audit feed so worker agents can
        pick them up and fix issues mid-build.
        """
        # Wait for workers to write some files first
        await asyncio.sleep(min(check_interval, self.deadline.phase_remaining(Phase.BUILD) / 3))

        while not self.deadline.is_expired() and self.deadline.phase_remaining(Phase.BUILD) > 30:
            check_result = await self._run_checks()
            if check_result:
                # Publish to the shared feed; only genuinely new findings
                # (deduplicated by type + message) get logged and surfaced
                new_findings = self.feed.publish(check_result)
                if new_findings:
                    self.event_log.emit(
                        phase=Phase.BUILD.value,
                        event_type="audit.finding",
                        summary=f"Audit found {len(new_findings)} new issue(s)",
                        data={"findings": new_findings},
                    )
            await asyncio.sleep(check_interval)

        return self.feed.all_findings

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

        findings.extend(await self._syntax_findings(workspace))

        if self.ui and not findings:
            self.ui.tool_activity("audit", "checks passed", self.deadline)

        return findings

    async def _syntax_findings(self, workspace: Path) -> list[dict[str, Any]]:
        """Compile-check source files so broken code is caught during BUILD.

        Existence checks alone let a worker keep building on a file that cannot
        even parse; a syntax error surfaced now is fed back through the audit
        feed while there is still time to fix it.
        """
        findings: list[dict[str, Any]] = []

        if any(workspace.rglob("*.py")):
            # compileall is quiet on success and names the offending file on failure.
            result = await self.dispatcher.dispatch(
                "exec_command",
                {"command": "python3 -m compileall -q .", "timeout": 20},
                self.context,
            )
            if result.status == "error":
                findings.append(
                    {
                        "type": "syntax_error",
                        "message": f"Python file(s) fail to compile: {result.display[:300]}",
                    }
                )

        js_files = [p for p in workspace.rglob("*.js") if "node_modules" not in p.parts]
        if js_files:
            rel = " ".join(f"'{p.relative_to(workspace)}'" for p in js_files[:20])
            result = await self.dispatcher.dispatch(
                "exec_command",
                # Skip silently when node isn't available rather than crying wolf.
                {
                    "command": f"command -v node >/dev/null || exit 0; node --check {rel}",
                    "timeout": 20,
                },
                self.context,
            )
            if result.status == "error":
                findings.append(
                    {
                        "type": "syntax_error",
                        "message": f"JavaScript file(s) fail to parse: {result.display[:300]}",
                    }
                )

        return findings
