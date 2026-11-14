"""Textual pilot tests for the Candidates tab, on fixture Workspaces in temp dirs."""

from pathlib import Path

from textual.widgets import DataTable, Static, TabbedContent, TextArea

from bench import Workspace
from bench.tui.app import BenchApp
from bench.tui.candidate_editor import GateScreen, NewCandidateScreen
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


def fill(app: BenchApp, values: dict[str, object]) -> None:
    for selector, value in values.items():
        widget = app.screen.query_one(selector)
        if isinstance(widget, TextArea):
            widget.text = value
        else:
            widget.value = value


async def create(app: BenchApp, pilot, **values: object) -> None:
    await pilot.press("n")
    await pilot.pause()
    fill(app, values)
    await pilot.press("ctrl+s")
    await pilot.pause()


async def test_creating_the_first_candidate_makes_it_the_baseline(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_candidates(app, pilot)
        await create(
            app, pilot,
            **{"#cand-tool": "Claude", "#cand-version": "opus-5-5",
               "#cand-settings": "temperature: 0.2\n", "#cand-env-vars": "ANTHROPIC_API_KEY"},
        )
        listed, shown = rows(app), status(app)

    assert listed == [["claude@opus-5-5", "opus-5-5", "automated", "Baseline",
                       "not reviewed", "none"]]
    assert "claude@opus-5-5 created" in shown
    c = Workspace.open(workspace_dir).candidate("claude@opus-5-5")
    assert c.settings == {"temperature": 0.2} and c.env_vars == ("ANTHROPIC_API_KEY",)


async def test_a_refused_candidate_is_shown_inline_and_nothing_is_written(
    workspace_dir: Path,
):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_candidates(app, pilot)
        await create(
            app, pilot,
            **{"#cand-tool": "Claude", "#cand-version": "opus-5-5", "#cand-baseline": False},
        )
        assert isinstance(app.screen, NewCandidateScreen)
        error = str(app.screen.query_one("#cand-error", Static).content)

    assert "must be the Baseline" in error
    assert Workspace.open(workspace_dir).candidates == ()


async def test_marking_a_candidate_the_baseline_moves_the_mark(workspace_dir: Path):
    write_candidate(workspace_dir, "claude@opus-5-5", candidate(baseline=True))
    write_candidate(workspace_dir, "other@1", candidate(tool="Other", version="1"))

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_candidates(app, pilot)
        await pilot.press("down", "b")
        await pilot.pause()
        listed = rows(app)

    assert [row[3] for row in listed] == ["", "Baseline"]
    assert Workspace.open(workspace_dir).baseline.id == "other@1"


GATE = {
    "#gate-reviewer": "J. Reviewer",
    "#gate-date": "2026-11-13",
    "#gate-retention": "30 days, no training",
    "#gate-sso": True,
    "#gate-enterprise-terms": True,
    "#gate-connector-support": "None needed",
}


async def gate(app: BenchApp, pilot, **values: object) -> None:
    await pilot.press("g")
    await pilot.pause()
    fill(app, {**GATE, **values})
    await pilot.press("ctrl+s")
    await pilot.pause()


async def created_then_gated(app: BenchApp, pilot, **values: object) -> None:
    await open_candidates(app, pilot)
    await create(app, pilot, **{"#cand-tool": "Claude", "#cand-version": "opus-5-5"})
    await gate(app, pilot, **values)


async def test_a_passing_gate_grants_its_clearance(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await created_then_gated(
            app, pilot, **{"#gate-result": "pass", "#gate-clearance": "public_only"}
        )
        listed, shown = rows(app), status(app)

    assert listed[0][4:] == ["pass", "public_only"]
    assert "passed its Gate" in shown
    ws = Workspace.open(workspace_dir)
    assert ws.candidate("claude@opus-5-5").gate.reviewer == "J. Reviewer"
    assert ws.decision_log() == ()


async def test_a_failed_gate_shows_the_reject_it_logged(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await created_then_gated(app, pilot, **{"#gate-result": "fail"})
        listed, shown = rows(app), status(app)

    assert listed[0][4:] == ["fail", "none"]
    [entry] = Workspace.open(workspace_dir).decision_log()
    assert entry["verdict"] == "reject" and entry["date"] == "2026-11-13"
    assert "Decision Log: reject for claude@opus-5-5 on 2026-11-13" in shown
    assert entry["rationale"][0] in shown


async def test_an_invalid_gate_is_shown_inline_and_changes_nothing(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await created_then_gated(
            app, pilot, **{"#gate-result": "fail", "#gate-clearance": "public_only"}
        )
        assert isinstance(app.screen, GateScreen)
        error = str(app.screen.query_one("#gate-error", Static).content)

    assert "a failed Gate grants no Clearance" in error
    ws = Workspace.open(workspace_dir)
    assert ws.candidate("claude@opus-5-5").gate is None
    assert ws.decision_log() == ()
