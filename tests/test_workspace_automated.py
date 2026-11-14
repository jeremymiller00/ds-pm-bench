"""Running one Task of an automated Run through the Workspace, with fake Candidate Adapters."""

import datetime
from pathlib import Path

import pytest
import yaml

from bench import CandidateReply, CandidateToolError, Workspace
from test_workspace_attempts import FakeClock
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import write_task

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
