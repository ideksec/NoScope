"""Main orchestrator — wires all phases together."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from noscope.capabilities import CapabilityStore
from noscope.config.settings import NoscopeSettings
from noscope.deadline import Deadline, Phase
from noscope.llm import create_provider
from noscope.logging.events import EventLog, RunDir
from noscope.phases import (
    BuildPhase,
    HandoffPhase,
    HardenPhase,
    PlanPhase,
    RequestPhase,
)
from noscope.spec.contract import generate_contract
from noscope.spec.models import SpecInput
from noscope.spec.parser import parse_spec
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher
from noscope.tools.filesystem import (
    CreateDirectoryTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from noscope.tools.git import (
    GitAddTool,
    GitCommitTool,
    GitDiffTool,
    GitInitTool,
    GitStatusTool,
)
from noscope.tools.shell import ShellTool
from noscope.ui.console import ConsoleUI


class Orchestrator:
    """Orchestrates the full NoScope run lifecycle."""

    def __init__(self, settings: NoscopeSettings, console: Console | None = None) -> None:
        self.settings = settings
        self.provider = create_provider(settings)
        self.ui = ConsoleUI(console)

    async def run(
        self,
        spec_path: Path | None = None,
        spec_input: SpecInput | None = None,
        timebox: str | None = None,
        output_dir: Path | None = None,
        sandbox: bool = False,
        auto_approve: bool = False,
    ) -> Path:
        """Execute a full NoScope run. Returns the run directory path."""
        # 1. Parse spec — from file or pre-built SpecInput
        if spec_input is not None:
            spec = spec_input
        elif spec_path is not None:
            spec = parse_spec(spec_path)
        else:
            raise ValueError("Either spec_path or spec_input must be provided")

        if timebox:
            from noscope.spec.models import _parse_duration

            spec.timebox = timebox
            spec.timebox_seconds = _parse_duration(timebox)

        # 2. Set up workspace
        workspace = output_dir or Path(f"./out/{spec.name.lower().replace(' ', '-')}")
        workspace = workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        # 3. Set up run directory and event log
        run_dir = RunDir()
        event_log = EventLog(run_dir)

        event_log.emit(
            phase="INIT",
            event_type="run.start",
            summary=f"Starting run: {spec.name}",
            data={
                "spec_path": str(spec_path),
                "workspace": str(workspace),
                "timebox": spec.timebox,
                "timebox_seconds": spec.timebox_seconds,
            },
        )

        # 4. Start deadline
        deadline = Deadline(spec.timebox_seconds)

        # Set up tools
        dispatcher = ToolDispatcher()
        dispatcher.register_all([
            ReadFileTool(),
            WriteFileTool(),
            ListDirectoryTool(),
            CreateDirectoryTool(),
            ShellTool(),
            GitInitTool(),
            GitStatusTool(),
            GitAddTool(),
            GitCommitTool(),
            GitDiffTool(),
        ])

        try:
            # 5. PLAN phase
            self.ui.phase_banner(Phase.PLAN, "Generating build plan...", deadline.format_remaining())
            plan_phase = PlanPhase()
            plan_output = await plan_phase.run(spec, self.provider, event_log, deadline)
            self.ui.console.print(
                f"  Plan: [cyan]{len(plan_output.tasks)}[/cyan] tasks, "
                f"[cyan]{len(plan_output.requested_capabilities)}[/cyan] capabilities requested"
            )

            # Save plan
            run_dir.plan_path.write_text(
                json.dumps(plan_output.model_dump(), indent=2), encoding="utf-8"
            )

            # 6. REQUEST phase
            self.ui.phase_banner(
                Phase.REQUEST, "Reviewing capabilities...", deadline.format_remaining()
            )
            if not auto_approve:
                self.ui.capability_table(plan_output.requested_capabilities)
            request_phase = RequestPhase()
            grants = await request_phase.run(
                plan_output, event_log, deadline, auto_approve=auto_approve
            )
            approved = sum(1 for g in grants if g.approved)
            self.ui.console.print(f"  Approved [cyan]{approved}/{len(grants)}[/cyan] capabilities")

            # Save grants
            run_dir.capabilities_grant_path.write_text(
                json.dumps([g.model_dump() for g in grants], indent=2), encoding="utf-8"
            )

            # 7. Write contract
            cap_store = CapabilityStore(grants)
            generate_contract(spec, plan_output, grants, run_dir.contract_path)

            # 8. BUILD phase
            self.ui.phase_banner(Phase.BUILD, "Building MVP...", deadline.format_remaining())
            tool_context = ToolContext(
                workspace=workspace,
                capabilities=cap_store,
                event_log=event_log,
                deadline=deadline,
                secrets={},
                danger_mode=self.settings.danger_mode,
            )

            build_phase = BuildPhase()
            tasks = await build_phase.run(
                plan_output, self.provider, dispatcher, tool_context, event_log, deadline
            )
            completed = sum(1 for t in tasks if t.completed)
            self.ui.console.print(
                f"  Completed [cyan]{completed}/{len(tasks)}[/cyan] tasks"
            )

            # 9. HARDEN phase
            self.ui.phase_banner(Phase.HARDEN, "Running acceptance checks...", deadline.format_remaining())
            harden_phase = HardenPhase()
            acceptance_results = await harden_phase.run(
                plan_output, spec, dispatcher, tool_context, event_log, deadline
            )
            self.ui.acceptance_results(acceptance_results)

        except Exception as e:
            event_log.emit(
                phase="ERROR",
                event_type="run.error",
                summary=f"Run error: {e}",
                data={"error": str(e), "type": type(e).__name__},
            )
            self.ui.console.print(f"\n[red]Error:[/red] {e}")
            # Still generate handoff
            tasks = plan_output.tasks if "plan_output" in locals() else []
            acceptance_results = []

        # 10. HANDOFF phase (ALWAYS runs)
        self.ui.phase_banner(Phase.HANDOFF, "Generating report...", deadline.format_remaining())
        handoff_phase = HandoffPhase()
        try:
            plan_for_handoff = plan_output if "plan_output" in locals() else None
            await handoff_phase.run(
                spec,
                plan_for_handoff or _empty_plan(),
                tasks,
                acceptance_results,
                self.provider,
                event_log,
                deadline,
                run_dir.handoff_path,
            )
        except Exception as e:
            # Last resort fallback
            run_dir.handoff_path.write_text(
                f"# Handoff Report: {spec.name}\n\nRun failed with error: {e}\n",
                encoding="utf-8",
            )

        event_log.emit(
            phase="DONE",
            event_type="run.complete",
            summary="Run complete",
            data={"run_dir": str(run_dir.path)},
        )
        event_log.close()

        return run_dir.path


def _empty_plan() -> object:
    """Return a minimal plan for error fallback."""
    from noscope.planning.models import PlanOutput

    return PlanOutput()
