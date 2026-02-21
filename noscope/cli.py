"""CLI entry point using Typer."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console

from noscope import __version__

app = typer.Typer(
    name="noscope",
    help="Time-boxed autonomous agent orchestration tool",
    no_args_is_help=True,
)
console = Console()


@app.command()
def run(
    spec: Path = typer.Option(..., "--spec", "-s", help="Path to spec file"),
    time: str = typer.Option("30m", "--time", "-t", help="Timebox duration (e.g., 5m, 1h)"),
    dir: Path = typer.Option(None, "--dir", "-d", help="Output directory for built project"),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run commands in Docker sandbox"),
    provider: str = typer.Option(None, "--provider", "-p", help="LLM provider (anthropic|openai)"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model override"),
    danger: bool = typer.Option(False, "--danger", help="Enable danger mode (bypass safety filters)"),
    auto_approve: bool = typer.Option(False, "--yes", "-y", help="Auto-approve all capability requests"),
    tui: bool = typer.Option(False, "--tui", help="Use full TUI interface"),
) -> None:
    """Build an MVP from a spec within a timebox."""
    from noscope.config.settings import load_settings
    from noscope.ui.console import ConsoleUI

    ui = ConsoleUI(console)

    if danger:
        ui.danger_warning()

    try:
        settings = load_settings(
            default_provider=provider,
            default_model=model,
            danger_mode=danger,
        )
    except ValueError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(1) from None

    ui.header(spec.name, time)

    from noscope.orchestrator import Orchestrator

    orchestrator = Orchestrator(settings, console=console)
    run_dir = asyncio.run(
        orchestrator.run(
            spec_path=spec,
            timebox=time,
            output_dir=dir,
            sandbox=sandbox,
            auto_approve=auto_approve,
        )
    )

    ui.complete(run_dir)


@app.command()
def doctor() -> None:
    """Check environment for NoScope requirements."""
    console.print(f"[bold]NoScope Doctor[/bold] v{__version__}\n")

    checks = []

    # Python version
    v = sys.version_info
    ok = v >= (3, 12)
    checks.append(("Python ≥ 3.12", ok, f"{v.major}.{v.minor}.{v.micro}"))

    # API keys — check env vars and .env file
    import os

    from dotenv import load_dotenv

    load_dotenv()
    has_anthropic = bool(os.environ.get("NOSCOPE_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("NOSCOPE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    checks.append(("Anthropic API key", has_anthropic, "set" if has_anthropic else "not set"))
    checks.append(("OpenAI API key", has_openai, "set" if has_openai else "not set"))
    checks.append(("At least one API key", has_anthropic or has_openai, ""))

    # Git
    git_ok = shutil.which("git") is not None
    checks.append(("git", git_ok, shutil.which("git") or "not found"))

    # Docker
    docker_ok = shutil.which("docker") is not None
    checks.append(("docker (optional)", docker_ok, shutil.which("docker") or "not found"))

    # uv
    uv_ok = shutil.which("uv") is not None
    checks.append(("uv (optional)", uv_ok, shutil.which("uv") or "not found"))

    for name, ok, detail in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        detail_str = f" ({detail})" if detail else ""
        console.print(f"  {icon} {name}{detail_str}")

    all_ok = all(ok for _, ok, _ in checks if "optional" not in _)
    console.print()
    if all_ok:
        console.print("[green]All checks passed![/green]")
    else:
        console.print("[yellow]Some checks failed. Fix the issues above.[/yellow]")


@app.command()
def init() -> None:
    """Create a new spec file template. (stub — coming in v0.2)"""
    template = '''---
name: "My Project"
timebox: "30m"
constraints:
  - "Use Python"
acceptance:
  - "cmd: python main.py"
  - "Output contains expected result"
---

# My Project

Describe what you want built here.
'''
    path = Path("spec.md")
    if path.exists():
        # Find a unique name
        for i in range(1, 100):
            path = Path(f"spec-{i}.md")
            if not path.exists():
                break

    path.write_text(template, encoding="utf-8")
    console.print(f"[green]Created {path}[/green] — edit it and run: noscope run --spec {path}")


@app.command()
def replay() -> None:
    """Replay a previous run. (stub — coming in v0.2)"""
    console.print("[yellow]Replay is not yet implemented. Coming in v0.2.[/yellow]")
    raise typer.Exit(0)


def main() -> None:
    app()
