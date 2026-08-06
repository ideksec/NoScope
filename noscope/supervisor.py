"""Multi-agent supervisor — orchestrates parallel build agents and audit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from noscope.agents import AuditAgent, AuditFeed, BuildAgent
from noscope.deadline import Deadline, Phase
from noscope.llm.base import LLMProvider
from noscope.logging.events import EventLog
from noscope.planning.models import PlanOutput, PlanTask
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher

if TYPE_CHECKING:
    from noscope.phases import TokenTracker
    from noscope.ui.console import ConsoleUI

# Maximum parallel workers (beyond setup agent).
# Keep at 2 to avoid API rate limits with concurrent LLM streams.
MAX_WORKERS = 2


class Supervisor:
    """Orchestrates multiple build agents for parallel task execution.

    Execution model:
    1. Setup agent runs first (project scaffolding, deps) — must finish before workers
    2. Worker agents run in parallel on partitioned task sets
    3. Audit agent runs concurrently, validating build quality
    """

    def __init__(
        self,
        provider: LLMProvider,
        dispatcher: ToolDispatcher,
        context: ToolContext,
        event_log: EventLog,
        deadline: Deadline,
        ui: ConsoleUI | None = None,
        tokens: TokenTracker | None = None,
    ) -> None:
        self.provider = provider
        self.dispatcher = dispatcher
        self.context = context
        self.event_log = event_log
        self.deadline = deadline
        self.ui = ui
        self.tokens = tokens

    async def run(self, plan: PlanOutput, workspace: Path) -> list[PlanTask]:
        """Execute the build plan with parallel agents. Returns all tasks."""
        all_tasks = plan.tasks
        if not all_tasks:
            return all_tasks

        self.deadline.advance_phase(Phase.BUILD)
        self.event_log.emit(
            phase=Phase.BUILD.value,
            event_type="supervisor.start",
            summary=f"Supervisor starting with {len(all_tasks)} tasks",
            data={"task_count": len(all_tasks)},
        )

        # Phase 1: Setup — first task always runs alone (project scaffolding)
        setup_tasks, remaining_tasks = self._split_setup(all_tasks)

        if setup_tasks:
            if self.ui:
                self.ui.tool_activity(
                    "supervisor", "running parallel setup agents...", self.deadline
                )

            # Split setup into two parallel agents: structure + deps
            structure_agent = BuildAgent(
                agent_id="setup-structure",
                provider=self.provider,
                dispatcher=self.dispatcher,
                context=self.context,
                event_log=self.event_log,
                deadline=self.deadline,
                ui=self.ui,
                tokens=self.tokens,
            )
            deps_agent = BuildAgent(
                agent_id="setup-deps",
                provider=self.provider,
                dispatcher=self.dispatcher,
                context=self.context,
                event_log=self.event_log,
                deadline=self.deadline,
                ui=self.ui,
                tokens=self.tokens,
            )

            structure_prompt = self._setup_structure_prompt(plan, workspace)
            deps_prompt = self._setup_deps_prompt(plan, workspace)

            # Run both in parallel — one writes files, the other installs deps
            setup_results = await asyncio.gather(
                structure_agent.run(setup_tasks, structure_prompt),
                deps_agent.run([], deps_prompt),  # No tasks to mark — just install
                return_exceptions=True,
            )

            # Log any setup agent failures
            labels = ["setup-structure", "setup-deps"]
            setup_failed = False
            for i, result in enumerate(setup_results):
                if isinstance(result, BaseException):
                    setup_failed = True
                    self.event_log.emit(
                        phase=Phase.BUILD.value,
                        event_type="agent.error",
                        summary=f"Setup agent {labels[i]} failed: {result}",
                        data={"agent": labels[i], "error": str(result)},
                    )

            # Only mark setup complete if the structure agent succeeded
            if not setup_failed:
                for t in setup_tasks:
                    if not t.completed:
                        t.completed = True

            self.event_log.emit(
                phase=Phase.BUILD.value,
                event_type="supervisor.setup_done",
                summary=f"Setup {'complete' if not setup_failed else 'FAILED'} (parallel structure + deps)",
            )

        # Phase 2: Parallel workers on remaining tasks
        audit_findings: list[object] = []
        if remaining_tasks and not self.deadline.is_expired():
            streams = self._partition_tasks(remaining_tasks)
            num_workers = len(streams)

            if self.ui:
                self.ui.tool_activity(
                    "supervisor",
                    f"launching {num_workers} parallel workers + audit agent...",
                    self.deadline,
                )

            self.event_log.emit(
                phase=Phase.BUILD.value,
                event_type="supervisor.parallel_start",
                summary=f"Launching {num_workers} workers + audit agent",
                data={
                    "workers": num_workers,
                    "streams": [[t.id for t in stream] for stream in streams],
                },
            )

            # Shared feed: audit publishes findings, workers poll and fix
            feed = AuditFeed()

            worker_coros = []
            for i, stream in enumerate(streams):
                agent = BuildAgent(
                    agent_id=f"worker-{i}",
                    provider=self.provider,
                    dispatcher=self.dispatcher,
                    context=self.context,
                    event_log=self.event_log,
                    deadline=self.deadline,
                    ui=self.ui,
                    tokens=self.tokens,
                    feed=feed,
                )
                prompt = self._worker_prompt(plan, workspace, stream, i)
                worker_coros.append(agent.run(stream, prompt))

            # Audit agent runs in parallel
            audit = AuditAgent(
                dispatcher=self.dispatcher,
                context=self.context,
                event_log=self.event_log,
                deadline=self.deadline,
                ui=self.ui,
                feed=feed,
            )
            audit_coro = audit.run_continuous()

            # Run workers and audit concurrently
            gather_results: list[object] = list(
                await asyncio.gather(*worker_coros, audit_coro, return_exceptions=True)
            )

            # Log any worker exceptions (don't let failures go silent)
            for i, result in enumerate(gather_results):  # type: ignore[assignment]
                if isinstance(result, BaseException):
                    label = f"worker-{i}" if i < len(worker_coros) else "audit"
                    self.event_log.emit(
                        phase=Phase.BUILD.value,
                        event_type="agent.error",
                        summary=f"Agent {label} failed: {result}",
                        data={"agent": label, "error": str(result), "type": type(result).__name__},
                    )

            # The final gather slot is the audit agent's findings
            last_result = gather_results[-1] if gather_results else None
            if isinstance(last_result, list):
                audit_findings = last_result

        # Summary
        completed = sum(1 for t in all_tasks if t.completed)
        self.event_log.emit(
            phase=Phase.BUILD.value,
            event_type="supervisor.done",
            summary=f"Build complete: {completed}/{len(all_tasks)} tasks done"
            + (f", {len(audit_findings)} audit finding(s)" if audit_findings else ""),
            data={
                "completed": completed,
                "total": len(all_tasks),
                "audit_findings": audit_findings,
            },
        )

        return all_tasks

    def _split_setup(self, tasks: list[PlanTask]) -> tuple[list[PlanTask], list[PlanTask]]:
        """Split off the first task (setup/scaffolding) from the rest."""
        setup: list[PlanTask] = []
        remaining: list[PlanTask] = []

        for t in tasks:
            # First task matching t1, "setup", or "scaffold" in title goes to setup
            if not setup and (
                t.id == "t1" or "setup" in t.title.lower() or "scaffold" in t.title.lower()
            ):
                setup.append(t)
            else:
                remaining.append(t)

        # If nothing matched as setup, use the first task
        if not setup and tasks:
            setup = [tasks[0]]
            remaining = tasks[1:]

        return setup, remaining

    def _partition_tasks(self, tasks: list[PlanTask]) -> list[list[PlanTask]]:
        """Partition tasks into parallel work streams.

        Builds the full dependency graph and groups each transitively-connected
        component into a single stream, so a worker never waits on files owned
        by another worker. Streams are ordered topologically (plan order as
        tiebreak) and merged down to at most MAX_WORKERS.
        """
        if not tasks:
            return []

        task_ids = {t.id for t in tasks}
        order = {t.id: i for i, t in enumerate(tasks)}

        # Union-find over dependency edges (ignoring deps outside this task
        # set, e.g. the already-completed setup task)
        parent = {t.id: t.id for t in tasks}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for task in tasks:
            for dep in task.depends_on:
                if dep in task_ids:
                    union(dep, task.id)

        # Collect components, preserving plan order within and across them
        components: dict[str, list[PlanTask]] = {}
        for task in tasks:
            components.setdefault(find(task.id), []).append(task)
        streams = [self._topo_sort(chain) for chain in components.values()]

        # Merge the smallest streams until we're within the worker limit
        while len(streams) > MAX_WORKERS:
            streams.sort(key=len)
            smallest = streams.pop(0)
            merged = sorted(smallest + streams[0], key=lambda t: order[t.id])
            streams[0] = merged

        return streams

    @staticmethod
    def _topo_sort(tasks: list[PlanTask]) -> list[PlanTask]:
        """Order tasks so dependencies come before dependents (plan order as tiebreak).

        Tasks in a dependency cycle fall back to plan order at the end.
        """
        task_ids = {t.id for t in tasks}
        emitted: set[str] = set()
        result: list[PlanTask] = []
        pending = list(tasks)

        while pending:
            ready = [
                t
                for t in pending
                if all(dep in emitted or dep not in task_ids for dep in t.depends_on)
            ]
            if not ready:  # dependency cycle — emit remaining in plan order
                result.extend(pending)
                break
            for t in ready:
                emitted.add(t.id)
                result.append(t)
            pending = [t for t in pending if t.id not in emitted]

        return result

    def _setup_structure_prompt(self, plan: PlanOutput, workspace: Path) -> str:
        return f"""\
