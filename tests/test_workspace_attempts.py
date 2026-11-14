import datetime
from pathlib import Path

import pytest
import yaml

from bench import AttemptError, ClearanceError, GateNotPassed, Workspace
from test_workspace_candidate_authoring import CHECKLIST
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import configure_private_root, write_task

DAY = datetime.date(2026, 11, 13)
CANDIDATE = "chatbot@web-1"


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self, today: datetime.date = DAY) -> None:
        self.seconds = 1000.0
        self.date = today

    def now(self) -> float:
        return self.seconds

    def today(self) -> datetime.date:
        return self.date

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

    attempt = ws.begin_manual_task(run, "T1.1")
    assert attempt.prompt == (
        "Frame this business problem as a data problem.\n\n"
        "Churn rose 4% last quarter in the SMB segment."
    )
    clock.advance(90)
    attempt.submit_raw("Predict churn for all customers next year.")
    clock.advance(300)
    record = attempt.finish("Predict SMB churn 60 days ahead.")

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
    attempt = ws.begin_manual_task(run, "T1.1")
    clock.advance(60)
    attempt.submit_raw("raw")
    clock.advance(100)
    attempt.pause()
    assert attempt.paused
    clock.advance(1000)  # interrupted
    attempt.resume()
    clock.advance(40)
    assert attempt.active_seconds == 200  # a running timer for the TUI
    attempt.pause()
    clock.advance(500)  # still paused when saved: does not count

    record = attempt.finish("final")

    assert record.latency_seconds == 60
    assert record.active_seconds == 200


def test_pausing_is_only_for_editing_and_cannot_be_doubled(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    attempt = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")

    with pytest.raises(AttemptError, match="raw Output"):
        attempt.pause()
    with pytest.raises(AttemptError, match="raw Output"):
        attempt.finish("final")
    attempt.submit_raw("raw")
    with pytest.raises(AttemptError, match="already"):
        attempt.submit_raw("again")
    with pytest.raises(AttemptError, match="not paused"):
        attempt.resume()
    attempt.pause()
    with pytest.raises(AttemptError, match="already paused"):
        attempt.pause()


def test_all_time_in_the_external_editor_counts_and_its_text_is_the_final(
    ws_dir: Path, clock: FakeClock
):
    editor = FakeEditor(clock, returns="one two three four", open_seconds=600)
    ws = open_ws(ws_dir, clock, editor)
    run = ws.start_run(CANDIDATE, on=DAY)
    attempt = ws.begin_manual_task(run, "T1.1")
    clock.advance(30)
    attempt.submit_raw("one two six seven")

    edited = attempt.edit_externally()
    clock.advance(10)
    record = attempt.finish()

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
    attempt = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")
    attempt.submit_raw("raw")
    attempt.edit_externally()
    attempt.edit_externally()

    assert editor.opened_with == ["raw", "edited"]


def test_the_external_editor_cannot_be_opened_while_paused(ws_dir: Path, clock: FakeClock):
    editor = FakeEditor(clock, returns="edited", open_seconds=5)
    ws = open_ws(ws_dir, clock, editor)
    attempt = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")
    attempt.submit_raw("raw")
    attempt.pause()

    with pytest.raises(AttemptError, match="paused"):
        attempt.edit_externally()
    assert editor.opened_with == []


def test_finishing_with_no_edit_keeps_the_raw_output_as_final(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    attempt = ws.begin_manual_task(ws.start_run(CANDIDATE, on=DAY), "T1.1")
    attempt.submit_raw("good as is")

    assert attempt.finish().edit_distance == 0


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
    with pytest.raises(AttemptError, match="abandoned"):
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
    attempt = ws.begin_manual_task(run, "T1.1")
    attempt.submit_raw("raw")
    attempt.finish("final")

    with pytest.raises(AttemptError, match="finished"):
        attempt.finish("again")
    with pytest.raises(AttemptError, match="already recorded"):
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
    attempt = ws.begin_manual_task(run, "T1.1")
    attempt.submit_raw("raw")

    assert attempt.finish().cost_usd == 0.75
    assert results(ws_dir, run.id, "T1.1")["cost_usd"] == 0.75


def test_a_candidate_without_a_flat_cost_records_null_cost(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    assert ws.candidate(CANDIDATE).cost_per_task_usd is None
    run = ws.start_run(CANDIDATE, on=DAY)
    attempt = ws.begin_manual_task(run, "T1.1")
    attempt.submit_raw("raw")

    assert attempt.finish().cost_usd is None
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


def test_a_narrowed_gate_stops_private_input_to_an_open_run(
    workspace_dir: Path, clock: FakeClock, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(workspace_dir, "T1.1")
    write_task(private, "T3.1", track="Prioritize")
    configure_private_root(workspace_dir, private)
    write_candidate(workspace_dir, CANDIDATE,
                    candidate(adapter="manual_assisted", env_vars=[], baseline=True))
    ws = open_ws(workspace_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)  # cleared for public and private

    ws.record_gate(CANDIDATE, reviewer="A. Nother", on=DAY, checklist=CHECKLIST,
                   passed=True, clearance="public_only")

    with pytest.raises(ClearanceError, match="T3.1"):
        ws.task_input(run, "T3.1")
    with pytest.raises(ClearanceError, match="T3.1"):
        ws.begin_manual_task(run, "T3.1")
    with pytest.raises(ClearanceError):
        ws.resume_run(run.id)
    assert ws.task_input(run, "T1.1")


def test_a_failed_gate_stops_an_open_run_beginning_a_task(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)

    ws.record_gate(CANDIDATE, reviewer="A. Nother", on=DAY, checklist=CHECKLIST,
                   passed=False, clearance=None)

    with pytest.raises(GateNotPassed):
        ws.begin_manual_task(run, "T1.1")


def test_a_run_of_an_automated_candidate_cannot_be_worked_by_hand(
    workspace_dir: Path, clock: FakeClock
):
    write_task(workspace_dir, "T1.1")
    data = candidate(adapter="automated", baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "api@1", data)
    ws = open_ws(workspace_dir, clock)
    run = ws.start_run("api@1", on=DAY)

    with pytest.raises(AttemptError, match="manual_assisted"):
        ws.begin_manual_task(run, "T1.1")


def test_a_runs_progress_counts_the_tasks_recorded(ws_dir: Path, clock: FakeClock):
    write_task(ws_dir, "T2.1", track="Specs")
    ws = open_ws(ws_dir, clock)
    run = ws.start_run(CANDIDATE, on=DAY)
    assert run.progress == (0, 2)

    attempt = ws.begin_manual_task(run, "T1.1")
    attempt.submit_raw("raw")
    attempt.finish()

    assert run.progress == (1, 2)
