"""An automated Candidate's Attempt: it starts with the Returned Output already in."""

import datetime
from pathlib import Path

import pytest
import yaml

from bench import (
    AttemptError,
    CandidateToolError,
    ClearanceError,
    GateNotPassed,
    Workspace,
)
from test_workspace_attempts import FakeClock, FakeEditor
from test_workspace_automated import FakeAdapter, reply
from test_workspace_candidate_authoring import CHECKLIST
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import configure_private_root, write_task

DAY = datetime.date(2026, 11, 13)
CANDIDATE = "claude@opus-5-5"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    write_task(workspace_dir, "T1.1")
    data = candidate(baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    (workspace_dir / "config" / "prices.yaml").write_text(
        "models:\n  claude-opus-5-5:\n    input_per_mtok: 5.0\n    output_per_mtok: 25.0\n"
    )
    return workspace_dir


def open_ws(ws_dir: Path, clock: FakeClock, adapter: FakeAdapter, editor=None) -> Workspace:
    return Workspace.open(ws_dir, clock=clock, editor=editor,
                          candidate_adapters=lambda c: adapter)


def results(run, task_id: str) -> dict:
    return yaml.safe_load((run.folder / "results" / f"{task_id}.yaml").read_text())


def test_the_automated_attempt_records_editing_time_and_the_api_calls_latency_and_cost(
    ws_dir: Path, clock: FakeClock
):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("one two three"), seconds=4.5))
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T1.1")
    clock.advance(3600)  # the PM comes back to it later: not active time

    attempt = ws.begin_attempt(run, "T1.1")
    assert attempt.raw == attempt.draft == "one two three"
    clock.advance(90)
    record = attempt.finish("one two four")

    assert record.active_seconds == 90
    assert record.latency_seconds == 4.5
    assert record.cost_usd == pytest.approx(0.0175)
    stored = results(run, "T1.1")
    assert (stored["active_seconds"], stored["latency_seconds"]) == (90, 4.5)
    assert (run.folder / "raw" / "T1.1.md").read_text() == "one two three"
    assert (run.folder / "final" / "T1.1.md").read_text() == "one two four"
    assert ws.returned_output(run, "T1.1") is None
    assert run.pending == ()


def test_the_recorded_task_result_keeps_the_provider_calls_model_tokens_and_tries(
    ws_dir: Path, clock: FakeClock
):
    (ws_dir / "config" / "prices.yaml").write_text("models: {}\n")
    adapter = FakeAdapter(clock, CandidateToolError("overloaded"),
                          reply("raw", tokens=(1200, 300)))
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T1.1")

    record = ws.begin_attempt(run, "T1.1").finish("final")

    assert record.call is not None
    assert (record.call.model, record.call.input_tokens, record.call.output_tokens,
            record.call.tries) == ("claude-opus-5-5", 1200, 300, 2)
    note = "no price for model claude-opus-5-5 in config/prices.yaml"
    assert record.call.cost_note == note
    stored = results(run, "T1.1")
    assert (stored["model"], stored["input_tokens"], stored["output_tokens"],
            stored["tries"], stored["cost_note"]) == ("claude-opus-5-5", 1200, 300, 2, note)
    assert not (run.folder / "returned" / "T1.1.yaml").exists()


def sent_and_returned(ws: Workspace):
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T1.1")
    return run


def test_pauses_in_the_automated_attempt_are_not_active_time(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("draft")))
    run = sent_and_returned(ws)

    attempt = ws.begin_attempt(run, "T1.1")
    clock.advance(30)
    attempt.pause()
    clock.advance(500)
    assert attempt.active_seconds == 30
    attempt.resume()
    clock.advance(20)
    record = attempt.finish()

    assert record.active_seconds == 50
    assert (run.folder / "final" / "T1.1.md").read_text() == "draft"


def test_the_automated_attempt_opens_the_raw_output_in_the_external_editor(
    ws_dir: Path, clock: FakeClock
):
    editor = FakeEditor(clock, returns="edited draft", open_seconds=600)
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("raw draft")), editor)
    run = sent_and_returned(ws)

    attempt = ws.begin_attempt(run, "T1.1")
    assert attempt.edit_externally() == "edited draft"
    record = attempt.finish()

    assert editor.opened_with == ["raw draft"]
    assert record.active_seconds == 600
    assert (run.folder / "raw" / "T1.1.md").read_text() == "raw draft"
    assert (run.folder / "final" / "T1.1.md").read_text() == "edited draft"


def fail_gate(ws: Workspace) -> None:
    ws.record_gate(CANDIDATE, reviewer="A. Nother", on=DAY, checklist=CHECKLIST,
                   passed=False, clearance=None)


def test_a_failed_gate_stops_an_automated_attempt_beginning(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply()))
    run = sent_and_returned(ws)
    fail_gate(ws)

    with pytest.raises(GateNotPassed):
        ws.begin_attempt(run, "T1.1")


