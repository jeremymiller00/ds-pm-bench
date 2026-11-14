import datetime
import json
from pathlib import Path

import pytest
import yaml

from bench import (
    CalibrationError,
    CertificationFailed,
    JudgeClearanceError,
    JudgeNotCertified,
    Workspace,
)
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import (
    CANDIDATE,
    DAY,
    OUTPUT,
    FakeJudges,
    private_run,
    recorded_run,
    write_judge_setup,
)
from test_workspace_tasks import configure_private_root, write_task


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """One public Task, a manual-assisted Baseline cleared for it, and a Judge Setup."""
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted",
                     env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    write_judge_setup(workspace_dir)
    return workspace_dir


@pytest.fixture
def private_ws(ws_dir: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Adds a Private Task T3.1 and a Candidate cleared for it (cleared@1)."""
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize", input_text="Real Q3 roadmap for Acme.")
    configure_private_root(ws_dir, private)
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted", env_vars=[])
    write_candidate(ws_dir, "cleared@1", data)
    return ws_dir, private


def test_an_output_of_a_finished_run_joins_the_calibration_set_blind(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)

    item = ws.add_calibration_item(run.id, "T1.1")

    [blind] = ws.unscored_calibration_items()
    assert blind.id == item.id
    assert blind.output == OUTPUT
    assert "Churn rose 4% last quarter" in blind.input
    assert blind.gold_answer.startswith("Problem: predict SMB churn")
    assert [c.id for c in blind.criteria][-1] == "names_the_decision"
    seen = repr(blind)
    for identity in ("chatbot", "web-1", CANDIDATE, run.id, "draft"):
        assert identity not in seen
    assert (ws_dir / "calibration" / f"{item.id}.yaml").is_file()


def test_a_private_tasks_item_keeps_its_content_in_the_private_root(private_ws):
    ws_dir, private = private_ws
    ws = Workspace.open(ws_dir)
    run = private_run(ws)

    item = ws.add_calibration_item(run.id, "T3.1")

    public = (ws_dir / "calibration" / f"{item.id}.yaml").read_text()
    for content in ("final T3.1", "Real Q3 roadmap", "Gold", str(private)):
        assert content not in public
    assert (private / "calibration" / "content" / f"{item.id}.yaml").is_file()
    assert not (ws_dir / "calibration" / "content").exists()
    assert item.visibility == "private"
    [blind] = ws.unscored_calibration_items()
    assert blind.output == "final T3.1"
    assert "Real Q3 roadmap for Acme." in blind.input


def test_an_unfinished_run_or_an_output_already_in_the_set_is_refused(ws_dir: Path):
    write_task(ws_dir, "T1.2")
    ws = Workspace.open(ws_dir)
    run = recorded_run(ws)  # T1.2 still pending
    with pytest.raises(CalibrationError, match="not finished"):
        ws.add_calibration_item(run.id, "T1.1")
    run.record_output("T1.2", raw="d", final="f", active_seconds=1, latency_seconds=1,
                      cost_usd=None)
    ws.add_calibration_item(run.id, "T1.1")

    with pytest.raises(CalibrationError, match="already"):
        ws.add_calibration_item(run.id, "T1.1")

    assert [i.task_id for i in ws.calibration_items()] == ["T1.1"]


CRITERIA = ("correctness", "completeness", "clarity", "pm_judgment", "format_fit",
            "names_the_decision")


def scores(**overrides: int) -> dict[str, int]:
    return {c: overrides.get(c, 4) for c in CRITERIA}


def test_a_human_scores_an_item_blind_and_it_leaves_the_unscored_queue(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    item = ws.add_calibration_item(recorded_run(ws).id, "T1.1")

    scored = ws.score_calibration_item(item.id, scores(clarity=2))

    assert scored.human_scores == scores(clarity=2)
    assert ws.unscored_calibration_items() == ()
    assert ws.calibration_items()[0].human_scores["clarity"] == 2


@pytest.mark.parametrize("bad, match", [
    ({c: 4 for c in CRITERIA[:-1]}, "missing"),
    ({**scores(), "style": 3}, "unknown"),
    (scores(clarity=6), "clarity"),
    (scores(clarity=3.5), "whole number"),
])
def test_human_scores_must_be_whole_1_to_5_for_exactly_its_criteria(ws_dir, bad, match):
    ws = Workspace.open(ws_dir)
    item = ws.add_calibration_item(recorded_run(ws).id, "T1.1")

    with pytest.raises(CalibrationError, match=match):
        ws.score_calibration_item(item.id, bad)

    assert not ws.calibration_items()[0].human_scored


def test_a_spot_checked_output_can_join_the_calibration_set(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)
    ws.judge_run(run, allow_uncertified=True)
    run.record_spot_check("T1.1", "clarity", 2)

    item = ws.add_calibration_item(run.id, "T1.1")

    assert [b.id for b in ws.unscored_calibration_items()] == [item.id]


class ScriptedJudge:
    """A JudgeModel scoring each Output by a script keyed on the Output's text."""

    def __init__(self, by_output: dict[str, dict[str, int]]):
        self.by_output = by_output
        self.requests = []

    def judge(self, request) -> str:
        self.requests.append(request)
        given = self.by_output.get(request.output, {})
        return json.dumps({"scores": {
            c.id: {"score": given.get(c.id, 4), "reason": "ok."} for c in request.criteria
        }})


def calibration_set(ws: Workspace, human: list[dict], judged: list[dict]) -> list[str]:
    """One item per Run, Run i's Output being 'output i'; scores the human's side."""
    item_ids = []
    for i, human_scores in enumerate(human):
        run = ws.start_run(CANDIDATE, on=DAY + datetime.timedelta(days=i))
        run.record_output("T1.1", raw="draft", final=f"output {i}", active_seconds=1,
                          latency_seconds=1, cost_usd=None)
        item = ws.add_calibration_item(run.id, "T1.1")
        ws.score_calibration_item(item.id, scores(**human_scores))
        item_ids.append(item.id)
    assert len(judged) == len(human)
    return item_ids


def judges_for(judged: list[dict]) -> FakeJudges:
    script = {f"output {i}": s for i, s in enumerate(judged)}
    return FakeJudges(**{"openai/gpt-6": ScriptedJudge(script)})


HUMAN = [{"clarity": 1, "correctness": 1}, {"clarity": 2, "correctness": 5},
         {"clarity": 3, "correctness": 1}, {"clarity": 4, "correctness": 5},
         {"clarity": 5, "correctness": 3}]
JUDGED = [{"clarity": 1, "correctness": 5}, {"clarity": 2, "correctness": 1},
          {"clarity": 3, "correctness": 5}, {"clarity": 4, "correctness": 1},
          {"clarity": 4, "correctness": 3}]


def test_the_judge_scores_the_calibration_set_blind_to_the_human(ws_dir: Path):
    judges = judges_for(JUDGED)
    ws = Workspace.open(ws_dir, judges=judges)
    item_ids = calibration_set(ws, HUMAN, JUDGED)

    judged = ws.judge_calibration_set()

    assert [j.output_id for j in judged] == item_ids
    judge = judges.by_label["openai/gpt-6"]
    assert judge.requests[0].output == "output 0"
    assert CANDIDATE not in judge.requests[0].render()
    item = ws.calibration_items()[1]
    assert item.judge_scores[ws.judge_setup.hash]["correctness"] == 1
    assert ws.judge_calibration_set() == ()  # nothing left for this Judge Setup


def test_agreement_per_criterion_is_share_within_one_point_and_weighted_kappa(ws_dir):
    ws = Workspace.open(ws_dir, judges=judges_for(JUDGED))
    calibration_set(ws, HUMAN, JUDGED)
    ws.judge_calibration_set()

    agreement = ws.calibration_agreement()

    clarity = agreement.criteria["clarity"]
    assert (clarity.items, clarity.within_one_point) == (5, 1.0)
    assert clarity.weighted_kappa == pytest.approx(16 / 17)
    correctness = agreement.criteria["correctness"]
    assert correctness.within_one_point == pytest.approx(0.2)
    assert correctness.weighted_kappa == pytest.approx(-1.0)
    # every other Criterion: both always 4, so kappa is undefined
    assert agreement.criteria["format_fit"].within_one_point == 1.0
    assert agreement.criteria["format_fit"].weighted_kappa is None
    assert agreement.judge_setup_hash == ws.judge_setup.hash


def test_kappa_matches_a_worked_example_off_the_diagonal(ws_dir: Path):
    human = [{"clarity": s} for s in (2, 3, 3, 4, 5, 1)]
    judged = [{"clarity": s} for s in (3, 3, 2, 5, 5, 2)]
    ws = Workspace.open(ws_dir, judges=judges_for(judged))
    calibration_set(ws, human, judged)
    ws.judge_calibration_set()

    assert ws.calibration_agreement().criteria["clarity"].weighted_kappa == pytest.approx(0.8)


def test_a_private_item_is_refused_to_a_judge_without_private_clearance(private_ws):
    ws_dir, private = private_ws
    judges = FakeJudges()
    ws = Workspace.open(ws_dir, judges=judges)
    ws.add_calibration_item(private_run(ws).id, "T3.1")

    with pytest.raises(JudgeClearanceError, match="private"):
        ws.judge_calibration_set()
    assert judges.by_label == {}  # no judge model was even made

    write_judge_setup(ws_dir, clearance="public_and_private")
    ws = Workspace.open(ws_dir, judges=judges)
    [judging] = ws.judge_calibration_set()
    reasons = (private / "calibration" / "judge" / f"{judging.output_id}.yaml").read_text()
    assert "ok." in reasons
    assert "ok." not in (ws_dir / "calibration" / f"{judging.output_id}.yaml").read_text()


AGREEING = [{"clarity": s, "correctness": s} for s in (1, 2, 3, 4, 5)]
CLOSE = [{"clarity": s, "correctness": s} for s in (1, 2, 3, 4, 4)]


def certified_workspace(ws_dir: Path) -> Workspace:
    ws = Workspace.open(ws_dir, judges=judges_for(CLOSE))
    calibration_set(ws, AGREEING, CLOSE)
    ws.judge_calibration_set()
    ws.certify_judge(on=DAY)
    return ws


def test_a_judge_setup_meeting_the_thresholds_is_certified_and_logged(ws_dir: Path):
    ws = certified_workspace(ws_dir)

    assert ws.judge_certified
    [entry] = ws.decision_log()
    assert entry["kind"] == "judge_certification"
    assert entry["date"] == DAY.isoformat()
    assert entry["judge_setup_hash"] == ws.judge_setup.hash
    assert entry["items"] == [i.id for i in ws.calibration_items()]
    assert entry["within_one_point"] == 1.0
    assert entry["weighted_kappa"] >= 0.6
    assert entry["thresholds"] == {"within_one_point": 0.9, "weighted_kappa": 0.6}
    assert entry["criteria"]["clarity"]["weighted_kappa"] == pytest.approx(16 / 17)
    assert Workspace.open(ws_dir).judge_certified  # it is read back from the log


def test_a_judge_setup_below_the_thresholds_is_not_certified(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=judges_for(JUDGED))
    calibration_set(ws, HUMAN, JUDGED)
    ws.judge_calibration_set()

    with pytest.raises(CertificationFailed, match="within 1 point"):
        ws.certify_judge(on=DAY)

    assert not ws.judge_certified
    assert ws.decision_log() == ()


def test_the_thresholds_come_from_the_settings(ws_dir: Path):
    path = ws_dir / "config" / "settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["calibration"]["weighted_kappa"] = 0.99
    path.write_text(yaml.safe_dump(data))
    ws = Workspace.open(ws_dir, judges=judges_for(CLOSE))
    calibration_set(ws, AGREEING, CLOSE)
    ws.judge_calibration_set()

    with pytest.raises(CertificationFailed, match="kappa"):
        ws.certify_judge(on=DAY)


def test_certification_needs_every_item_scored_by_the_human_and_the_judge(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=judges_for(CLOSE))
    with pytest.raises(CertificationFailed, match="empty"):
        ws.certify_judge(on=DAY)
    calibration_set(ws, AGREEING, CLOSE)
    with pytest.raises(CertificationFailed, match="not scored by Judge Setup"):
        ws.certify_judge(on=DAY)
    ws.judge_calibration_set()
    run = ws.start_run(CANDIDATE, on=DAY + datetime.timedelta(days=30))
    run.record_output("T1.1", raw="d", final="late", active_seconds=1, latency_seconds=1,
                      cost_usd=None)
    ws.add_calibration_item(run.id, "T1.1")

    with pytest.raises(CertificationFailed, match="no human score"):
        ws.certify_judge(on=DAY)


def test_a_certified_judge_judges_a_run_without_an_override(ws_dir: Path):
    ws = certified_workspace(ws_dir)
    run = ws.start_run(CANDIDATE, on=DAY + datetime.timedelta(days=60))
    run.record_output("T1.1", raw="d", final=OUTPUT, active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    ws.judge_run(run)

    assert run.unjudged == ()
    assert [e["kind"] for e in ws.decision_log()] == ["judge_certification"]


def test_editing_the_judge_prompt_loses_certification(ws_dir: Path):
    certified_workspace(ws_dir)
    write_judge_setup(ws_dir, prompt="Score harshly.")
    ws = Workspace.open(ws_dir, judges=judges_for(CLOSE))
    run = ws.start_run(CANDIDATE, on=DAY + datetime.timedelta(days=60))
    run.record_output("T1.1", raw="d", final=OUTPUT, active_seconds=1, latency_seconds=1,
                      cost_usd=None)

    assert not ws.judge_certified
    assert ws.calibration_agreement().unjudged  # the new Judge Setup must re-score
    with pytest.raises(JudgeNotCertified, match="recalibrate"):
        ws.judge_run(run)
    ws.judge_run(run, allow_uncertified=True)  # the override path stays
    assert ws.decision_log()[-1]["kind"] == "uncertified_judge_override"
