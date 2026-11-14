"""Textual pilot tests for the Settings tab, on fixture Workspaces in temp dirs (#31)."""

from pathlib import Path

from textual.widgets import Input, Static, TabbedContent, TextArea

from bench import Workspace
from bench.tui.app import BenchApp
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
