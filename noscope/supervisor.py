"""Multi-agent supervisor — orchestrates parallel build agents and audit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from noscope.agents import AuditAgent, BuildAgent
from noscope.deadline import Deadline, Phase
from noscope.llm.base import LLMProvider
from noscope.logging.events import EventLog
from noscope.planning.models import PlanOutput, PlanTask
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher

if TYPE_CHECKING:
    from noscope.phases import TokenTracker
    from noscope.ui.console import ConsoleUI

# Maximum parallel workers (beyond setup agent)
MAX_WORKERS = 3


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
                self.ui.tool_activity("supervisor", "running setup agent...", self.deadline)

            setup_agent = BuildAgent(
                agent_id="setup",
                provider=self.provider,
                dispatcher=self.dispatcher,
                context=self.context,
                event_log=self.event_log,
                deadline=self.deadline,
                ui=self.ui,
                tokens=self.tokens,
            )
            setup_prompt = self._setup_prompt(plan, workspace)
            await setup_agent.run(setup_tasks, setup_prompt)

            self.event_log.emit(
                phase=Phase.BUILD.value,
                event_type="supervisor.setup_done",
                summary=f"Setup complete: {sum(1 for t in setup_tasks if t.completed)}/{len(setup_tasks)} tasks",
            )

        # Phase 2: Parallel workers on remaining tasks
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
            )
            audit_coro = audit.run_continuous()

            # Run workers and audit concurrently
            await asyncio.gather(*worker_coros, audit_coro, return_exceptions=True)

        # Summary
        completed = sum(1 for t in all_tasks if t.completed)
        self.event_log.emit(
            phase=Phase.BUILD.value,
            event_type="supervisor.done",
            summary=f"Build complete: {completed}/{len(all_tasks)} tasks done",
            data={"completed": completed, "total": len(all_tasks)},
        )

        return all_tasks

    def _split_setup(self, tasks: list[PlanTask]) -> tuple[list[PlanTask], list[PlanTask]]:
        """Split off the first task (setup/scaffolding) from the rest."""
        setup: list[PlanTask] = []
        remaining: list[PlanTask] = []

        for t in tasks:
            # First task, or tasks with no dependencies, go to setup
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

        Uses task dependencies if available, otherwise round-robin assignment.
        Limits to MAX_WORKERS streams.
        """
        if not tasks:
            return []

        # Group by dependency chains
        streams: list[list[PlanTask]] = []
        assigned: set[str] = set()

        # First pass: group tasks that depend on each other
        for task in tasks:
            if task.id in assigned:
                continue

            chain = [task]
            assigned.add(task.id)

            # Find tasks that depend on this one
            for other in tasks:
                if other.id not in assigned and task.id in other.depends_on:
                    chain.append(other)
                    assigned.add(other.id)

            streams.append(chain)

        # If we have more streams than workers, merge small ones
        while len(streams) > MAX_WORKERS:
            # Merge the two shortest streams
            streams.sort(key=len)
            smallest = streams.pop(0)
            streams[0] = smallest + streams[0]

        # If we have unassigned tasks, distribute round-robin
        unassigned = [t for t in tasks if t.id not in assigned]
        for i, task in enumerate(unassigned):
            idx = i % len(streams) if streams else 0
            if not streams:
                streams.append([])
            streams[idx].append(task)

        return streams

    def _setup_prompt(self, plan: PlanOutput, workspace: Path) -> str:
        return f"""\
You are the SETUP agent. Your job is to create the project foundation FAST.

Workspace: {workspace}

RULES:
- Create project structure and install dependencies
- NEVER use interactive scaffolding (create-react-app, npm create, etc)
- Write package.json / requirements.txt MANUALLY, then npm install / pip install
- Use "npm init -y" if you need a basic package.json
- Use "python3 -m pip install" instead of bare "pip"
- Create essential config files (tsconfig.json, etc) by writing them directly
- Call mark_task_complete when done
- Be FAST — other agents are waiting for you to finish before they can start

MVP definition: {json.dumps(plan.mvp_definition)}
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
