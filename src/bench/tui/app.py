"""The app shell: one screen with a section per area of the benchmark."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from bench.core.workspace import Workspace
from bench.tui.tasks import TaskBrowser


class BenchApp(App[None]):
    TITLE = "DS PM Bench"

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        yield Header()
        yield TaskBrowser(self.workspace)
        yield Footer()
