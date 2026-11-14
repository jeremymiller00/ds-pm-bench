"""Textual pilot tests for the Calibration tab: build a Calibration Set, score it blind,
run the Judge on it and certify the Judge Setup.

Fake judges stand in for every model call; Runs are recorded through the Workspace.
"""

import datetime
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, Select, Static

from bench import Workspace
from bench.tui.app import BenchApp
from bench.tui.calibration import CalibrationScoreScreen
from bench.tui.runs import JudgeScreen
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import (
    CANDIDATE,
    DAY,
    OUTPUT,
    FakeJudge,
    FakeJudges,
    recorded_run,
    write_judge_setup,
)
from test_workspace_tasks import write_task

JUDGE = "openai/gpt-6"


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


def text(app: BenchApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


async def open_calibration(pilot) -> None:
    await pilot.click("#--content-tab-calibration")
    await pilot.pause()


async def add_item(app: BenchApp, pilot, run_id: str, task_id: str = "T1.1") -> None:
    app.query_one("#cal-run", Select).value = run_id
    await pilot.pause()
    app.query_one("#cal-task", Select).value = task_id
    await pilot.pause()
    await pilot.click("#cal-add")
    await pilot.pause()


async def score_first(app: BenchApp, pilot, score: int) -> CalibrationScoreScreen:
    items = app.query_one("#cal-unscored", OptionList)
    items.focus()
    items.highlighted = 0
    await pilot.press("enter")
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, CalibrationScoreScreen)
    for field in screen.query(Input):
        field.value = str(score)
    return screen


async def test_a_judge_setup_is_certified_from_a_fixture_calibration_set(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{JUDGE: FakeJudge(score=4)}))
    run = recorded_run(ws)
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await open_calibration(pilot)
        before = text(app, "#cal-setup")
        await add_item(app, pilot, run.id)
        await score_first(app, pilot, 4)
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.click("#cal-judge")
        await app.workers.wait_for_complete()
        await pilot.pause()
        agreement = text(app, "#cal-agreement")
        await pilot.click("#cal-certify")
        await pilot.pause()
        after = text(app, "#cal-setup")

    assert ws.judge_setup.hash in before and "not certified" in before
    assert "100% within 1 point" in agreement
    assert ws.judge_certified
    assert ws.judge_setup.hash in after and "not certified" not in after
    [item] = ws.calibration_items()
    assert item.human_scores == {c: 4 for c in item.criteria}


async def test_the_scoring_screen_is_blind(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{JUDGE: FakeJudge(score=2)}))
    run = recorded_run(ws)
    item = ws.add_calibration_item(run.id, "T1.1")
    ws.judge_calibration_set()  # the Judge has scored it already: still never shown
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await open_calibration(pilot)
        unscored = str(app.query_one("#cal-unscored", OptionList).get_option_at_index(0).prompt)
        screen = await score_first(app, pilot, 4)
        shown = "\n".join(str(s.content) for s in screen.query(Static))

    assert unscored == item.id
    assert OUTPUT in shown and "Churn rose 4% last quarter" in shown
    for leak in (run.id, CANDIDATE, "chatbot", "ok."):
        assert leak not in shown and leak not in unscored


async def test_a_failed_certification_names_its_shortfalls_and_logs_nothing(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)
    ws.add_calibration_item(run.id, "T1.1")
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await open_calibration(pilot)
        await pilot.click("#cal-certify")
        await pilot.pause()
        status = text(app, "#cal-status")

    assert "no human score yet for c0001" in status
    assert not ws.judge_certified
    assert ws.decision_log() == ()


async def test_a_bad_score_is_refused_on_the_scoring_screen(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)
    ws.add_calibration_item(run.id, "T1.1")
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await open_calibration(pilot)
        await score_first(app, pilot, 9)
        await pilot.press("ctrl+s")
        await pilot.pause()
        still_open = isinstance(app.screen, CalibrationScoreScreen)
        error = text(app, "#cal-score-error")

    assert still_open and error
    assert not ws.calibration_items()[0].human_scored


def certify(ws: Workspace, run_id: str) -> None:
    item = ws.add_calibration_item(run_id, "T1.1")
    ws.score_calibration_item(item.id, {c: 4 for c in item.criteria})
    ws.judge_calibration_set()
    ws.certify_judge(on=DAY)


async def open_judge_screen(app: BenchApp, pilot, run_id: str) -> JudgeScreen:
    await pilot.click("#--content-tab-runs")
    await pilot.pause()
    app.query_one("#run-existing", Select).value = run_id
    await pilot.pause()
    await pilot.click("#run-resume")
    await pilot.pause()
    await pilot.click("#run-judge")
    await pilot.pause()
    assert isinstance(app.screen, JudgeScreen)
    return app.screen


async def test_a_certified_judge_setup_judges_a_run_with_no_override(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{JUDGE: FakeJudge(score=4)}))
    certify(ws, recorded_run(ws).id)
    run = ws.start_run(CANDIDATE, on=DAY + datetime.timedelta(days=1))
    run.record_output("T1.1", raw="draft", final=OUTPUT, active_seconds=60,
                      latency_seconds=5, cost_usd=None)
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await open_judge_screen(app, pilot, run.id)
        assert not screen.query("#judge-override")
        notice = text(app, "#judge-certification")
        await pilot.click("#judge-run")
        await app.workers.wait_for_complete()
        await pilot.pause()
        shown = text(app, "#judge-results")

    assert "certified" in notice and "not certified" not in notice
    assert "override" not in shown
    assert ws.resume_run(run.id).unjudged == ()
    assert [e["kind"] for e in ws.decision_log()] == ["judge_certification"]


async def test_the_decision_log_labels_a_certification_with_its_hash(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{JUDGE: FakeJudge(score=4)}))
    certify(ws, recorded_run(ws).id)
    app = BenchApp(ws)
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.click("#--content-tab-decision-log")
        await pilot.pause()
        entries = app.query_one("#log-entries", OptionList)
        label = str(entries.get_option_at_index(0).prompt)

    assert ws.judge_setup.hash in label
    assert "certified" in label and "100% within 1 point" in label
    assert "?" not in label
