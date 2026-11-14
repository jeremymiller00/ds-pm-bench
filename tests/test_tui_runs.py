"""Textual pilot tests for the Runs tab: a manual-assisted Run worked through the TUI.

A fake Clock and Editor stand in for time and $EDITOR, so nothing here waits on wall time.
"""

import shutil
from pathlib import Path

import pytest
import yaml
from textual.widgets import OptionList, Select, Static, TextArea

from bench import Workspace
from bench.tui.app import BenchApp
from bench.tui.runs import ManualTaskScreen
from test_workspace_candidates import candidate, write_candidate
from test_workspace_manual_session import FakeClock

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
