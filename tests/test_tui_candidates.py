"""Textual pilot tests for the Candidates tab, on fixture Workspaces in temp dirs."""

from pathlib import Path

from textual.widgets import DataTable, Static, TabbedContent

from bench import Workspace
from bench.tui.app import BenchApp
from test_workspace_candidates import candidate, write_candidate


def rows(app: BenchApp) -> list[list[str]]:
    table = app.query_one("#candidate-table", DataTable)
    return [[str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)]


async def open_candidates(app: BenchApp, pilot) -> None:
    app.query_one(TabbedContent).active = "candidates"
    await pilot.pause()
    app.query_one("#candidate-table", DataTable).focus()
    await pilot.pause()


def status(app: BenchApp) -> str:
    return str(app.query_one("#candidate-status", Static).content)


async def test_candidates_tab_lists_version_adapter_baseline_gate_and_clearance(
    workspace_dir: Path,
):
    write_candidate(workspace_dir, "claude@opus-5-5", candidate(baseline=True))
    write_candidate(
        workspace_dir,
        "copilot@2",
        candidate(tool="Copilot", version="2", adapter="manual_assisted", env_vars=[],
                  settings={}, gate=None),
    )

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_candidates(app, pilot)
        listed = rows(app)

    assert listed == [
        ["claude@opus-5-5", "claude-opus-5-5", "automated", "Baseline", "pass",
         "public_and_private"],
        ["copilot@2", "2", "manual_assisted", "", "not reviewed", "none"],
    ]
