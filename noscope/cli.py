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
    danger: bool = typer.Option(
        False, "--danger", help="Enable danger mode (bypass safety filters)"
    ),
    auto_approve: bool = typer.Option(
        False, "--yes", "-y", help="Auto-approve all capability requests"
    ),
    serve: bool = typer.Option(
        False, "--serve", help="After a verified build, launch the app (blocks until Ctrl+C)"
    ),
    token_budget: int = typer.Option(
        None, "--token-budget", help="Stop the build once this many total tokens are used"
    ),
    workers: int = typer.Option(None, "--workers", help="Parallel build workers (default 2)"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Exercise the whole pipeline with no API calls and no tokens spent",
    ),
) -> None:
    """Build an MVP from a spec within a timebox."""
    import os

    from noscope.config.settings import load_settings
    from noscope.ui.console import ConsoleUI

    ui = ConsoleUI(console)

    if danger:
        ui.danger_warning()

    if dry_run:
        # No provider is contacted, so settings needn't carry a real key.
        os.environ.setdefault("NOSCOPE_ANTHROPIC_API_KEY", "dry-run")
        console.print(
            "  [cyan]Dry run[/cyan] — no API calls, no tokens. "
            "Exercises the full pipeline end to end.\n"
        )

    try:
        settings = load_settings(
            default_provider=provider,
            default_model=model,
            danger_mode=danger,
            token_budget=token_budget,
            max_workers=workers,
        )
    except ValueError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(1) from None

    ui.header(spec.name, time)

    from noscope.orchestrator import Orchestrator

    orchestrator = Orchestrator(settings, console=console, dry_run=dry_run)
    asyncio.run(
        orchestrator.run(
            spec_path=spec,
            timebox=time,
            output_dir=dir,
            sandbox=sandbox,
            auto_approve=auto_approve,
            serve=serve,
        )
    )