You are the STRUCTURE agent. Write all project files FAST. Another agent is installing deps in parallel.

Workspace: {workspace}

YOUR JOB:
- Write package.json / requirements.txt with all needed dependencies
- Write the main app entry point (app.py, server.js, etc)
- Write essential config files (tsconfig.json, .env, etc)
- Create directory structure (templates/, static/, src/, etc)
- Do NOT run npm install or pip install — the deps agent handles that
- NEVER use interactive scaffolding (create-react-app, npm create, etc)
- Call mark_task_complete when all files are written
- Be FAST — other agents are waiting

MVP definition: {json.dumps(plan.mvp_definition)}
"""

    def _setup_deps_prompt(self, plan: PlanOutput, workspace: Path) -> str:
        return f"""\
You are the DEPS agent. Install project dependencies FAST. Another agent is writing files in parallel.

Workspace: {workspace}

YOUR JOB:
1. Wait briefly (2-3 seconds) for the structure agent to write package.json or requirements.txt
2. Check which dependency file exists (list_directory once)
3. Install: "python3 -m pip install -r requirements.txt" or "npm install"
4. If the file doesn't exist yet, wait 5 more seconds and check again
5. Once deps are installed, you're done — no need to call mark_task_complete

RULES:
- Do NOT write any files — the structure agent handles that
- Do NOT use interactive scaffolding tools
- Use "python3 -m pip install" not bare "pip"
- If both package.json and requirements.txt exist, install BOTH
"""

    def _worker_prompt(
        self, plan: PlanOutput, workspace: Path, tasks: list[PlanTask], worker_idx: int
    ) -> str:
        task_ids = ", ".join(t.id for t in tasks)
        return f"""\
You are worker agent {worker_idx}. You are one of several agents building this project IN PARALLEL.

Workspace: {workspace}
Your assigned tasks: {task_ids}

Other agents are working on different tasks simultaneously. Focus ONLY on your assigned tasks.

RULES:
- The project structure and dependencies are already set up — do NOT reinstall or reconfigure
- Write code for YOUR tasks only
- Do NOT modify files that other agents might be working on
- Call mark_task_complete after finishing each task
- If you need a file that doesn't exist yet, create it — another agent may not have written it yet
- NEVER use interactive scaffolding tools (create-react-app, npm create, etc)
- Use "python3" not "python", "python3 -m pip" not "pip"
- Build something impressive — good styling, thoughtful UX

MVP definition: {json.dumps(plan.mvp_definition)}
Exclusions: {json.dumps(plan.exclusions)}
"""
