"""The app shell: one tab per section of the benchmark, each a client of the Workspace.

Later slices replace a placeholder section by yielding their own widget in its TabPane.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from bench.core.workspace import Workspace
from bench.tui.tasks import TaskBrowser

# (pane id, tab label) for the sections still to come, in navigation order.
PLACEHOLDER_SECTIONS = (
    ("runs", "Runs"),
    ("calibration", "Calibration"),
    ("comparisons", "Comparisons"),
    ("decision-log", "Decision Log"),
    ("settings", "Settings"),
)


class BenchApp(App[None]):
    TITLE = "DS PM Bench"

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="tasks"):
            with TabPane("Tasks", id="tasks"):
                yield TaskBrowser(self.workspace)
            for pane_id, label in PLACEHOLDER_SECTIONS:
                with TabPane(label, id=pane_id):
                    yield Static(f"{label}: not built yet.")
        yield Footer()
