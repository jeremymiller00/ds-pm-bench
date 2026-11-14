"""The edit pass of an automated Run: an Attempt that starts with the raw Output returned."""

import datetime
from pathlib import Path

import pytest
import yaml

from bench import Workspace
from test_workspace_attempts import FakeClock, FakeEditor
from test_workspace_automated import FakeAdapter, reply
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import write_task

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


def test_the_edit_pass_records_editing_time_and_the_api_calls_latency_and_cost(
    ws_dir: Path, clock: FakeClock
):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("one two three"), seconds=4.5))
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T1.1")
    clock.advance(3600)  # the PM comes back to it later: not active time

    attempt = ws.begin_edit_pass(run, "T1.1")
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
    assert ws.pending_output(run, "T1.1") is None
    assert run.pending == ()


def sent_and_pending(ws: Workspace):
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T1.1")
    return run


def test_pauses_in_the_edit_pass_are_not_active_time(ws_dir: Path, clock: FakeClock):
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("draft")))
    run = sent_and_pending(ws)

    attempt = ws.begin_edit_pass(run, "T1.1")
    clock.advance(30)
    attempt.pause()
    clock.advance(500)
    assert attempt.active_seconds == 30
    attempt.resume()
    clock.advance(20)
    record = attempt.finish()

    assert record.active_seconds == 50
    assert (run.folder / "final" / "T1.1.md").read_text() == "draft"


def test_the_edit_pass_opens_the_raw_output_in_the_external_editor(
    ws_dir: Path, clock: FakeClock
):
    editor = FakeEditor(clock, returns="edited draft", open_seconds=600)
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("raw draft")), editor)
    run = sent_and_pending(ws)

    attempt = ws.begin_edit_pass(run, "T1.1")
    assert attempt.edit_externally() == "edited draft"
    record = attempt.finish()

    assert editor.opened_with == ["raw draft"]
    assert record.active_seconds == 600
    assert (run.folder / "raw" / "T1.1.md").read_text() == "raw draft"
    assert (run.folder / "final" / "T1.1.md").read_text() == "edited draft"