def test_an_automated_attempt_open_when_the_gate_fails_cannot_save_and_keeps_the_raw_output(
    ws_dir: Path, clock: FakeClock
):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("draft")))
    run = sent_and_returned(ws)
    attempt = ws.begin_attempt(run, "T1.1")
    fail_gate(ws)

    with pytest.raises(GateNotPassed):
        attempt.finish()

    assert run.pending == ("T1.1",)
    assert ws.returned_output(run, "T1.1").raw == "draft"


def test_a_narrowed_clearance_stops_saving_a_private_tasks_automated_attempt(
    ws_dir: Path, clock: FakeClock, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(private, "T2.1", track="Specs")
    configure_private_root(ws_dir, private)
    write_candidate(ws_dir, CANDIDATE, candidate(baseline=True))  # public and private
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("private draft")))
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T2.1")
    attempt = ws.begin_attempt(run, "T2.1")

    ws.record_gate(CANDIDATE, reviewer="A. Nother", on=DAY, checklist=CHECKLIST,
                   passed=True, clearance="public_only")

    with pytest.raises(ClearanceError, match="T2.1"):
        attempt.finish()
    with pytest.raises(ClearanceError, match="T2.1"):
        ws.begin_attempt(run, "T2.1")
    assert "T2.1" in run.pending


def test_a_private_tasks_automated_attempt_saves_its_content_to_the_private_root(
    ws_dir: Path, clock: FakeClock, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(private, "T2.1", track="Specs")
    configure_private_root(ws_dir, private)
    write_candidate(ws_dir, CANDIDATE, candidate(baseline=True))
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("private draft")))
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T2.1")

    ws.begin_attempt(run, "T2.1").finish("private final")

    content = private / "runs" / run.id
    assert (content / "raw" / "T2.1.md").read_text() == "private draft"
    assert (content / "final" / "T2.1.md").read_text() == "private final"
    assert not (run.folder / "final").exists()
    assert ws.returned_output(run, "T2.1") is None


def test_abandoning_an_automated_attempt_keeps_the_raw_output_for_another(
    ws_dir: Path, clock: FakeClock
):
    adapter = FakeAdapter(clock, reply("draft"))
    ws = open_ws(ws_dir, clock, adapter)
    run = sent_and_returned(ws)
    first = ws.begin_attempt(run, "T1.1")
    clock.advance(100)
    first.abandon()

    second = ws.begin_attempt(ws.resume_run(run.id), "T1.1")
    clock.advance(10)
    record = second.finish("final")

    assert record.active_seconds == 10
    assert len(adapter.sent) == 1  # not sent again


def test_the_automated_attempt_survives_reopening_the_workspace(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("draft"), seconds=7))
    run_id = sent_and_returned(ws).id

    reopened = open_ws(ws_dir, clock, FakeAdapter(clock))
    record = reopened.begin_attempt(reopened.resume_run(run_id), "T1.1").finish()

    assert record.latency_seconds == 7
    assert record.cost_usd == pytest.approx(0.0175)


def test_a_task_with_no_raw_output_has_no_automated_attempt(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock))
    run = ws.start_run(CANDIDATE, on=DAY)

    with pytest.raises(AttemptError, match="no raw Output"):
        ws.begin_attempt(run, "T1.1")


def test_a_recorded_task_has_no_second_automated_attempt(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply()))
    run = sent_and_returned(ws)
    attempt = ws.begin_attempt(run, "T1.1")
    attempt.finish()

    with pytest.raises(AttemptError, match="already recorded"):
        ws.begin_attempt(run, "T1.1")
    with pytest.raises(AttemptError):
        attempt.finish()


def test_the_raw_output_of_an_automated_attempt_is_already_in(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply()))
    attempt = ws.begin_attempt(sent_and_returned(ws), "T1.1")

    with pytest.raises(AttemptError, match="already submitted"):
        attempt.submit_raw("pasted")


def test_a_manual_assisted_runs_attempt_starts_without_a_raw_output(
    ws_dir: Path, clock: FakeClock
):
    data = candidate(adapter="manual_assisted", env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(ws_dir, CANDIDATE, data)
    ws = open_ws(ws_dir, clock, FakeAdapter(clock))
    run = ws.start_run(CANDIDATE, on=DAY)

    attempt = ws.begin_attempt(run, "T1.1")  # the same method, chosen by Adapter kind

    assert attempt.raw is None


def test_a_task_whose_adapter_failed_twice_can_be_retried_later(
    ws_dir: Path, clock: FakeClock
):
    adapter = FakeAdapter(clock, CandidateToolError("overloaded"), CandidateToolError("500"),
                          reply("at last"), seconds=2)
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)
    with pytest.raises(CandidateToolError, match="errored twice"):
        ws.run_automated_task(run, "T1.1")
    clock.advance(600)

    returned = ws.run_automated_task(ws.resume_run(run.id), "T1.1")
    record = ws.begin_attempt(run, "T1.1").finish()

    assert returned.tries == 1
    assert record.latency_seconds == 2  # the later call only
    assert len(adapter.sent) == 3
    assert run.pending == ()
