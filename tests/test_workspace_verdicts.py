import datetime
from pathlib import Path

import pytest
import yaml

from bench import SuiteMismatch, Workspace
from test_workspace_candidates import write_candidate
from test_workspace_runs import cleared
from test_workspace_tasks import configure_private_root, write_task

DAY = datetime.date(2026, 11, 13)
BASELINE = "claude@opus-5-5"
CHALLENGER = "other@1"


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """Two public Tasks in two Tracks, a Baseline and a challenger, both public only."""
    write_task(workspace_dir, "T1.1", track="Framing")
    write_task(workspace_dir, "T2.1", track="Specs")
    write_candidate(workspace_dir, BASELINE, cleared("public_only", baseline=True))
    write_candidate(workspace_dir, CHALLENGER, cleared("public_only", version="other-1"))
    return workspace_dir


def complete_run(
    ws: Workspace,
    candidate_id: str,
    *,
    on: datetime.date = DAY,
    quality: dict[str, float] | None = None,
    active_seconds: float = 600,
) -> str:
    """Start and finish a Run; every Criterion of a Task gets that Task's quality score."""
    run = ws.start_run(candidate_id, on=on)
    for task in run.tasks:
        run.record_output(
            task.id,
            raw="one two three four five",
            final="one two three four six",
            active_seconds=active_seconds,
            latency_seconds=12,
            cost_usd=0.03,
        )
        q = (quality or {}).get(task.id, 4)
        run.record_criterion_scores(task.id, {c.id: q for c in task.rubric.criteria})
    return run.id


def compare(ws_dir: Path, *, spot_check_disagreement: float | None = None, **challenger):
    ws = Workspace.open(ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER, **challenger)
    return ws.compare(
        challenger_run, baseline_run, spot_check_disagreement=spot_check_disagreement
    )


def test_a_challenger_matching_the_baseline_is_adopted(ws_dir: Path):
    comparison = compare(ws_dir)

    assert comparison.verdict.outcome == "adopt"
    assert comparison.challenger_score.weighted == comparison.baseline_score.weighted


def test_a_challenger_well_below_the_baseline_is_rejected(ws_dir: Path):
    # Same Quality, but no time saved: Time saved drops from 4 to 1, weighted by 0.6.
    comparison = compare(ws_dir, active_seconds=2700)

    assert comparison.verdict.outcome == "reject"
    assert comparison.baseline_score.weighted - comparison.challenger_score.weighted == (
        pytest.approx(0.6)
    )


def test_a_challenger_within_the_retest_margin_below_the_baseline_is_retested(ws_dir: Path):
    # Quality 3.8 vs 4 on both Tracks: weighted 0.1 below, inside the 0.2 margin.
    comparison = compare(ws_dir, quality={"T1.1": 3.8, "T2.1": 3.8})

    assert comparison.verdict.outcome == "re-test"
    assert any("re-test margin" in r for r in comparison.verdict.rationale)


def test_a_track_regression_beyond_the_margin_rejects_a_higher_scoring_challenger(
    ws_dir: Path,
):
    # Framing 5 lifts the weighted total, but Specs 3.4 is 0.6 below the Baseline's 4.
    comparison = compare(ws_dir, quality={"T1.1": 5, "T2.1": 3.4})

    assert comparison.challenger_score.weighted > comparison.baseline_score.weighted
    assert comparison.verdict.outcome == "reject"
    assert comparison.verdict.regressions == ("Specs",)
    assert any("Regression" in r and "Specs" in r for r in comparison.verdict.rationale)


def test_a_track_drop_within_the_regression_margin_is_not_a_regression(ws_dir: Path):
    comparison = compare(ws_dir, quality={"T1.1": 5, "T2.1": 3.5})

    assert comparison.verdict.outcome == "adopt"
    assert comparison.verdict.regressions == ()


def test_a_spot_check_disagreement_over_one_point_forces_a_retest(ws_dir: Path):
    comparison = compare(ws_dir, spot_check_disagreement=1.5)

    assert comparison.verdict.outcome == "re-test"
    assert any("Spot-check" in r for r in comparison.verdict.rationale)


def test_a_spot_check_disagreement_of_one_point_is_tolerated(ws_dir: Path):
    assert compare(ws_dir, spot_check_disagreement=1).verdict.outcome == "adopt"


@pytest.fixture
def private_ws_dir(ws_dir: Path, tmp_path: Path) -> Path:
    """ws_dir plus a Private Task, with both Candidates cleared for it."""
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize")
    configure_private_root(ws_dir, private)
    write_candidate(ws_dir, BASELINE, cleared("public_and_private", baseline=True))
    write_candidate(ws_dir, CHALLENGER, cleared("public_and_private", version="other-1"))
    return ws_dir


def test_an_adopt_over_a_public_and_private_suite_is_a_full_adopt(private_ws_dir: Path):
    verdict = compare(private_ws_dir).verdict

    assert (verdict.outcome, verdict.suite, verdict.full_adopt) == (
        "adopt",
        "public_and_private",
        True,
    )
    assert verdict.label == "adopt"
    assert verdict.private_tasks == 1


def test_a_public_only_suite_gives_a_labelled_verdict_that_is_not_a_full_adopt(
    ws_dir: Path,
):
    verdict = compare(ws_dir).verdict

    assert (verdict.outcome, verdict.suite, verdict.full_adopt) == (
        "adopt",
        "public_only",
        False,
    )
    assert verdict.label == "adopt (public-only Suite, not a full adopt)"
    assert verdict.private_tasks == 0


