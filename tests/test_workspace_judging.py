import datetime
import json
from pathlib import Path

import pytest
import yaml

from bench import Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import write_task

DAY = datetime.date(2026, 11, 13)
CANDIDATE = "chatbot@web-1"
OUTPUT = "Frame it as 60-day SMB churn prediction."


class FakeJudge:
    """A JudgeModel that scores every Criterion it is asked about with scripted scores."""

    def __init__(self, score: int = 4, scores: dict | None = None, reply: str | None = None):
        self.score = score
        self.scores = scores or {}
        self.reply = reply
        self.requests = []

    def judge(self, request) -> str:
        self.requests.append(request)
        if self.reply is not None:
            return self.reply
        return json.dumps({
            "scores": {
                c.id: {"score": self.scores.get(c.id, self.score), "reason": f"{c.name} ok."}
                for c in request.criteria
            }
        })


class FakeJudges:
    """The factory Workspace.open receives: a FakeJudge per provider/model."""

    def __init__(self, **by_label: FakeJudge):
        self.by_label = by_label

    def __call__(self, spec) -> FakeJudge:
        return self.by_label.setdefault(spec.label, FakeJudge())


def write_judge_setup(
    root: Path,
    *,
    mode: str = "single",
    models: list | None = None,
    clearance: str = "public_only",
    prompt: str = "Score the Output against each Criterion.",
) -> None:
    (root / "config" / "judge").mkdir(parents=True, exist_ok=True)
    (root / "config" / "judge" / "prompt.md").write_text(prompt)
    (root / "config" / "judge.yaml").write_text(yaml.safe_dump({
        "mode": mode,
        "clearance": clearance,
        "prompt": "config/judge/prompt.md",
        "models": models or [{"provider": "openai", "model": "gpt-6"}],
    }))


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted",
                     env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    write_judge_setup(workspace_dir)
    return workspace_dir


def recorded_run(ws: Workspace):
    run = ws.start_run(CANDIDATE, on=DAY)
    run.record_output("T1.1", raw="draft", final=OUTPUT, active_seconds=60,
                      latency_seconds=5, cost_usd=None)
    return run


def results(run, task_id: str = "T1.1") -> dict:
    return yaml.safe_load((run.folder / "results" / f"{task_id}.yaml").read_text())


def test_a_single_judge_scores_every_criterion_of_a_recorded_output(ws_dir: Path):
    judges = FakeJudges(**{"openai/gpt-6": FakeJudge(scores={"clarity": 2})})
    ws = Workspace.open(ws_dir, judges=judges)
    run = recorded_run(ws)

    ws.judge_run(run, allow_uncertified=True)

    scores = results(run)["criterion_scores"]
    assert scores == {"correctness": 4, "completeness": 4, "clarity": 2, "pm_judgment": 4,
                      "format_fit": 4, "names_the_decision": 4}
    assert ws.resume_run(run.id).judge_setup_hash == ws.judge_setup.hash
