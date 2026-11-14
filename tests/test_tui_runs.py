"""Textual pilot tests for the Runs tab: a manual-assisted Run worked through the TUI.

A fake Clock and Editor stand in for time and $EDITOR, so nothing here waits on wall time.
"""

import contextlib
import shutil
from pathlib import Path

import pytest
import yaml
from textual.widgets import OptionList, Select, Static, TextArea

from bench import Workspace
from bench.tui.app import BenchApp
from bench.tui.runs import ManualTaskScreen, ManualTimeScreen
from test_tui import configure_private_root, write_task
from test_workspace_attempts import FakeClock, FakeEditor
from test_workspace_candidates import candidate, write_candidate

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = "chatbot@web-1"
EXAMPLE = "T1.1-example"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """The examples/ Task and a manual-assisted Candidate with no API."""
    shutil.copytree(REPO_ROOT / "examples", workspace_dir / "examples")
    data = candidate(adapter="manual_assisted", env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    return workspace_dir


def text(app: BenchApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


async def open_runs(app: BenchApp, pilot) -> None:
    await pilot.click("#--content-tab-runs")
    await pilot.pause()


async def start_run(app: BenchApp, pilot, candidate_id: str = CANDIDATE) -> None:
    await open_runs(app, pilot)
    app.query_one("#run-candidate", Select).value = candidate_id
    await pilot.pause()
    await pilot.click("#run-start")
    await pilot.pause()


async def open_task(app: BenchApp, pilot, task_id: str = EXAMPLE) -> ManualTaskScreen:
    tasks = app.query_one("#run-tasks", OptionList)
    tasks.focus()
    tasks.highlighted = next(
        i for i in range(tasks.option_count) if tasks.get_option_at_index(i).id == task_id
    )
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, ManualTaskScreen)
    return app.screen


async def paste_raw(app: BenchApp, pilot, raw: str) -> None:
    app.screen.query_one("#task-raw", TextArea).text = raw
    await pilot.press("ctrl+s")
    await pilot.pause()


def results(ws_dir: Path, task_id: str = EXAMPLE) -> dict:
    (path,) = (ws_dir / "runs").glob(f"*/results/{task_id}.yaml")
    return yaml.safe_load(path.read_text())


async def test_a_full_run_of_the_example_task_completes_with_no_api(
    ws_dir: Path, clock: FakeClock
):
    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        assert "0/1 Tasks recorded" in text(app, "#run-progress")

        screen = await open_task(app, pilot)
        prompt = screen.query_one("#task-prompt", TextArea)
        assert prompt.read_only
        assert prompt.text.startswith("You are helping a data science product manager.")
        await pilot.press("escape", "c")  # leave the text box, then copy
        assert app.clipboard == prompt.text

        clock.advance(60)
        await paste_raw(app, pilot, "Predict churn for every customer next year.")
        draft = screen.query_one("#task-draft", TextArea)
        assert draft.text == "Predict churn for every customer next year."

        clock.advance(120)
        screen.refresh_timer()
        assert "3:00" in text(app, "#task-timer")

        draft.text = "Predict SMB churn 60 days ahead."
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, ManualTaskScreen)
        status = text(app, "#run-status")
        progress = text(app, "#run-progress")

    assert "Edit Distance 0.86" in status
    assert "active 3:00" in status and "latency 1:00" in status
    assert "1/1 Tasks recorded" in progress
    saved = results(ws_dir)
    assert (saved["active_seconds"], saved["latency_seconds"]) == (180, 60)


async def test_pause_is_refused_before_the_raw_output_then_stops_the_timer(
    ws_dir: Path, clock: FakeClock
):
    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        screen = await open_task(app, pilot)
        await pilot.press("escape", "p")
        assert not screen.attempt.paused
        assert "submit the raw Output before you pause" in text(app, "#task-error")

        clock.advance(30)
        await paste_raw(app, pilot, "Raw draft.")
        await pilot.press("escape", "p")
        assert screen.attempt.paused
        assert screen.query_one("#task-draft", TextArea).read_only
        assert "paused" in text(app, "#task-timer")

        clock.advance(600)  # an interruption: none of it counts
        await pilot.press("e")
        assert "resume the paused timer before editing" in text(app, "#task-error")
        await pilot.press("p")
        assert not screen.attempt.paused
        assert not screen.query_one("#task-draft", TextArea).read_only

        clock.advance(45)
        await pilot.press("ctrl+s")
        await pilot.pause()

    saved = results(ws_dir)
    assert (saved["active_seconds"], saved["latency_seconds"]) == (75, 30)


