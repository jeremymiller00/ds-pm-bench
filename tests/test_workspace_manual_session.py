import datetime
from pathlib import Path

import pytest
import yaml

from bench import Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import write_task

DAY = datetime.date(2026, 11, 13)
CANDIDATE = "chatbot@web-1"


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.seconds = 1000.0

    def now(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


class FakeEditor:
    """Stands in for $EDITOR: returns scripted text after the scripted open time."""

    def __init__(self, clock: FakeClock, *, returns: str, open_seconds: float) -> None:
        self.clock = clock
        self.returns = returns
        self.open_seconds = open_seconds
        self.opened_with: list[str] = []

    def edit(self, text: str) -> str:
        self.opened_with.append(text)
        self.clock.advance(self.open_seconds)
        return self.returns


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    write_task(workspace_dir, "T1.1")
    data = candidate(adapter="manual_assisted", env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    return workspace_dir


def open_ws(ws_dir: Path, clock: FakeClock, editor=None) -> Workspace:
    return Workspace.open(ws_dir, clock=clock, editor=editor)


def results(ws_dir: Path, run_id: str, task_id: str) -> dict:
    return yaml.safe_load((ws_dir / "runs" / run_id / "results" / f"{task_id}.yaml").read_text())


def test_a_manual_task_records_latency_to_paste_and_active_time_to_save(
    ws_dir: Path, clock: FakeClock
):
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)

    session = ws.begin_manual_task(run, "T1.1")
    assert session.prompt == (
        "Frame this business problem as a data problem.\n\n"
        "Churn rose 4% last quarter in the SMB segment."
    )
    clock.advance(90)
    session.submit_raw("Predict churn for all customers next year.")
    clock.advance(300)
    record = session.finish("Predict SMB churn 60 days ahead.")

    assert record.latency_seconds == 90
    assert record.active_seconds == 390
    saved = results(ws_dir, run.id, "T1.1")
    assert saved["latency_seconds"] == 90
    assert saved["active_seconds"] == 390
    assert saved["cost_usd"] is None
    assert (run.folder / "raw" / "T1.1.md").read_text() == (
        "Predict churn for all customers next year."
    )
    assert (run.folder / "final" / "T1.1.md").read_text() == "Predict SMB churn 60 days ahead."


def test_pauses_in_the_built_in_editor_are_not_active_time(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)
    session = ws.begin_manual_task(run, "T1.1")
    clock.advance(60)
    session.submit_raw("raw")
    clock.advance(100)
    session.pause()
    assert session.paused
    clock.advance(1000)  # interrupted
    session.resume()
    clock.advance(40)
    assert session.active_seconds == 200  # a running timer for the TUI
    session.pause()
    clock.advance(500)  # still paused when saved: does not count

    record = session.finish("final")

    assert record.latency_seconds == 60
    assert record.active_seconds == 200


def test_pausing_is_only_for_editing_and_cannot_be_doubled(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    session = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")

    with pytest.raises(ValueError, match="raw Output"):
        session.pause()
    with pytest.raises(ValueError, match="raw Output"):
        session.finish("final")
    session.submit_raw("raw")
    with pytest.raises(ValueError, match="already"):
        session.submit_raw("again")
    with pytest.raises(ValueError, match="not paused"):
        session.resume()
    session.pause()
    with pytest.raises(ValueError, match="already paused"):
        session.pause()
