"""Main orchestrator — wires all phases together."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    TokenTracker,
    VerifyPhase,
)
from noscope.planning.models import PlanOutput
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
from noscope.tools.shell import ShellTool, build_execution_env
from noscope.ui.console import ConsoleUI


class Orchestrator:
    """Orchestrates the full NoScope run lifecycle."""

    def __init__(self, settings: NoscopeSettings, console: Console | None = None) -> None:
        self.settings = settings
        self.provider = create_provider(settings)
        self.ui = ConsoleUI(console)
        self._model = settings.default_model or self._default_model_for_provider()

    def _default_model_for_provider(self) -> str:
        if self.settings.default_provider == "openai":
            return "gpt-4o"
        return "claude-sonnet-4-20250514"

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
        # Token tracking for cost calculation
        tokens = TokenTracker()

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
        dispatcher.register_all(
            [
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
            ]
        )

        tasks: list[Any] = []
        acceptance_results: list[dict[str, Any]] = []
        plan_output: PlanOutput | None = None
        verify_data: tuple[bool, str] | None = None

        try:
            # 5. PLAN phase
            self.ui.phase_banner(
                Phase.PLAN, "Generating build plan...", deadline.format_remaining()
            )
            plan_phase = PlanPhase()
            plan_output = await plan_phase.run(
                spec, self.provider, event_log, deadline, tokens=tokens
            )
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
                secrets=_runtime_secrets(self.settings),
                danger_mode=self.settings.danger_mode,
            )

            build_phase = BuildPhase()
            tasks = await build_phase.run(
                plan_output,
                self.provider,
                dispatcher,
                tool_context,
                event_log,
                deadline,
                ui=self.ui,
                tokens=tokens,
            )
            completed = sum(1 for t in tasks if t.completed)
            self.ui.console.print(f"  Completed [cyan]{completed}/{len(tasks)}[/cyan] tasks")

            # 9. HARDEN phase
            self.ui.phase_banner(
                Phase.HARDEN, "Running acceptance checks...", deadline.format_remaining()
            )
            harden_phase = HardenPhase()
            acceptance_results = await harden_phase.run(
                plan_output,
                spec,
                dispatcher,
                tool_context,
                event_log,
                deadline,
                ui=self.ui,
            )
            self.ui.acceptance_results(acceptance_results)

            # 10. VERIFY phase — confirm MVP actually runs
            if not deadline.is_expired():
                self.ui.phase_banner(
                    Phase.VERIFY, "Verifying MVP runs...", deadline.format_remaining()
                )
                verify_phase = VerifyPhase()
                verified, verify_msg = await verify_phase.run(
                    spec,
                    self.provider,
                    dispatcher,
                    tool_context,
                    event_log,
                    deadline,
                    ui=self.ui,
                    tokens=tokens,
                )
                verify_data = (verified, verify_msg)
                self.ui.verify_result(verified, verify_msg)

        except Exception as e:
            event_log.emit(
                phase="ERROR",
                event_type="run.error",
                summary=f"Run error: {e}",
                data={"error": str(e), "type": type(e).__name__},
            )
            self.ui.console.print(f"\n[red]Error:[/red] {e}")
            if not tasks and plan_output is not None:
                tasks = plan_output.tasks

        # 11. HANDOFF phase (ALWAYS runs)
        self.ui.phase_banner(Phase.HANDOFF, "Generating report...", deadline.format_remaining())
        handoff_phase = HandoffPhase()
        try:
            await handoff_phase.run(
                spec,
                plan_output or _empty_plan(),
                tasks,
                acceptance_results,
                self.provider,
                event_log,
                deadline,
                run_dir.handoff_path,
                tokens=tokens,
                workspace=workspace,
                verify_result=verify_data,
            )
        except Exception as e:
            event_log.emit(
                phase="HANDOFF",
                event_type="handoff.error",
                summary=f"Handoff report generation failed: {e}",
                data={"error": str(e), "type": type(e).__name__},
            )
            run_dir.handoff_path.write_text(
                f"# Handoff Report: {spec.name}\n\nRun failed with error: {e}\n",
                encoding="utf-8",
            )

        event_log.emit(
            phase="DONE",
            event_type="run.complete",
            summary="Run complete",
            data={
                "run_dir": str(run_dir.path),
                "input_tokens": tokens.input_tokens,
                "output_tokens": tokens.output_tokens,
            },
        )
        event_log.close()

        # Detect launch info
        verified_ok = verify_data[0] if verify_data else None
        verify_msg = verify_data[1] if verify_data else ""
        launch_cmd, launch_url = _detect_launch(workspace)

        completed_count = sum(1 for t in tasks if t.completed) if tasks else 0
        checks_passed = sum(1 for r in acceptance_results if r.get("passed"))

        # Show final summary — ALWAYS
        provider_name = self.settings.default_provider or "anthropic"
        self.ui.final_summary(
            spec_name=spec.name,
            timebox=spec.timebox,
            workspace=workspace,
            run_dir=run_dir.path,
            tasks_completed=completed_count,
            tasks_total=len(tasks),
            checks_passed=checks_passed,
            checks_total=len(acceptance_results),
            verified=verified_ok,
            verify_msg=verify_msg,
            launch_url=launch_url if launch_cmd else None,
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
            provider=provider_name,
            model=self._model,
        )

        # 12. LAUNCH — start the app for the user if verified
        if verified_ok and launch_cmd:
            self.ui.launch_app(workspace, launch_cmd, launch_url)
            await _run_server(launch_cmd, workspace)

        return run_dir.path


def _detect_launch(workspace: Path) -> tuple[str | None, str]:
    """Detect how to launch the built app. Returns (command, url)."""
    # Python/Flask
    app_py = workspace / "app.py"
    main_py = workspace / "main.py"
    manage_py = workspace / "manage.py"
    package_json = workspace / "package.json"

    if app_py.exists():
        # Check if it's Flask/FastAPI
        content = app_py.read_text(encoding="utf-8", errors="replace")
        if "flask" in content.lower() or "Flask" in content:
            return "python3 app.py", "http://localhost:5000"
        if "fastapi" in content.lower() or "FastAPI" in content:
            return "python3 -m uvicorn app:app --host 0.0.0.0 --port 8000", "http://localhost:8000"
        return "python3 app.py", "http://localhost:5000"

    if main_py.exists():
        content = main_py.read_text(encoding="utf-8", errors="replace")
        if "flask" in content.lower() or "fastapi" in content.lower():
            return "python3 main.py", "http://localhost:5000"
        return "python3 main.py", "http://localhost:8000"

    if manage_py.exists():
        return "python3 manage.py runserver", "http://localhost:8000"

    if package_json.exists():
        return "npm start", "http://localhost:3000"

    return None, ""


async def _run_server(command: str, workspace: Path) -> None:
    """Start the server and let the user interact with it. Blocks until Ctrl+C."""
    import asyncio
    import signal

    env = build_execution_env()

    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(workspace),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        # Stream output until user hits Ctrl+C
        while True:
            line = await proc.stdout.readline()  # type: ignore[union-attr]
            if not line:
                break
            print(line.decode("utf-8", errors="replace"), end="")
    except (KeyboardInterrupt, asyncio.CancelledError):
        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()


def _empty_plan() -> PlanOutput:
    """Return a minimal plan for error fallback."""
    from noscope.planning.models import PlanOutput

    return PlanOutput()


def _runtime_secrets(settings: NoscopeSettings) -> dict[str, str]:
    """Provide known runtime secrets for output redaction."""
    secrets: dict[str, str] = {}
    if settings.anthropic_api_key:
        secrets["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if settings.openai_api_key:
        secrets["OPENAI_API_KEY"] = settings.openai_api_key
    return secrets
