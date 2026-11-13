import datetime
from pathlib import Path

import pytest

from bench import Workspace
from test_workspace_candidates import write_candidate
from test_workspace_runs import cleared
from test_workspace_tasks import write_task

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


def compare(ws_dir: Path, **challenger) -> object:
    ws = Workspace.open(ws_dir)
    baseline_run = complete_run(ws, BASELINE)
    challenger_run = complete_run(ws, CHALLENGER, **challenger)
    return ws.compare(challenger_run, baseline_run)


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
