import datetime
import json
from pathlib import Path

import pytest
import yaml

from bench import (
    ClearanceError,
    ConfigError,
    JudgeNotCertified,
    JudgeRefused,
    JudgeReplyError,
    JudgeSetupMismatch,
    JudgeUnavailable,
    JudgingError,
    RunNotFinished,
    Workspace,
)
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import configure_private_root, write_task

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


def test_the_judge_sees_input_rubric_gold_and_final_but_never_the_candidate(ws_dir: Path):
    judge = FakeJudge()
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": judge}))
    run = recorded_run(ws)

    ws.judge_run(run, allow_uncertified=True)

    [request] = judge.requests
    assert "Churn rose 4% last quarter" in request.input
    assert "Frame this business problem" in request.input
    assert request.gold_answer.startswith("Problem: predict SMB churn")
    assert request.output == OUTPUT
    assert [c.id for c in request.criteria][-1] == "names_the_decision"
    seen = request.render() + repr(request)
    for identity in ("chatbot", "web-1", CANDIDATE, "draft"):
        assert identity not in seen


PANEL = [{"provider": "openai", "model": "gpt-6"}, {"provider": "google", "model": "gemini-4"},
         {"provider": "openai", "model": "gpt-6-mini"}]


def test_a_panel_scores_the_median_and_flags_criteria_more_than_a_point_apart(ws_dir: Path):
    write_judge_setup(ws_dir, mode="panel", models=PANEL)
    judges = FakeJudges(**{
        "openai/gpt-6": FakeJudge(scores={"clarity": 5, "correctness": 2}),
        "google/gemini-4": FakeJudge(scores={"clarity": 2, "correctness": 3}),
        "openai/gpt-6-mini": FakeJudge(scores={"clarity": 3, "correctness": 3}),
    })
    ws = Workspace.open(ws_dir, judges=judges)
    run = recorded_run(ws)

    [judging] = ws.judge_run(run, allow_uncertified=True)

    record = results(run)
    assert record["criterion_scores"]["clarity"] == 3
    assert record["criterion_scores"]["correctness"] == 3
    assert record["judge"]["disagreements"] == ["clarity"]
    assert record["judge"]["scores"]["openai/gpt-6"]["clarity"] == 5
    assert record["judge"]["scores"]["google/gemini-4"]["clarity"] == 2
    assert record["judge"]["full_human_check"] is True
    assert judging.disagreements == ("clarity",)
    reasons = ws.resume_run(run.id).judge_reasons("T1.1")
    assert reasons["openai/gpt-6-mini"]["clarity"] == {"score": 3, "reason": "Clarity ok."}
    assert run.full_human_checks == ("T1.1",)


def test_a_panel_within_a_point_raises_no_flag(ws_dir: Path):
    write_judge_setup(ws_dir, mode="panel", models=PANEL[:2])
    judges = FakeJudges(**{"openai/gpt-6": FakeJudge(score=4),
                           "google/gemini-4": FakeJudge(score=3)})
    ws = Workspace.open(ws_dir, judges=judges)
    run = recorded_run(ws)

    ws.judge_run(run, allow_uncertified=True)

    record = results(run)
    assert record["criterion_scores"]["clarity"] == 3.5
    assert record["judge"]["disagreements"] == []
    assert run.full_human_checks == ()


def test_an_uncertified_judge_is_refused_without_an_override(ws_dir: Path):
    judge = FakeJudge()
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": judge}))
    run = recorded_run(ws)

    with pytest.raises(JudgeNotCertified, match="allow_uncertified"):
        ws.judge_run(run)

    assert judge.requests == []
    assert run.unjudged == ("T1.1",)
    assert ws.decision_log() == ()


