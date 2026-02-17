"""Textual TUI for NoScope — full terminal UI with live updates."""

from __future__ import annotations

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.reactive import reactive
    from textual.widgets import Footer, Header, ListItem, ListView, RichLog, Static

    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


def is_available() -> bool:
    """Check if the TUI dependencies are installed."""
    return HAS_TEXTUAL


if HAS_TEXTUAL:

    class TaskItem(ListItem):
        """A task item in the task list."""

        def __init__(self, task_id: str, title: str, status: str = "planned") -> None:
            super().__init__()
            self.task_id = task_id
            self.task_title = title
            self.task_status = status

        def compose(self) -> ComposeResult:
            icons = {"planned": "○", "in_progress": "◉", "done": "●"}
            icon = icons.get(self.task_status, "○")
            yield Static(f"{icon} [{self.task_id}] {self.task_title}")

    class NoscopeTUI(App[None]):
        """NoScope Terminal UI."""

        CSS = """
        #header-bar {
            dock: top;
            height: 3;
            background: $primary;
            color: $text;
            text-align: center;
            padding: 1;
        }

        #main {
            height: 1fr;
        }

        #task-panel {
            width: 35;
            border-right: solid $primary;
        }

        #log-panel {
            width: 1fr;
        }

        #status-bar {
            dock: bottom;
            height: 1;
            background: $surface;
        }
        """

        phase: reactive[str] = reactive("INIT")
        remaining: reactive[str] = reactive("--:--")

        def __init__(self, project_name: str = "NoScope") -> None:
            super().__init__()
            self.project_name = project_name

        def compose(self) -> ComposeResult:
            yield Header()
            yield Static(
                f"[bold]{self.project_name}[/bold] | Phase: {self.phase} | ⏱ {self.remaining}",
                id="header-bar",
            )
            with Horizontal(id="main"):
                with Vertical(id="task-panel"):
                    yield Static("[bold]Tasks[/bold]")
                    yield ListView(id="task-list")
                with Vertical(id="log-panel"):
                    yield Static("[bold]Output[/bold]")
                    yield RichLog(id="output-log", highlight=True, markup=True)
            yield Footer()

        def add_task(self, task_id: str, title: str) -> None:
            task_list = self.query_one("#task-list", ListView)
            task_list.append(TaskItem(task_id, title))

        def log_output(self, text: str) -> None:
            log = self.query_one("#output-log", RichLog)
            log.write(text)

        def update_phase(self, phase: str, remaining: str) -> None:
            self.phase = phase
            self.remaining = remaining
            header = self.query_one("#header-bar", Static)
            header.update(
                f"[bold]{self.project_name}[/bold] | Phase: {phase} | ⏱ {remaining}"
            )
