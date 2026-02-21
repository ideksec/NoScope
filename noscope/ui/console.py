"""Rich console output for NoScope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from noscope import __version__
from noscope.capabilities import CapabilityRequest
from noscope.deadline import Deadline, Phase


class ConsoleUI:
    """Rich-powered console output for NoScope runs."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._current_phase = ""
        self._last_activity = ""

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
        self._current_phase = phase.value
        self.console.print(
            f"\n[{color} bold]▶ [{phase.value}][/{color} bold] {message} "
            f"[dim]({remaining} remaining)[/dim]"
        )

    def tool_activity(self, tool_name: str, summary: str, deadline: Deadline) -> None:
        """Show a tool execution as it happens with current time remaining."""
        remaining = deadline.format_remaining()
        # Truncate long summaries
        if len(summary) > 80:
            summary = summary[:77] + "..."
        self.console.print(
            f"  [dim]⚡ {tool_name}[/dim] [dim italic]{summary}[/dim italic] "
            f"[dim]({remaining})[/dim]"
        )

    def task_complete(self, task_id: str, title: str, deadline: Deadline) -> None:
        """Show a task completion."""
        remaining = deadline.format_remaining()
        self.console.print(
            f"  [green]✓[/green] [bold]{task_id}[/bold] {title} [dim]({remaining})[/dim]"
        )

    def llm_thinking(self, summary: str, deadline: Deadline) -> None:
        """Show what the LLM is doing."""
        remaining = deadline.format_remaining()
        if len(summary) > 100:
            summary = summary[:97] + "..."
        self.console.print(
            f"  [dim]💭 {summary}[/dim] [dim]({remaining})[/dim]"
        )

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

    def acceptance_results(self, results: list[dict[str, Any]]) -> None:
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

    def verify_result(self, success: bool, message: str) -> None:
        """Show the MVP verification result."""
        if success:
            self.console.print(
                Panel(
                    f"[bold green]MVP VERIFIED[/bold green]\n\n{message}",
                    border_style="green",
                )
            )
        else:
            self.console.print(
                Panel(
                    f"[bold red]MVP VERIFICATION FAILED[/bold red]\n\n{message}",
                    border_style="red",
                )
            )

    def cost_summary(self, input_tokens: int, output_tokens: int, provider: str, model: str) -> None:
        """Show estimated cost of the run."""
        # Pricing per million tokens (approximate, as of 2025)
        pricing: dict[str, tuple[float, float]] = {
            "claude-sonnet-4-20250514": (3.0, 15.0),
            "claude-haiku-4-5-20251001": (0.80, 4.0),
            "gpt-4o": (2.50, 10.0),
            "gpt-4o-mini": (0.15, 0.60),
        }
        input_price, output_price = pricing.get(model, (3.0, 15.0))
        cost = (input_tokens / 1_000_000 * input_price) + (output_tokens / 1_000_000 * output_price)

        self.console.print(
            f"\n  [dim]Tokens: {input_tokens:,} in / {output_tokens:,} out "
            f"| Estimated cost: ${cost:.4f} ({provider}/{model})[/dim]"
        )

    def launch_app(self, workspace: Path, command: str, url: str) -> None:
        """Show that the app is being launched for the user."""
        self.console.print(
            Panel(
                f"[bold green]Launching your app![/bold green]\n\n"
                f"Directory: [cyan]{workspace}[/cyan]\n"
                f"Command: [bold]{command}[/bold]\n\n"
                f"Open in your browser: [bold cyan underline]{url}[/bold cyan underline]\n\n"
                f"[dim]Press Ctrl+C to stop the server[/dim]",
                title="[bold green]LIVE DEMO[/bold green]",
                border_style="green",
            )
        )

    def final_summary(
        self,
        spec_name: str,
        timebox: str,
        workspace: Path,
        run_dir: Path,
        tasks_completed: int,
        tasks_total: int,
        checks_passed: int,
        checks_total: int,
        verified: bool | None,
        verify_msg: str,
        launch_url: str | None,
        input_tokens: int,
        output_tokens: int,
        provider: str,
        model: str,
    ) -> None:
        """Show comprehensive final summary — always displayed."""
        # Status line
        if verified:
            status = "[bold green]MVP VERIFIED[/bold green]"
            border = "green"
        elif verified is False:
            status = "[bold red]MVP FAILED[/bold red]"
            border = "red"
        else:
            status = "[bold yellow]NOT VERIFIED[/bold yellow] (deadline expired)"
            border = "yellow"

        # Build info lines
        lines = [
            f"  Status:      {status}",
            f"  Project:     [bold]{spec_name}[/bold]",
            f"  Timebox:     {timebox}",
            f"  Tasks:       {tasks_completed}/{tasks_total} completed",
            f"  Checks:      {checks_passed}/{checks_total} passed",
        ]

        if verify_msg:
            lines.append(f"  Verify:      {verify_msg[:80]}")

        if launch_url:
            lines.append(f"  URL:         [bold cyan underline]{launch_url}[/bold cyan underline]")

        lines.append("")
        lines.append(f"  Workspace:   [cyan]{workspace}[/cyan]")
        lines.append(f"  Handoff:     [cyan]{run_dir / 'handoff.md'}[/cyan]")
        lines.append(f"  Event log:   [cyan]{run_dir / 'events.jsonl'}[/cyan]")

        # Cost
        pricing: dict[str, tuple[float, float]] = {
            "claude-sonnet-4-20250514": (3.0, 15.0),
            "claude-haiku-4-5-20251001": (0.80, 4.0),
            "gpt-4o": (2.50, 10.0),
            "gpt-4o-mini": (0.15, 0.60),
        }
        input_price, output_price = pricing.get(model, (3.0, 15.0))
        cost = (input_tokens / 1_000_000 * input_price) + (output_tokens / 1_000_000 * output_price)
        lines.append(
            f"  Cost:        ${cost:.4f} ({input_tokens:,} in / {output_tokens:,} out)"
        )

        self.console.print(
            Panel(
                "\n".join(lines),
                title=f"[bold]NoScope — {spec_name}[/bold]",
                border_style=border,
                padding=(1, 2),
            )
        )