async def test_editing_in_editor_suspends_the_app_and_counts_all_its_open_time(
    ws_dir: Path, clock: FakeClock
):
    editor = FakeEditor(clock, returns="Predict SMB churn 60 days ahead.", open_seconds=240)
    app = BenchApp(Workspace.open(ws_dir, clock=clock, editor=editor))
    suspended: list[bool] = []

    @contextlib.contextmanager
    def suspend():  # the terminal is the boundary: record that the app gave it up
        suspended.append(True)
        yield

    app.suspend = suspend
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        screen = await open_task(app, pilot)
        assert "all the time it is open counts" in text(app, "#task-help")
        clock.advance(10)
        await paste_raw(app, pilot, "Predict churn for every customer next year.")
        await pilot.press("escape", "e")
        await pilot.pause()
        assert suspended == [True]
        assert editor.opened_with == ["Predict churn for every customer next year."]
        assert screen.query_one("#task-draft", TextArea).text == (
            "Predict SMB churn 60 days ahead."
        )
        await pilot.press("ctrl+s")
        await pilot.pause()

    saved = results(ws_dir)
    assert (saved["active_seconds"], saved["latency_seconds"]) == (250, 10)
    final = next((ws_dir / "runs").glob(f"*/final/{EXAMPLE}.md")).read_text()
    assert final == "Predict SMB churn 60 days ahead."


async def test_suite_choice_is_offered_only_where_clearance_allows(
    ws_dir: Path, tmp_path: Path, clock: FakeClock
):
    private = tmp_path / "private"
    configure_private_root(ws_dir, private)
    write_task(private, "T1.2")
    write_task(ws_dir, "T2.1", track="Specs", gold="TODO: write the Gold Answer")
    write_candidate(ws_dir, "claude@opus-5-5",  # public_and_private
                    candidate(adapter="manual_assisted", env_vars=[]))

    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await open_runs(app, pilot)
        suite = app.query_one("#run-suite", Select)
        app.query_one("#run-candidate", Select).value = CANDIDATE
        await pilot.pause()
        assert suite.disabled and suite.value == "public_only"

        app.query_one("#run-candidate", Select).value = "claude@opus-5-5"
        await pilot.pause()
        assert not suite.disabled and suite.value == "public_and_private"
        suite.value = "public_only"
        await pilot.click("#run-start")
        await pilot.pause()
        progress = text(app, "#run-progress")
        pending = app.query_one("#run-tasks", OptionList)
        pending_ids = [pending.get_option_at_index(i).id for i in range(pending.option_count)]

    assert "(public_only): 0/1 Tasks recorded" in progress
    assert "T2.1: Gold Answer" in progress  # excluded as a placeholder
    assert pending_ids == [EXAMPLE]


async def test_a_half_finished_run_resumes_with_its_progress(
    ws_dir: Path, clock: FakeClock
):
    write_task(ws_dir, "T2.1", track="Specs")
    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await start_run(app, pilot)
        await open_task(app, pilot, "T2.1")
        await paste_raw(app, pilot, "A spec.")
        await pilot.press("ctrl+s")
        await pilot.pause()
        (run_id,) = Workspace.open(ws_dir).run_ids()

    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await open_runs(app, pilot)
        app.query_one("#run-existing", Select).value = run_id
        await pilot.pause()
        await pilot.click("#run-resume")
        await pilot.pause()
        progress = text(app, "#run-progress")
        pending = app.query_one("#run-tasks", OptionList)
        pending_ids = [pending.get_option_at_index(i).id for i in range(pending.option_count)]

    assert f"Run {run_id} (public_only): 1/2 Tasks recorded" in progress
    assert pending_ids == [EXAMPLE]


async def test_a_placeholder_manual_time_is_timed_with_a_stopwatch(
    ws_dir: Path, clock: FakeClock
):
    write_task(ws_dir, "T2.1", track="Specs", manual_time="null")
    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await open_runs(app, pilot)
        untimed = app.query_one("#manual-time-tasks", OptionList)
        assert [untimed.get_option_at_index(i).id for i in range(untimed.option_count)] == [
            "T2.1"
        ]
        untimed.focus()
        untimed.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ManualTimeScreen)

        clock.advance(25 * 60 + 30)
        screen.refresh_timer()
        assert "25:30" in text(app, "#stopwatch-timer")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, ManualTimeScreen)
        status = text(app, "#run-status")
        left = app.query_one("#manual-time-tasks", OptionList).option_count

    assert "T2.1 Manual Time recorded: 25.5 minutes" in status
    assert left == 0
    assert Workspace.open(ws_dir).task("T2.1").manual_time_minutes == 25.5


async def test_manual_assisted_and_automated_candidates_are_both_offered_for_a_run(
    ws_dir: Path, clock
):
    automated = candidate(tool="Api", version="1", baseline=False)
    write_candidate(ws_dir, "api@1", automated)

    app = BenchApp(Workspace.open(ws_dir, clock=clock))
    async with app.run_test() as pilot:
        await open_runs(app, pilot)
        offered = [value for _, value in app.query_one("#run-candidate", Select)._options]

    assert CANDIDATE in offered
    assert "api@1" in offered