def test_the_uncertified_override_is_logged_once_with_the_judge_setup_hash(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)

    ws.judge_run(run, allow_uncertified=True)
    ws.judge_run(run, allow_uncertified=True)  # nothing left to judge

    entries = ws.decision_log()
    assert len(entries) == 1
    assert entries[0] == {
        "kind": "uncertified_judge_override",
        "date": DAY.isoformat(),
        "run": run.id,
        "candidate": {"id": CANDIDATE},
        "judge_setup_hash": ws.judge_setup.hash,
    }


@pytest.mark.parametrize("model", ["claude-sonnet-5", "Claude Sonnet 5", "CLAUDE-SONNET-5"])
def test_a_judge_model_that_is_the_model_the_candidate_calls_is_refused(ws_dir: Path, model):
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted",
                     env_vars=[], baseline=True,
                     settings={"provider": "anthropic", "model": "claude-sonnet-5"})
    data["gate"]["clearance"] = "public_only"
    write_candidate(ws_dir, CANDIDATE, data)
    write_judge_setup(ws_dir, mode="panel", models=[
        PANEL[0], {"provider": "anthropic", "model": model},
    ])
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)

    with pytest.raises(JudgeRefused, match="anthropic/"):
        ws.judge_run(run, allow_uncertified=True)

    assert run.unjudged == ("T1.1",)
    assert ws.decision_log() == ()


def test_the_claude_baseline_cannot_be_judged_by_the_same_claude_model(workspace_dir: Path):
    write_task(workspace_dir, "T1.1")
    data = candidate(baseline=True)  # tool Claude, version claude-opus-5-5
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "claude@claude-opus-5-5", data)
    write_judge_setup(workspace_dir, models=[
        {"provider": "anthropic", "model": "claude-opus-5-5"},
    ])
    ws = Workspace.open(workspace_dir, judges=FakeJudges())
    run = ws.start_run("claude@claude-opus-5-5", on=DAY)
    run.record_output("T1.1", raw="x", final="y", active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    with pytest.raises(JudgeRefused, match="anthropic/claude-opus-5-5"):
        ws.judge_run(run, allow_uncertified=True)


def test_self_judging_compares_the_model_the_candidate_calls_not_its_version(
    workspace_dir: Path,
):
    write_task(workspace_dir, "T1.1")
    data = candidate(version="opus-latest", baseline=True,
                     settings={"model": "claude-opus-5-5"})  # tool Claude
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "claude@opus-latest", data)
    write_judge_setup(workspace_dir, models=[
        {"provider": "anthropic", "model": "claude-opus-5-5"},
    ])
    ws = Workspace.open(workspace_dir, judges=FakeJudges())
    run = ws.start_run("claude@opus-latest", on=DAY)
    run.record_output("T1.1", raw="x", final="y", active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    with pytest.raises(JudgeRefused, match="anthropic/claude-opus-5-5"):
        ws.judge_run(run, allow_uncertified=True)


def test_the_same_model_id_from_another_provider_is_not_self_judging(workspace_dir: Path):
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="OpenAI", version="shared-1", baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "openai@shared-1", data)
    write_judge_setup(workspace_dir, models=[{"provider": "anthropic", "model": "shared-1"}])
    ws = Workspace.open(workspace_dir, judges=FakeJudges())
    run = ws.start_run("openai@shared-1", on=DAY)
    run.record_output("T1.1", raw="x", final="y", active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    [judging] = ws.judge_run(run, allow_uncertified=True)

    assert not judging.same_family


def test_a_same_family_candidate_is_flagged_for_a_full_human_check(ws_dir: Path):
    write_judge_setup(ws_dir, models=[
        {"provider": "openai", "model": "web-2", "family": ["chatbot"]},
    ])
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)

    [judging] = ws.judge_run(run, allow_uncertified=True)

    assert judging.same_family and judging.full_human_check
    record = results(run)["judge"]
    assert record["same_family"] is True and record["full_human_check"] is True
    assert ws.resume_run(run.id).full_human_checks == ("T1.1",)