def test_a_public_only_reject_is_labelled_too(ws_dir: Path):
    verdict = compare(ws_dir, active_seconds=2700).verdict

    assert verdict.label == "reject (public-only Suite)"


def test_runs_over_different_suites_cannot_be_compared(private_ws_dir: Path):
    write_candidate(private_ws_dir, CHALLENGER, cleared("public_only", version="other-1"))
    ws = Workspace.open(private_ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER)

    with pytest.raises(SuiteMismatch, match="T3.1"):
        ws.compare(challenger_run, baseline_run)


def test_runs_more_than_a_week_apart_are_compared_with_a_warning(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER, on=DAY + datetime.timedelta(days=8))

    comparison = ws.compare(challenger_run, baseline_run)

    assert comparison.warnings == ("the Runs are 8 days apart, more than a week",)
    assert comparison.verdict.outcome == "adopt"


def test_runs_a_week_apart_get_no_warning(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER, on=DAY - datetime.timedelta(days=7))

    assert ws.compare(challenger_run, baseline_run).warnings == ()


def test_the_baseline_run_must_be_a_run_of_the_baseline(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER)

    with pytest.raises(ValueError, match="not the Baseline"):
        ws.compare(baseline_run, challenger_run)


@pytest.mark.parametrize(
    "gate", [None, {"result": "fail", "clearance": None}], ids=["no-gate", "failed-gate"]
)
def test_a_candidate_whose_gate_has_not_passed_is_rejected_without_runs(ws_dir: Path, gate):
    data = cleared("public_and_private", version="bad-1")
    data["gate"] = None if gate is None else {**data["gate"], **gate}
    write_candidate(ws_dir, "bad@1", data)

    verdict = Workspace.open(ws_dir).gate_verdict("bad@1")

    assert verdict.outcome == "reject"
    assert (verdict.candidate_version, verdict.baseline_id) == ("bad-1", BASELINE)
    assert verdict.suite is None and verdict.challenger_run_id is None
    assert any("Gate" in r for r in verdict.rationale)
    assert not (ws_dir / "runs").exists()


def test_a_gate_verdict_is_refused_for_a_candidate_whose_gate_passed(ws_dir: Path):
    with pytest.raises(ValueError, match="passed"):
        Workspace.open(ws_dir).gate_verdict(CHALLENGER)


def test_a_comparisons_verdict_is_logged_with_its_rationale(private_ws_dir: Path):
    ws = Workspace.open(private_ws_dir)
    verdict = compare(private_ws_dir).verdict

    path = ws.log_verdict(verdict, on=DAY)

    assert path == private_ws_dir / "decisions" / "0001-2026-11-13-other@1.yaml"
    entry = yaml.safe_load(path.read_text())
    assert entry == {
        "date": "2026-11-13",
        "candidate": {"id": "other@1", "version": "other-1"},
        "baseline": {"id": "claude@opus-5-5", "version": "claude-opus-5-5"},
        "verdict": "adopt",
        "label": "adopt",
        "full_adopt": True,
        "suite": "public_and_private",
        "private_tasks": 1,
        "judge_setup_hash": None,
        "rationale": list(verdict.rationale),
        "regressions": [],
        "warnings": [],
        "runs": {
            "challenger": "2026-11-13-other@1",
            "baseline": "2026-11-13-claude@opus-5-5",
        },
    }
    assert ws.decision_log() == (entry,)


def test_a_public_only_entry_keeps_its_label_and_judge_setup_hash(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    verdict = compare(ws_dir, quality={"T1.1": 5, "T2.1": 3.4}).verdict

    entry = yaml.safe_load(ws.log_verdict(verdict, on=DAY, judge_setup_hash="abc123").read_text())

    assert (entry["verdict"], entry["label"], entry["full_adopt"]) == (
        "reject",
        "reject (public-only Suite)",
        False,
    )
    assert (entry["private_tasks"], entry["judge_setup_hash"]) == (0, "abc123")
    assert entry["regressions"] == ["Specs"]


def test_a_gate_failure_verdict_is_logged_without_runs(ws_dir: Path):
    data = cleared("public_only", version="bad-1")
    data["gate"]["result"] = "fail"
    write_candidate(ws_dir, "bad@1", data)
    ws = Workspace.open(ws_dir)

    entry = yaml.safe_load(ws.log_verdict(ws.gate_verdict("bad@1"), on=DAY).read_text())

    assert (entry["verdict"], entry["suite"], entry["private_tasks"]) == ("reject", None, 0)
    assert entry["runs"] == {"challenger": None, "baseline": None}


def test_the_decision_log_is_append_only(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER)
    first = ws.log_verdict(ws.compare(challenger_run, baseline_run).verdict, on=DAY)
    first_text = first.read_text()

    retest = ws.compare(challenger_run, baseline_run, spot_check_disagreement=2)
    second = ws.log_verdict(retest.verdict, on=DAY)

    assert first.read_text() == first_text
    assert second == ws_dir / "decisions" / "0002-2026-11-13-other@1.yaml"
    assert [e["verdict"] for e in Workspace.open(ws_dir).decision_log()] == [
        "adopt",
        "re-test",
    ]