@app.command()
def doctor(
    live: bool = typer.Option(
        False,
        "--live",
        help="Also make one tiny API call to prove the key and model actually work",
    ),
) -> None:
    """Check environment for NoScope requirements."""
    console.print(f"[bold]NoScope Doctor[/bold] v{__version__}\n")

    # (name, ok, detail, required). Only `required` checks decide the exit code;
    # the rest are reported for context. Deriving that from the name — the old
    # `"optional" not in name` — made every informational row a hard failure.
    checks: list[tuple[str, bool, str, bool]] = []

    # Python version
    v = sys.version_info
    ok = v >= (3, 12)
    checks.append(("Python ≥ 3.12", ok, f"{v.major}.{v.minor}.{v.micro}", True))

    # API keys — check env vars and .env file
    import os

    from dotenv import load_dotenv

    load_dotenv()
    has_anthropic = bool(
        os.environ.get("NOSCOPE_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    )
    has_openai = bool(os.environ.get("NOSCOPE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    # Either key alone is enough, so the per-provider rows are informational.
    checks.append(
        ("Anthropic API key", has_anthropic, "set" if has_anthropic else "not set", False)
    )
    checks.append(("OpenAI API key", has_openai, "set" if has_openai else "not set", False))
    checks.append(("At least one API key", has_anthropic or has_openai, "", True))

    # Git
    git_ok = shutil.which("git") is not None
    checks.append(("git", git_ok, shutil.which("git") or "not found", True))

    # Docker — the binary existing says nothing about --sandbox working, so
    # check the daemon too. This is the exact check `run --sandbox` performs.
    from noscope.tools.docker import preflight_docker

    docker_problem = asyncio.run(preflight_docker(timeout=5.0))
    docker_ok = docker_problem is None
    checks.append(
        (
            "docker (optional, for --sandbox)",
            docker_ok,
            "daemon reachable" if docker_ok else "unusable — see below",
            False,
        )
    )

    # uv
    uv_ok = shutil.which("uv") is not None
    checks.append(("uv (optional)", uv_ok, shutil.which("uv") or "not found", False))

    for name, ok, detail, required in checks:
        icon = "[green]✓[/green]" if ok else ("[red]✗[/red]" if required else "[yellow]–[/yellow]")
        detail_str = f" ({detail})" if detail else ""
        console.print(f"  {icon} {name}{detail_str}")

    if docker_problem:
        # Optional, so it doesn't fail the run — but say what's actually wrong.
        console.print(f"\n  [dim]{docker_problem}[/dim]")

    all_ok = all(ok for _, ok, _, required in checks if required)

    if live:
        all_ok = _live_check() and all_ok

    console.print()
    if all_ok:
        console.print("[green]All checks passed![/green]")
        return
    console.print("[yellow]Some checks failed. Fix the issues above.[/yellow]")
    # Exit non-zero so `noscope doctor` is usable as a gate in scripts and CI.
    raise typer.Exit(1)


def _live_check() -> bool:
    """Make one minimal API call so a bad key or model fails here, not mid-run."""
    from noscope.config.settings import load_settings
    from noscope.errors import explain_error
    from noscope.llm import create_provider, default_model_for, resolve_provider_name
    from noscope.llm.base import Message

    try:
        settings = load_settings()
    except ValueError as e:
        console.print(f"  [red]✗[/red] live API check ({e})")
        return False

    provider_name = resolve_provider_name(settings)
    model = settings.default_model or default_model_for(provider_name)

    async def _ping() -> str:
        provider = create_provider(settings)
        response = await provider.complete(
            [Message(role="user", content="Reply with the single word: ok")]
        )
        return response.content.strip()

    try:
        reply = asyncio.run(_ping())
    except Exception as e:  # noqa: BLE001 — surfaced to the user below
        console.print(f"  [red]✗[/red] live API check ({provider_name}/{model})")
        hint = explain_error(e, provider=provider_name, model=model)
        console.print(f"      {hint or f'{type(e).__name__}: {e}'}")
        return False

    console.print(
        f"  [green]✓[/green] live API check ({provider_name}/{model}) — replied {reply[:20]!r}"
    )
    return True


@app.command()
def new(
    provider: str = typer.Option(None, "--provider", "-p", help="LLM provider (anthropic|openai)"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model override"),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run commands in Docker sandbox"),
    danger: bool = typer.Option(False, "--danger", help="Enable danger mode"),
    auto_approve: bool = typer.Option(
        False, "--yes", "-y", help="Auto-approve all capability requests"
    ),
    serve: bool = typer.Option(
        False, "--serve", help="After a verified build, launch the app (blocks until Ctrl+C)"
    ),
    token_budget: int = typer.Option(
        None, "--token-budget", help="Stop the build once this many total tokens are used"
    ),
    workers: int = typer.Option(None, "--workers", help="Parallel build workers (default 2)"),
) -> None:
    """Create a new project interactively and start building immediately."""
    from rich.panel import Panel
    from rich.prompt import Prompt

    from noscope.config.settings import load_settings
    from noscope.spec.models import AcceptanceCheck, SpecInput
    from noscope.ui.console import ConsoleUI

    ui = ConsoleUI(console)

    console.print(
        Panel(
            "[bold]New Project[/bold]", title="[bold blue]NoScope[/bold blue]", border_style="blue"
        )
    )

    # 1. Project name
    name = Prompt.ask("\n  [bold]Project name[/bold]")
    if not name.strip():
        console.print("[red]Project name is required[/red]")
        raise typer.Exit(1)

    # 2. Description (multiline)
    console.print("\n  [bold]What should it do?[/bold] [dim](enter a blank line to finish)[/dim]")
    lines: list[str] = []
    while True:
        line = Prompt.ask("  ")
        if not line.strip():
            break
        lines.append(line)

    if not lines:
        console.print("[red]Description is required[/red]")
        raise typer.Exit(1)
    body = "\n".join(lines)

    # 3. Timebox
    timebox = Prompt.ask("\n  [bold]Timebox[/bold]", default="5m")

    # 4. Constraints (optional)
    constraints_raw = Prompt.ask(
        "\n  [bold]Constraints[/bold] [dim](comma-separated, or Enter to skip)[/dim]",
        default="",
    )
    constraints = (
        [c.strip() for c in constraints_raw.split(",") if c.strip()] if constraints_raw else []
    )

    # 5. Acceptance checks (optional)
    acceptance_raw = Prompt.ask(
        "\n  [bold]Acceptance checks[/bold] [dim](comma-separated, or Enter to skip)[/dim]",
        default="",
    )
    acceptance = (
        [AcceptanceCheck.from_string(a.strip()) for a in acceptance_raw.split(",") if a.strip()]
        if acceptance_raw
        else []
    )

    # 6. Output directory
    default_dir = f"./{name.lower().replace(' ', '-')}"
    output_dir = Prompt.ask("\n  [bold]Output directory[/bold]", default=default_dir)

    # Build SpecInput
    spec = SpecInput(
        name=name.strip(),
        timebox=timebox,
        constraints=constraints,
        acceptance=acceptance,
        body=f"# {name.strip()}\n\n{body}",
    )

    # Save spec file for reproducibility. Serialize the frontmatter with a real
    # YAML dumper — hand-built quoting broke on any name or constraint
    # containing a quote character.
    from noscope.spec.parser import build_spec_file, slugify

    spec_filename = slugify(spec.name) + ".md"
    Path(spec_filename).write_text(
        build_spec_file(
            name=spec.name,
            timebox=spec.timebox,
            constraints=constraints,
            acceptance=[a.raw for a in acceptance],
            body=spec.body,
        ),
        encoding="utf-8",
    )
    console.print(f"\n  [dim]Spec saved to {spec_filename}[/dim]")

    # Load settings and run
    if danger:
        ui.danger_warning()

    try:
        settings = load_settings(
            default_provider=provider,
            default_model=model,
            danger_mode=danger,
            token_budget=token_budget,
            max_workers=workers,
        )
    except ValueError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(1) from None

    ui.header(spec.name, timebox)

    from noscope.orchestrator import Orchestrator

    orchestrator = Orchestrator(settings, console=console)
    asyncio.run(
        orchestrator.run(
            spec_input=spec,
            timebox=timebox,
            output_dir=Path(output_dir),
            sandbox=sandbox,
            auto_approve=auto_approve,
            serve=serve,
        )
    )


@app.command()
def init() -> None:
    """Create a spec file template."""
    template = """---
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
"""
    path = Path("spec.md")
    if path.exists():
        for i in range(1, 100):
            path = Path(f"spec-{i}.md")
            if not path.exists():
                break

    path.write_text(template, encoding="utf-8")
    console.print(f"[green]Created {path}[/green] — edit it and run: noscope run --spec {path}")


def main() -> None:
    app()