def test_an_openai_judge_flags_an_openai_candidate_by_default(workspace_dir: Path):
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="OpenAI", version="gpt-5", baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "openai@gpt-5", data)
    write_judge_setup(workspace_dir)  # openai/gpt-6
    ws = Workspace.open(workspace_dir, judges=FakeJudges())
    run = ws.start_run("openai@gpt-5", on=DAY)
    run.record_output("T1.1", raw="x", final="y", active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    [judging] = ws.judge_run(run, allow_uncertified=True)

    assert judging.same_family


@pytest.fixture
def private_ws(ws_dir: Path, tmp_path: Path) -> tuple[Path, Path]:
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize", input_text="Real Q3 roadmap for Acme.")
    configure_private_root(ws_dir, private)
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted", env_vars=[])
    write_candidate(ws_dir, "cleared@1", data)  # public_and_private
    return ws_dir, private


def private_run(ws: Workspace):
    run = ws.start_run("cleared@1", on=DAY)
    for task_id in ("T1.1", "T3.1"):
        run.record_output(task_id, raw="draft", final=f"final {task_id}", active_seconds=1,
                          latency_seconds=1, cost_usd=None)
    return run


def test_a_private_task_is_refused_to_a_judge_without_private_clearance(private_ws):
    ws_dir, private = private_ws
    judge = FakeJudge()
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": judge}))
    run = private_run(ws)
    (private / "tasks" / "T3.1" / "input.md").chmod(0)  # any read would raise
    try:
        with pytest.raises(ClearanceError, match="T3.1"):
            ws.judge_run(run, allow_uncertified=True)
    finally:
        (private / "tasks" / "T3.1" / "input.md").chmod(0o644)

    assert judge.requests == []
    assert run.unjudged == ("T1.1", "T3.1")


def test_a_privately_cleared_judge_keeps_private_reasons_in_the_private_root(private_ws):
    ws_dir, private = private_ws
    write_judge_setup(ws_dir, clearance="public_and_private")
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = private_run(ws)

    ws.judge_run(run, allow_uncertified=True)

    assert run.unjudged == ()
    assert (private / "runs" / run.id / "judge" / "T3.1.yaml").is_file()
    assert not (run.folder / "judge" / "T3.1.yaml").exists()
    assert (run.folder / "judge" / "T1.1.yaml").is_file()
    public_record = (run.folder / "results" / "T3.1.yaml").read_text()
    assert "ok." not in public_record  # numbers only in the public repo
    assert ws.resume_run(run.id).judge_reasons("T3.1")["openai/gpt-6"]["clarity"]["score"] == 4


def test_editing_the_judge_prompt_changes_the_judge_setup_hash(ws_dir: Path):
    before = Workspace.open(ws_dir).judge_setup.hash

    (ws_dir / "config" / "judge" / "prompt.md").write_text("Score strictly.")
    after = Workspace.open(ws_dir).judge_setup.hash

    assert before != after
    write_judge_setup(ws_dir)
    assert Workspace.open(ws_dir).judge_setup.hash == before


def test_changing_a_judge_model_changes_the_hash_but_clearance_does_not(ws_dir: Path):
    before = Workspace.open(ws_dir).judge_setup.hash

    write_judge_setup(ws_dir, clearance="public_and_private")
    assert Workspace.open(ws_dir).judge_setup.hash == before
    write_judge_setup(ws_dir, models=[{"provider": "openai", "model": "gpt-6-mini"}])
    assert Workspace.open(ws_dir).judge_setup.hash != before


def test_a_run_judged_by_one_judge_setup_refuses_another(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)
    ws.judge_run(run, allow_uncertified=True)
    (ws_dir / "config" / "judge" / "prompt.md").write_text("Score strictly.")

    judge = FakeJudge()
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": judge}))

    with pytest.raises(JudgeSetupMismatch, match="one Judge Setup"):
        ws.judge_run(run, allow_uncertified=True)

    assert judge.requests == []
    assert len(ws.decision_log()) == 1  # only the first, allowed, override


@pytest.mark.parametrize(
    "reply, match",
    [
        ("not json", "not JSON"),
        ('{"other": 1}', "no 'scores'"),
        ('{"scores": {"clarity": {"score": 3, "reason": "ok"}}}', "missing"),
        (None, "must be 1-5"),
        ("reason", "one-line reason"),
    ],
)
def test_a_malformed_reply_is_rejected_naming_the_judge_and_task(ws_dir: Path, reply, match):
    if reply is None:
        judge = FakeJudge(scores={"clarity": 7})
    elif reply == "reason":
        judge = FakeJudge(reply=json.dumps({"scores": {
            c: {"score": 3, "reason": "two\nlines"} for c in
            ("correctness", "completeness", "clarity", "pm_judgment", "format_fit",
             "names_the_decision")
        }}))
    else:
        judge = FakeJudge(reply=reply)
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": judge}))
    run = recorded_run(ws)

    with pytest.raises(JudgeReplyError, match=match) as raised:
        ws.judge_run(run, allow_uncertified=True)

    assert "openai/gpt-6" in str(raised.value) and "T1.1" in str(raised.value)
    assert run.unjudged == ("T1.1",)


def test_a_reply_in_a_code_fence_is_accepted(ws_dir: Path):
    class FencedJudge(FakeJudge):
        def judge(self, request) -> str:
            return f"```json\n{super().judge(request)}\n```"

    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": FencedJudge()}))
    run = recorded_run(ws)

    ws.judge_run(run, allow_uncertified=True)

    assert run.unjudged == ()


def test_judging_without_a_judge_setup_is_unavailable(ws_dir: Path):
    run = recorded_run(Workspace.open(ws_dir, judges=FakeJudges()))
    (ws_dir / "config" / "judge.yaml").unlink()
    with pytest.raises(JudgeUnavailable, match="config/judge.yaml"):
        Workspace.open(ws_dir, judges=FakeJudges()).judge_run(run, allow_uncertified=True)


@pytest.mark.parametrize(
    "change, field",
    [
        ({"mode": "jury"}, "mode"),
        ({"clearance": "everything"}, "clearance"),
        ({"models": []}, "models"),
        ({"mode": "panel"}, "models"),
        ({"models": PANEL}, "models"),
        ({"models": [{"provider": "mistral", "model": "x"}]}, "models[0].provider"),
        ({"models": [{"provider": "openai"}]}, "models[0].model"),
        ({"models": [{"provider": "openai", "model": "x", "seed": 1}]}, "models[0].seed"),
        ({"mode": "panel", "models": [PANEL[0], PANEL[0]]}, "models[1]"),
        ({"prompt": "config/judge/missing.md"}, "prompt"),
        ({"extra": 1}, "(file).extra"),
    ],
)
def test_an_invalid_judge_setup_names_the_file_and_field(ws_dir: Path, change, field):
    path = ws_dir / "config" / "judge.yaml"
    path.write_text(yaml.safe_dump({**yaml.safe_load(path.read_text()), **change}))

    with pytest.raises(ConfigError) as raised:
        Workspace.open(ws_dir)

    assert (raised.value.file, raised.value.field) == ("config/judge.yaml", field)


def judged_comparison(ws_dir: Path, *, rejudge_challenger_with: str | None = None):
    data = candidate(tool="other", version="1", adapter="manual_assisted", env_vars=[])
    data["gate"]["clearance"] = "public_only"
    write_candidate(ws_dir, "other@1", data)
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    baseline = recorded_run(ws)
    ws.judge_run(baseline, allow_uncertified=True)
    if rejudge_challenger_with is not None:
        (ws_dir / "config" / "judge" / "prompt.md").write_text(rejudge_challenger_with)
        ws = Workspace.open(ws_dir, judges=FakeJudges())
    challenger = ws.start_run("other@1", on=DAY)
    challenger.record_output("T1.1", raw="draft", final=OUTPUT, active_seconds=60,
                             latency_seconds=5, cost_usd=None)
    ws.judge_run(challenger, allow_uncertified=True)
    return ws, ws.compare(challenger.id, baseline.id)


def test_judge_scores_feed_the_comparison_which_carries_the_judge_setup_hash(ws_dir: Path):
    ws, comparison = judged_comparison(ws_dir)

    assert comparison.challenger_score.dimensions["quality"] == 4
    assert comparison.judge_setup_hash == ws.judge_setup.hash
    assert not any("Judge Setup" in w for w in comparison.verdict.warnings)
    path = ws.log_verdict(comparison.verdict, judge_setup_hash=comparison.judge_setup_hash)
    assert yaml.safe_load(path.read_text())["judge_setup_hash"] == ws.judge_setup.hash


def test_runs_judged_by_different_judge_setups_warn_and_carry_no_single_hash(ws_dir: Path):
    _, comparison = judged_comparison(ws_dir, rejudge_challenger_with="Score strictly.")

    assert comparison.judge_setup_hash is None
    assert any("different Judge Setups" in w for w in comparison.verdict.warnings)


def test_a_run_with_pending_tasks_cannot_be_judged(ws_dir: Path):
    write_task(ws_dir, "T1.2")
    judge = FakeJudge()
    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": judge}))
    run = recorded_run(ws)  # T1.1 recorded, T1.2 still pending

    with pytest.raises(RunNotFinished, match="T1.2"):
        ws.judge_run(run, allow_uncertified=True)

    assert judge.requests == []
    assert run.unjudged == ("T1.1",)
    assert ws.decision_log() == ()


def test_a_judging_that_judges_no_task_logs_no_override(ws_dir: Path):
    broken = FakeJudges(**{"openai/gpt-6": FakeJudge(reply="not json")})
    ws = Workspace.open(ws_dir, judges=broken)
    run = recorded_run(ws)
    with pytest.raises(JudgeReplyError):
        ws.judge_run(run, allow_uncertified=True)

    assert ws.decision_log() == ()

    ws = Workspace.open(ws_dir, judges=FakeJudges())
    ws.judge_run(run, allow_uncertified=True)

    [entry] = ws.decision_log()
    assert entry["kind"] == "uncertified_judge_override"


def test_a_resumed_judging_does_not_log_the_override_again(ws_dir: Path):
    write_task(ws_dir, "T1.2")
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)
    run.record_output("T1.2", raw="x", final="y", active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    class FailsOnSecondTask(FakeJudge):
        def judge(self, request) -> str:
            if self.requests:
                self.requests.append(request)
                return "not json"
            return super().judge(request)

    ws = Workspace.open(ws_dir, judges=FakeJudges(**{"openai/gpt-6": FailsOnSecondTask()}))
    with pytest.raises(JudgeReplyError, match="T1.2"):
        ws.judge_run(run, allow_uncertified=True)
    assert len(ws.decision_log()) == 1  # logged once T1.1 was judged

    ws = Workspace.open(ws_dir, judges=FakeJudges())
    ws.judge_run(run, allow_uncertified=True)

    assert len(ws.decision_log()) == 1
    assert run.unjudged == ()


def test_without_judge_models_judging_is_unavailable(ws_dir: Path):
    ws = Workspace.open(ws_dir)  # no judges: the composition root was not used
    run = recorded_run(ws)

    with pytest.raises(JudgeUnavailable, match="without judge models"):
        ws.judge_run(run, allow_uncertified=True)

    assert ws.decision_log() == ()


@pytest.mark.parametrize("error", [
    RunNotFinished, JudgeUnavailable, JudgeNotCertified, JudgeRefused,
    JudgeReplyError, JudgeSetupMismatch,
])
def test_every_judging_refusal_is_a_judging_error(error):
    assert issubclass(error, JudgingError)


def test_a_private_task_refused_to_the_judge_is_a_judging_and_clearance_error():
    from bench import JudgeClearanceError

    assert issubclass(JudgeClearanceError, JudgingError)
    assert issubclass(JudgeClearanceError, ClearanceError)
