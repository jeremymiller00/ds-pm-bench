"""Textual pilot tests for an automated Run in the Runs tab: send its Tasks, edit each
raw Output, then judge the Run.

Fake Candidate Adapters, Judges, Clock and Editor stand in for every model call, time and
$EDITOR, so nothing here touches the network or waits on wall time.
"""

import shutil
from pathlib import Path

import pytest
from textual.widgets import OptionList, Select, Static, TextArea

from bench import CandidateToolError, Workspace
from bench.tui.app import BenchApp
from bench.tui.runs import ManualTaskScreen
from test_tui import write_task
from test_workspace_attempts import FakeClock
from test_workspace_automated import FakeAdapter, reply
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import write_judge_setup

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = "claude@opus-5-5"
EXAMPLE = "T1.1-example"
RAW = "Predict churn for every customer next year."


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """The examples/ Task, an automated Candidate and a single-Judge Judge Setup."""
    shutil.copytree(REPO_ROOT / "examples", workspace_dir / "examples")
    data = candidate(baseline=True)  # automated, tool Claude
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    (workspace_dir / "config" / "prices.yaml").write_text(
        "models:\n  claude-opus-5-5:\n    input_per_mtok: 5.0\n    output_per_mtok: 25.0\n"
    )
    write_judge_setup(workspace_dir)  # openai/gpt-6
    return workspace_dir


def open_app(ws_dir: Path, clock: FakeClock, adapter: FakeAdapter, judges=None) -> BenchApp:
    return BenchApp(Workspace.open(ws_dir, clock=clock, candidate_adapters=lambda c: adapter,
                                   judges=judges))


def text(app: BenchApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


def options(app: BenchApp, selector: str) -> list[str]:
    widget = app.screen.query_one(selector, OptionList)
    return [str(widget.get_option_at_index(i).prompt) for i in range(widget.option_count)]


async def start_run(app: BenchApp, pilot) -> None:
    await pilot.click("#--content-tab-runs")
    await pilot.pause()
    app.query_one("#run-candidate", Select).value = CANDIDATE
    await pilot.pause()
    await pilot.click("#run-start")
    await pilot.pause()


async def send(app: BenchApp, pilot) -> None:
    await pilot.click("#run-send")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def edit_task(app: BenchApp, pilot, task_id: str = EXAMPLE) -> ManualTaskScreen:
    tasks = app.query_one("#run-tasks", OptionList)
    tasks.focus()
    tasks.highlighted = next(
        i for i in range(tasks.option_count) if tasks.get_option_at_index(i).id == task_id
    )
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, ManualTaskScreen)
    return app.screen


async def test_sending_a_runs_tasks_stores_raw_outputs_ready_to_edit(
    ws_dir: Path, clock: FakeClock
):
    adapter = FakeAdapter(clock, reply(RAW), seconds=4.5)
    app = open_app(ws_dir, clock, adapter)
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        assert options(app, "#run-tasks") == [f"{EXAMPLE}: not sent"]

        await send(app, pilot)
        status = text(app, "#run-status")
        listed = options(app, "#run-tasks")

    assert len(adapter.sent) == 1
    assert f"{EXAMPLE}: raw Output ready" in status
    assert "latency 0:04" in status and "cost $0.02" in status
    assert listed == [f"{EXAMPLE}: raw Output ready to edit"]


async def test_a_task_that_errors_twice_is_shown_and_sent_again_on_retry(
    ws_dir: Path, clock: FakeClock
):
    write_task(ws_dir, "T2.1", track="Specs")
    adapter = FakeAdapter(clock, reply(RAW), CandidateToolError("overloaded"),
                          CandidateToolError("500"), reply("A spec."))
    app = open_app(ws_dir, clock, adapter)
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        await send(app, pilot)
        status = text(app, "#run-status")
        after_failure = options(app, "#run-tasks")

        await send(app, pilot)  # the retry sends only the Task that failed
        retried = text(app, "#run-status")
        after_retry = options(app, "#run-tasks")

    assert f"{EXAMPLE}: raw Output ready" in status
    assert "T2.1 failed" in status and "errored twice" in status and "500" in status
    assert after_failure == [f"{EXAMPLE}: raw Output ready to edit",
                             "T2.1: failed, send again to retry"]
    assert len(adapter.sent) == 4
    assert "T2.1: raw Output ready" in retried and EXAMPLE not in retried
    assert after_retry == [f"{EXAMPLE}: raw Output ready to edit",
                           "T2.1: raw Output ready to edit"]


async def test_the_edit_pass_opens_the_task_screen_with_the_raw_output_in(
    ws_dir: Path, clock: FakeClock
):
    adapter = FakeAdapter(clock, reply(RAW), seconds=4.5)
    app = open_app(ws_dir, clock, adapter)
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        await send(app, pilot)
        clock.advance(3600)  # coming back later is not editing time

        screen = await edit_task(app, pilot)
        raw = screen.query_one("#task-raw", TextArea)
        draft = screen.query_one("#task-draft", TextArea)
        assert raw.read_only and raw.text == RAW
        assert draft.text == RAW
        clock.advance(90)
        draft.text = "Predict SMB churn 60 days ahead."
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, ManualTaskScreen)
        status = text(app, "#run-status")
        progress = text(app, "#run-progress")
        left = options(app, "#run-tasks")

    assert "Edit Distance 0.86" in status
    assert "active 1:30" in status and "latency 0:04" in status and "cost $0.02" in status
    assert "1/1 Tasks recorded" in progress
    assert left == []


async def test_an_abandoned_edit_pass_keeps_the_raw_output_for_another(
    ws_dir: Path, clock: FakeClock
):
    app = open_app(ws_dir, clock, FakeAdapter(clock, reply(RAW)))
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        await send(app, pilot)
        await edit_task(app, pilot)
        await pilot.press("escape", "a")
        await pilot.pause()
        status = text(app, "#run-status")
        listed = options(app, "#run-tasks")

    assert "abandoned" in status and "raw Output kept" in status
    assert listed == [f"{EXAMPLE}: raw Output ready to edit"]


async def test_a_task_not_yet_sent_cannot_be_edited(ws_dir: Path, clock: FakeClock):
    app = open_app(ws_dir, clock, FakeAdapter(clock))
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        tasks = app.query_one("#run-tasks", OptionList)
        tasks.focus()
        tasks.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        on_screen = app.screen
        status = text(app, "#run-status")

    assert not isinstance(on_screen, ManualTaskScreen)
    assert "send it first" in status
