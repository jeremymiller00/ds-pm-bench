import datetime
from pathlib import Path

import pytest
import yaml

from bench import ClearanceError, Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import configure_private_root, write_task

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


def test_all_time_in_the_external_editor_counts_and_its_text_is_the_final(
    ws_dir: Path, clock: FakeClock
):
    editor = FakeEditor(clock, returns="one two three four", open_seconds=600)
    ws = open_ws(ws_dir, clock, editor)
    run = ws.start_run(CANDIDATE, on=DAY)
    session = ws.begin_manual_task(run, "T1.1")
    clock.advance(30)
    session.submit_raw("one two six seven")

    edited = session.edit_externally()
    clock.advance(10)
    record = session.finish()

    assert editor.opened_with == ["one two six seven"]
    assert edited == "one two three four"
    assert record.active_seconds == 640
    assert record.latency_seconds == 30
    assert record.edit_distance == 0.5  # 2 of 4 words changed
    assert (run.folder / "final" / "T1.1.md").read_text() == "one two three four"
    assert results(ws_dir, run.id, "T1.1")["edit_distance"] == 0.5


def test_a_second_external_edit_reopens_the_last_edited_text(ws_dir: Path, clock: FakeClock):
    editor = FakeEditor(clock, returns="edited", open_seconds=5)
    ws = open_ws(ws_dir, clock, editor)
    session = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")
    session.submit_raw("raw")
    session.edit_externally()
    session.edit_externally()

    assert editor.opened_with == ["raw", "edited"]


def test_the_external_editor_cannot_be_opened_while_paused(ws_dir: Path, clock: FakeClock):
    editor = FakeEditor(clock, returns="edited", open_seconds=5)
    ws = open_ws(ws_dir, clock, editor)
    session = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")
    session.submit_raw("raw")
    session.pause()

    with pytest.raises(ValueError, match="paused"):
        session.edit_externally()
    assert editor.opened_with == []


def test_finishing_with_no_edit_keeps_the_raw_output_as_final(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    session = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")
    session.submit_raw("good as is")

    assert session.finish().edit_distance == 0


def test_abandoning_records_nothing_and_the_task_can_be_redone(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)
    first = ws.begin_manual_task(run, "T1.1")
    clock.advance(500)
    first.submit_raw("abandoned draft")
    first.abandon()

    assert run.pending == ("T1.1",)
    assert not (run.folder / "results").exists()
    assert not (run.folder / "raw").exists()
    with pytest.raises(ValueError, match="abandoned"):
        first.finish("late")

    clock.advance(1000)
    second = ws.begin_manual_task(run, "T1.1")
    clock.advance(20)
    second.submit_raw("redo")
    record = second.finish("redo")

    assert record.latency_seconds == 20
    assert record.active_seconds == 20
    assert run.pending == ()


def test_a_finished_task_cannot_be_begun_or_finished_again(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)
    session = ws.begin_manual_task(run, "T1.1")
    session.submit_raw("raw")
    session.finish("final")

    with pytest.raises(ValueError, match="finished"):
        session.finish("again")
    with pytest.raises(ValueError, match="already recorded"):
        ws.begin_manual_task(ws.resume_run(run.id), "T1.1")


def test_a_private_task_is_refused_before_any_content_is_shown(
    ws_dir: Path, clock: FakeClock, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize")
    configure_private_root(ws_dir, private)
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)  # cleared for public only
    (private / "tasks" / "T3.1" / "input.md").chmod(0)  # any read would raise

    try:
        with pytest.raises(ClearanceError, match="T3.1"):
            ws.begin_manual_task(run, "T3.1")
    finally:
        (private / "tasks" / "T3.1" / "input.md").chmod(0o644)


def test_a_candidates_flat_per_task_cost_is_the_tasks_cost(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    ws.update_candidate(CANDIDATE, cost_per_task_usd=0.75)
    run = ws.start_run(CANDIDATE, on=DAY)
    session = ws.begin_manual_task(run, "T1.1")
    session.submit_raw("raw")

    assert session.finish().cost_usd == 0.75
    assert results(ws_dir, run.id, "T1.1")["cost_usd"] == 0.75


def test_a_candidate_without_a_flat_cost_records_null_cost(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    assert ws.candidate(CANDIDATE).cost_per_task_usd is None
    run = ws.start_run(CANDIDATE, on=DAY)
    session = ws.begin_manual_task(run, "T1.1")
    session.submit_raw("raw")

    assert session.finish().cost_usd is None
    assert results(ws_dir, run.id, "T1.1")["cost_usd"] is None


def test_manual_time_is_recorded_on_the_task(ws_dir: Path, clock: FakeClock):
    write_task(ws_dir, "T2.1", track="Specs", manual_time="null")
    ws = open_ws(ws_dir, clock)
    assert "Manual Time" in ws.task("T2.1").placeholders

    task = ws.record_manual_time("T2.1", 38.5)

    assert task.manual_time_minutes == 38.5
    assert Workspace.open(ws_dir).task("T2.1").placeholders == ()


def test_a_manual_time_stopwatch_records_the_minutes_it_ran(
    workspace_dir: Path, clock: FakeClock
):
    write_task(workspace_dir, "T1.1", manual_time="null")
    ws = open_ws(workspace_dir, clock)

    stopwatch = ws.begin_manual_time("T1.1")
    clock.advance(25 * 60 + 33)
    assert stopwatch.elapsed_seconds == 1533
    task = stopwatch.finish()

    assert task.manual_time_minutes == 25.6
    assert Workspace.open(workspace_dir).task("T1.1").manual_time_minutes == 25.6
