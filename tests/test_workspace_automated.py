"""Running one Task of an automated Run through the Workspace, with fake Candidate Adapters."""

import datetime
from pathlib import Path

import pytest
import yaml

from bench import (
    AttemptError,
    CandidateReply,
    CandidateToolError,
    ClearanceError,
    MissingSecret,
    Workspace,
)
from test_workspace_attempts import FakeClock
from test_workspace_candidate_authoring import CHECKLIST
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import configure_private_root, write_task

DAY = datetime.date(2026, 11, 13)
CANDIDATE = "claude@opus-5-5"
PROMPT = (
    "Frame this business problem as a data problem.\n\n"
    "Churn rose 4% last quarter in the SMB segment."
)


class FakeAdapter:
    """A Candidate Adapter that replays scripted replies (or errors), each after a delay."""

    def __init__(self, clock: FakeClock, *script, seconds: float = 3.0) -> None:
        self.clock = clock
        self.script = list(script)
        self.seconds = seconds
        self.sent: list[str] = []

    def send(self, text: str) -> CandidateReply:
        self.sent.append(text)
        self.clock.advance(self.seconds)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def reply(text: str = "A framed problem.", tokens: tuple[int, int] = (1000, 500)):
    return CandidateReply(text=text, input_tokens=tokens[0], output_tokens=tokens[1])


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


def open_ws(ws_dir: Path, clock: FakeClock, adapter: FakeAdapter) -> Workspace:
    return Workspace.open(ws_dir, clock=clock, candidate_adapters=lambda c: adapter)


def test_an_automated_task_sends_prompt_and_input_and_stores_the_raw_output_pending_edit(
    ws_dir: Path, clock: FakeClock
):
    adapter = FakeAdapter(clock, reply("A framed problem."), seconds=4.5)
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)

    pending = ws.run_automated_task(run, "T1.1")

    assert adapter.sent == [PROMPT]
    assert pending.raw == "A framed problem."
    assert pending.latency_seconds == 4.5
    assert (pending.input_tokens, pending.output_tokens) == (1000, 500)
    # 1000 * $5/M + 500 * $25/M
    assert pending.cost_usd == pytest.approx(0.0175)
    assert pending.cost_note is None
    assert pending.tries == 1
    assert ws.pending_output(run, "T1.1") == pending
    assert (run.folder / "raw" / "T1.1.md").read_text() == "A framed problem."
    # Not yet an Output: the edit pass (an Attempt) finishes it.
    assert run.pending == ("T1.1",)
    assert not (run.folder / "results" / "T1.1.yaml").exists()


def test_the_cost_is_null_with_a_note_when_the_model_has_no_price(
    ws_dir: Path, clock: FakeClock
):
    (ws_dir / "config" / "prices.yaml").write_text("models: {}\n")
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply()))
    run = ws.start_run(CANDIDATE, on=DAY)

    pending = ws.run_automated_task(run, "T1.1")

    assert pending.cost_usd is None
    assert pending.cost_note == "no price for model claude-opus-5-5 in config/prices.yaml"
    stored = yaml.safe_load((run.folder / "pending" / "T1.1.yaml").read_text())
    assert stored["cost_usd"] is None


def test_a_tool_error_is_retried_exactly_once(ws_dir: Path, clock: FakeClock):
    adapter = FakeAdapter(clock, CandidateToolError("overloaded"), reply("second try"))
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)

    pending = ws.run_automated_task(run, "T1.1")

    assert adapter.sent == [PROMPT, PROMPT]
    assert pending.raw == "second try"
    assert pending.tries == 2
    # Latency is the wait for the first draft, the failed try included.
    assert pending.latency_seconds == 6.0


def test_a_second_tool_error_raises_and_stores_nothing(ws_dir: Path, clock: FakeClock):
    adapter = FakeAdapter(clock, CandidateToolError("overloaded"), CandidateToolError("500"))
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)

    with pytest.raises(CandidateToolError, match="errored twice"):
        ws.run_automated_task(run, "T1.1")

    assert len(adapter.sent) == 2
    assert ws.pending_output(run, "T1.1") is None
    assert not (run.folder / "raw").exists()
    assert run.pending == ("T1.1",)


def test_a_private_task_is_refused_before_its_content_is_read_or_sent(
    ws_dir: Path, clock: FakeClock, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(private, "T2.1", track="Specs")
    configure_private_root(ws_dir, private)
    data = candidate(baseline=True)  # cleared for public and private
    write_candidate(ws_dir, CANDIDATE, data)
    adapter = FakeAdapter(clock, reply())
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)
    # A later Gate review narrows the Clearance, which applies to the open Run.
    ws.record_gate(CANDIDATE, reviewer="R", on=DAY, checklist=CHECKLIST, passed=True,
                   clearance="public_only")
    (private / "tasks" / "T2.1" / "input.md").unlink()  # reading it would fail loudly

    with pytest.raises(ClearanceError):
        ws.run_automated_task(run, "T2.1")

    assert adapter.sent == []


def test_a_private_tasks_raw_output_goes_to_its_private_root(
    ws_dir: Path, clock: FakeClock, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(private, "T2.1", track="Specs")
    configure_private_root(ws_dir, private)
    write_candidate(ws_dir, CANDIDATE, candidate(baseline=True))
    ws = open_ws(ws_dir, clock, FakeAdapter(clock, reply("private draft")))
    run = ws.start_run(CANDIDATE, on=DAY)

    ws.run_automated_task(run, "T2.1")

    assert (private / "runs" / run.id / "raw" / "T2.1.md").read_text() == "private draft"
    assert not (run.folder / "raw").exists()
    numbers = (run.folder / "pending" / "T2.1.yaml").read_text()
    assert "private draft" not in numbers
    assert ws.pending_output(run, "T2.1").raw == "private draft"


def test_a_task_already_pending_its_edit_is_not_sent_again(ws_dir: Path, clock: FakeClock):
    adapter = FakeAdapter(clock, reply(), reply())
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)
    ws.run_automated_task(run, "T1.1")

    with pytest.raises(AttemptError, match="awaiting its edit"):
        ws.run_automated_task(ws.resume_run(run.id), "T1.1")

    assert len(adapter.sent) == 1


def test_a_manual_assisted_run_cannot_be_sent_automatically(
    ws_dir: Path, clock: FakeClock
):
    data = candidate(adapter="manual_assisted", env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(ws_dir, CANDIDATE, data)
    adapter = FakeAdapter(clock, reply())
    ws = open_ws(ws_dir, clock, adapter)
    run = ws.start_run(CANDIDATE, on=DAY)

    with pytest.raises(AttemptError, match="manual_assisted"):
        ws.run_automated_task(run, "T1.1")

    assert adapter.sent == []


def test_a_missing_api_key_fails_naming_the_variable(
    ws_dir: Path, clock: FakeClock, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ws = Workspace.open(ws_dir, clock=clock)  # the real provider Adapters
    run = ws.start_run(CANDIDATE, on=DAY)

    with pytest.raises(MissingSecret, match="ANTHROPIC_API_KEY is not set"):
        ws.run_automated_task(run, "T1.1")

    assert ws.pending_output(run, "T1.1") is None
