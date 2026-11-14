"""Textual pilot tests for the Settings tab, on fixture Workspaces in temp dirs (#31)."""

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, Select, Static, TabbedContent, TextArea

from bench import Workspace
from bench.tui.app import BenchApp
from test_workspace_calibration import certified_workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import CANDIDATE, write_judge_setup
from test_workspace_tasks import write_task


async def open_settings(app: BenchApp, pilot) -> None:
    app.query_one(TabbedContent).active = "settings"
    await pilot.pause()


def fill(app: BenchApp, values: dict[str, str]) -> None:
    for selector, value in values.items():
        widget = app.query_one(selector)
        if isinstance(widget, TextArea):
            widget.text = value
        else:
            widget.value = value


def text(app: BenchApp, selector: str) -> str:
    return str(app.query_one(selector, Static).content)


async def save(app: BenchApp, pilot, button: str) -> None:
    app.query_one(button).press()
    await pilot.pause()


async def test_valid_settings_edits_are_saved_through_the_workspace(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        assert app.query_one("#set-weight-quality", Input).value == "50"
        fill(app, {
            "#set-weight-quality": "40",
            "#set-weight-edit_distance": "30",
            "#set-regression_margin": "0.4",
            "#set-tracks": "Framing, Specs, Prioritize, Story, Launch",
        })
        await save(app, pilot, "#settings-save")
        saved = text(app, "#settings-status")

    reloaded = Workspace.open(workspace_dir).settings
    assert reloaded.weights == {
        "quality": 40, "edit_distance": 30, "time_saved": 20, "cost_latency": 10
    }
    assert reloaded.regression_margin == 0.4
    assert reloaded.tracks[-1] == "Launch"
    assert reloaded.retest_margin == 0.2  # untouched fields stay as they were
    assert "Saved" in saved


def settings_file(root: Path) -> str:
    return (root / "config" / "settings.yaml").read_text()


async def test_weights_not_summing_to_100_are_refused_next_to_the_weights(
    workspace_dir: Path,
):
    before = settings_file(workspace_dir)
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        assert text(app, "#weight-sum") == "Sum: 100"
        fill(app, {"#set-weight-quality": "40"})
        await pilot.pause()
        live_sum = text(app, "#weight-sum")
        await save(app, pilot, "#settings-save")
        error = text(app, "#err-weights")

    assert live_sum == "Sum: 90 (must be 100)"
    assert "must sum to 100, got 90" in error
    assert settings_file(workspace_dir) == before


async def test_a_bad_band_is_shown_next_to_its_band(workspace_dir: Path):
    before = settings_file(workspace_dir)
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        fill(app, {"#set-band-cost": "0.5, 0.1, 0.2, 1"})
        await save(app, pilot, "#settings-save")
        band_error, weights_error = text(app, "#err-band-cost"), text(app, "#err-weights")

    assert "strictly ascending" in band_error
    assert weights_error == ""
    assert settings_file(workspace_dir) == before


async def test_dropping_a_track_a_task_uses_is_refused_next_to_the_tracks(
    workspace_dir: Path,
):
    write_task(workspace_dir, "T1.1")  # a Framing Task
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        fill(app, {"#set-tracks": "Specs, Prioritize, Story"})
        await save(app, pilot, "#settings-save")
        error = text(app, "#err-tracks")

    assert "cannot remove Track 'Framing'" in error
    assert Workspace.open(workspace_dir).settings.tracks[0] == "Framing"


async def test_a_field_that_is_not_a_number_is_shown_next_to_it(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        fill(app, {"#set-spot_check_rate": "a quarter"})
        await save(app, pilot, "#settings-save")
        error = text(app, "#err-spot_check_rate")

    assert "must be a number" in error


def price_rows(app: BenchApp) -> list[list[str]]:
    table = app.query_one("#price-table", DataTable)
    return [[str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)]


async def test_a_price_is_set_listed_and_removed(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        fill(app, {"#price-model": "anthropic/claude-opus-5-5", "#price-input": "15",
                   "#price-output": "75"})
        await save(app, pilot, "#price-save")
        listed = price_rows(app)
        saved = Workspace.open(workspace_dir).prices.models
        fill(app, {"#price-model": "anthropic/claude-opus-5-5"})
        await save(app, pilot, "#price-remove")
        after_removal = price_rows(app)

    assert listed == [["anthropic/claude-opus-5-5", "15", "75"]]
    assert saved["anthropic/claude-opus-5-5"].output_per_mtok == 75
    assert after_removal == []
    assert Workspace.open(workspace_dir).prices.models == {}


async def test_a_refused_price_is_shown_next_to_the_price_form(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        fill(app, {"#price-model": "openai/gpt-6"})
        await save(app, pilot, "#price-remove")
        removal_error = text(app, "#err-prices")
        fill(app, {"#price-input": "-1", "#price-output": "2"})
        await save(app, pilot, "#price-save")
        price_error = text(app, "#err-prices")

    assert "has no price to remove" in removal_error
    assert "config/prices.yaml" in price_error
    assert Workspace.open(workspace_dir).prices.models == {}


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """One public Task, a manual-assisted Baseline cleared for it, and a Judge Setup."""
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted",
                     env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    write_judge_setup(workspace_dir)
    return workspace_dir


@pytest.fixture
def certified(ws_dir: Path) -> Workspace:
    """A Workspace whose Judge Setup is certified on a Calibration Set (fake judges)."""
    return certified_workspace(ws_dir)


async def test_editing_the_judge_prompt_drops_certification(certified: Workspace):
    old_hash = certified.judge_setup.hash
    app = BenchApp(certified)
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        before = text(app, "#judge-status")
        warning = text(app, "#judge-warning")
        fill(app, {"#judge-prompt": "Score harshly."})
        await save(app, pilot, "#judge-save")
        after = text(app, "#judge-status")

    assert f"{old_hash}: certified" in before
    assert "drops its Certification" in warning
    assert certified.judge_setup.hash != old_hash
    assert not certified.judge_certified
    assert f"{certified.judge_setup.hash}: not certified" in after
    assert "Certification dropped" in after


async def test_widening_the_judge_clearance_drops_certification(certified: Workspace):
    app = BenchApp(certified)
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        app.query_one("#judge-clearance", Select).value = "public_and_private"
        await save(app, pilot, "#judge-save")
        after = text(app, "#judge-status")

    assert certified.judge_setup.clearance == "public_and_private"
    assert not certified.judge_certified
    assert "Certification dropped" in after


async def test_a_refused_judge_setup_edit_is_shown_and_writes_nothing(ws_dir: Path):
    before = (ws_dir / "config" / "judge.yaml").read_text()
    app = BenchApp(Workspace.open(ws_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        app.query_one("#judge-mode", Select).value = "panel"
        await save(app, pilot, "#judge-save")
        error = text(app, "#err-judge")

    assert "a Panel needs at least two models" in error
    assert (ws_dir / "config" / "judge.yaml").read_text() == before


async def test_ctrl_s_saves_the_section_holding_the_focus(workspace_dir: Path):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        fill(app, {"#set-retest_margin": "0.3", "#price-model": "openai/gpt-6",
                   "#price-input": "1", "#price-output": "2"})
        app.query_one("#price-output", Input).focus()
        await pilot.press("ctrl+s")
        await pilot.pause()

    ws = Workspace.open(workspace_dir)
    assert ws.prices.models["openai/gpt-6"].input_per_mtok == 1
    assert ws.settings.retest_margin == 0.2  # the settings section was not saved


async def test_without_a_judge_setup_the_form_creates_one(workspace_dir: Path):
    (workspace_dir / "config" / "judge.yaml").unlink()
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_settings(app, pilot)
        before = text(app, "#judge-status")
        app.query_one("#judge-mode", Select).value = "single"
        app.query_one("#judge-clearance", Select).value = "public_only"
        fill(app, {"#judge-models": "- {provider: openai, model: gpt-6}",
                   "#judge-prompt-file": "config/judge/prompt.md",
                   "#judge-prompt": "Score the Output."})
        await save(app, pilot, "#judge-save")
        after = text(app, "#judge-status")

    assert "No Judge Setup yet" in before
    assert "not certified" in after
    assert Workspace.open(workspace_dir).judge_setup.prompt == "Score the Output."
