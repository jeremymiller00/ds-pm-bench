"""Textual pilot tests for a Run's Spot-check queue: score each queued Output blind, see
progress, and add a checked Output to the Calibration Set.

Fake judges stand in for every model call; the Run is recorded and judged through the
Workspace.
"""

from pathlib import Path

from textual.widgets import Input, OptionList, Select, Static

from bench import Workspace
from bench.tui.app import BenchApp
from bench.tui.spot_checks import SpotCheckScreen, SpotCheckTaskScreen
from test_workspace_judging import FakeJudges
from test_workspace_spot_checks import CHALLENGER, judged_run, ws_dir  # noqa: F401


def text(app: BenchApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


async def open_spot_checks(app: BenchApp, pilot, run_id: str) -> SpotCheckScreen:
    await pilot.click("#--content-tab-runs")
    await pilot.pause()
    app.query_one("#run-existing", Select).value = run_id
    await pilot.pause()
    await pilot.click("#run-resume")
    await pilot.pause()
    await pilot.click("#run-spot-check")
    await pilot.pause()
    assert isinstance(app.screen, SpotCheckScreen)
    return app.screen


async def choose(app: BenchApp, pilot, selector: str, index: int = 0) -> None:
    options = app.screen.query_one(selector, OptionList)
    options.focus()
    options.highlighted = index
    await pilot.press("enter")
    await pilot.pause()


async def check_next(app: BenchApp, pilot, score: str = "4") -> SpotCheckTaskScreen:
    await choose(app, pilot, "#spot-queue")
    screen = app.screen
    assert isinstance(screen, SpotCheckTaskScreen)
    for field in screen.query(Input):
        field.value = score
    return screen


async def test_a_runs_spot_check_queue_is_completed_and_an_output_joins_calibration(
    ws_dir: Path,  # noqa: F811
):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = judged_run(ws)
    queue = run.spot_check_queue
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await open_spot_checks(app, pilot, run.id)
        before = text(app, "#spot-progress")
        for _ in queue:
            await check_next(app, pilot)
            await pilot.press("ctrl+s")
            await pilot.pause()
        after = text(app, "#spot-progress")
        await choose(app, pilot, "#spot-checked")
        status = text(app, "#spot-status")

    assert f"0/{len(queue)}" in before
    assert f"{len(queue)}/{len(queue)}" in after
    assert ws.resume_run(run.id).spot_checks_left == {}
    [item] = ws.calibration_items()
    assert (item.run_id, item.task_id) == (run.id, queue[0])
    assert item.id in status


async def test_the_spot_check_screen_is_blind(ws_dir: Path):  # noqa: F811
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = judged_run(ws)
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        queue_screen = await open_spot_checks(app, pilot, run.id)
        listed = "\n".join(str(s.content) for s in queue_screen.query(Static))
        screen = await check_next(app, pilot)
        shown = "\n".join(str(s.content) for s in screen.query(Static))

    assert "final" in shown and "Churn rose 4% last quarter" in shown
    for leak in (run.id, CHALLENGER, "ok."):
        assert leak not in shown and leak not in listed


async def test_a_bad_spot_check_score_is_refused(ws_dir: Path):  # noqa: F811
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = judged_run(ws)
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await open_spot_checks(app, pilot, run.id)
        await check_next(app, pilot, score="nine")
        await pilot.press("ctrl+s")
        await pilot.pause()
        still_open = isinstance(app.screen, SpotCheckTaskScreen)
        error = text(app, "#spot-score-error")

    assert still_open and "nine" in error
    assert ws.resume_run(run.id).spot_checks == ()
