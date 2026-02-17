"""Rich console output for NoScope."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from noscope import __version__
from noscope.capabilities import CapabilityRequest
from noscope.deadline import Phase


class ConsoleUI:
    """Rich-powered console output for NoScope runs."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def header(self, spec_name: str, timebox: str) -> None:
        self.console.print(
            Panel(
                f"[bold white]{spec_name}[/bold white]\n"
                f"Timebox: [cyan]{timebox}[/cyan]",
                title=f"[bold blue]NoScope[/bold blue] v{__version__}",
                border_style="blue",
            )
        )

    def phase_banner(self, phase: Phase, message: str, remaining: str) -> None:
        colors = {
            Phase.PLAN: "cyan",
            Phase.REQUEST: "yellow",
            Phase.BUILD: "green",
            Phase.HARDEN: "magenta",
            Phase.HANDOFF: "blue",
        }
        color = colors.get(phase, "white")
        self.console.print(
            f"\n[{color} bold]▶ [{phase.value}][/{color} bold] {message} "
            f"[dim]({remaining} remaining)[/dim]"
        )

    def tool_execution(self, tool_name: str, display: str, elapsed: float) -> None:
        truncated = display[:500] + "..." if len(display) > 500 else display
        self.console.print(f"  [dim]⚡ {tool_name}[/dim] ({elapsed:.1f}s)")
        if truncated.strip():
            for line in truncated.strip().split("\n")[:10]:
                self.console.print(f"    [dim]{line}[/dim]")

    def capability_table(self, requests: list[CapabilityRequest]) -> None:
        table = Table(title="Capability Requests", show_header=True, header_style="bold")
        table.add_column("Capability", style="cyan")
        table.add_column("Justification")
        table.add_column("Risk", justify="center")

        risk_styles = {"low": "green", "medium": "yellow", "high": "red"}
        for req in requests:
            style = risk_styles.get(req.risk, "white")
            table.add_row(req.cap, req.why, f"[{style}]{req.risk}[/{style}]")

        self.console.print(table)

    def panic_warning(self) -> None:
        self.console.print(
            Panel(
                "[bold]PANIC MODE ACTIVATED[/bold]\n"
                "Time is running low. Stopping new features.\n"
                "Focusing on making the demo runnable.",
                border_style="red",
                style="red",
            )
        )

    def danger_warning(self) -> None:
        self.console.print(
            Panel(
                "[bold]⚠️  DANGER MODE ENABLED  ⚠️[/bold]\n"
                "Safety filters are DISABLED.\n"
                "Commands will execute without restrictions.",
                border_style="red bold",
                style="red",
            )
        )

    def acceptance_results(self, results: list[dict]) -> None:  # type: ignore[type-arg]
        table = Table(title="Acceptance Results", show_header=True, header_style="bold")
        table.add_column("Check", style="cyan")
        table.add_column("Result", justify="center")

        for r in results:
            if r.get("passed"):
                status = "[green]✓ Pass[/green]"
            elif r.get("skipped"):
                status = "[yellow]⊘ Skip[/yellow]"
            else:
                status = "[red]✗ Fail[/red]"
            table.add_row(r.get("name", "unknown"), status)

        self.console.print(table)

    def complete(self, run_dir: Path) -> None:
        self.console.print(
            Panel(
                f"[bold green]Run complete![/bold green]\n\n"
                f"Run directory: [cyan]{run_dir}[/cyan]\n"
                f"Handoff report: [cyan]{run_dir / 'handoff.md'}[/cyan]\n"
                f"Event log: [cyan]{run_dir / 'events.jsonl'}[/cyan]",
                title="[bold]Done[/bold]",
                border_style="green",
            )
        )
