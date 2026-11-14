"""The Spot-check queue: which Tasks of a judged Run a human scores, and its effect on compare."""

import datetime
import itertools
import shutil
from pathlib import Path

import pytest

from bench import SpotChecksUnfinished, Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import FakeJudges, write_judge_setup
from test_workspace_tasks import write_task

DAY = datetime.date(2026, 11, 13)
TRACKS = ("Framing", "Specs", "Prioritize", "Story")
# 12 Tasks, 3 per Track, as in the catalog.
TASKS = {f"T{t}.{n}": track for t, track in enumerate(TRACKS, 1) for n in (1, 2, 3)}
CHALLENGER = "other@1"
BASELINE = "chatbot@web-1"


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    for task_id, track in TASKS.items():
        write_task(workspace_dir, task_id, track=track)
    for cid, tool, version, baseline in (
        (BASELINE, "chatbot", "web-1", True),
        (CHALLENGER, "other", "1", False),
    ):
        data = candidate(tool=tool, version=version, adapter="manual_assisted",
                         env_vars=[], baseline=baseline)
        data["gate"]["clearance"] = "public_only"
        write_candidate(workspace_dir, cid, data)
    write_judge_setup(workspace_dir)
    return workspace_dir


def judged_run(ws: Workspace, candidate_id: str = CHALLENGER, on: datetime.date = DAY):
    run = ws.start_run(candidate_id, on=on)
    for task_id in run.pending:
        run.record_output(task_id, raw="draft", final="final", active_seconds=60,
                          latency_seconds=5, cost_usd=None)
    ws.judge_run(run, allow_uncertified=True)
    return ws.resume_run(run.id)


def finish_spot_checks(run, score: float = 4) -> None:
    for task_id, criteria in run.spot_checks_left.items():
        for criterion in criteria:
            run.record_spot_check(task_id, criterion, score)


def test_a_judged_run_queues_the_configured_share_of_its_tasks(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())

    run = judged_run(ws)

    assert len(run.spot_check_queue) == 3  # spot_check_rate 0.25 of 12
    assert set(run.spot_check_queue) <= set(TASKS)


def test_the_queue_is_reproducible_from_the_run_id(ws_dir: Path, tmp_path: Path):
    other_dir = tmp_path / "again"
    shutil.copytree(ws_dir, other_dir)
    first = judged_run(Workspace.open(ws_dir, judges=FakeJudges()))
    second = judged_run(Workspace.open(other_dir, judges=FakeJudges()))

    assert first.id == second.id
    assert first.spot_check_queue == second.spot_check_queue


def test_judging_again_never_resamples_the_queue(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = judged_run(ws)
    queue = run.spot_check_queue

    ws.judge_run(run, allow_uncertified=True)

    assert ws.resume_run(run.id).spot_check_queue == queue


def test_every_track_is_checked_across_two_runs_of_the_same_candidate(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    runs = [judged_run(ws, on=DAY + datetime.timedelta(days=d)) for d in range(6)]

    for before, after in itertools.pairwise(runs):
        tracks = {TASKS[t] for t in before.spot_check_queue + after.spot_check_queue}
        assert tracks == set(TRACKS), (before.id, after.id)


def test_another_candidates_run_does_not_count_toward_track_coverage(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    first = judged_run(ws, CHALLENGER, DAY)
    judged_run(ws, BASELINE, DAY + datetime.timedelta(days=1))
    later = judged_run(ws, CHALLENGER, DAY + datetime.timedelta(days=2))

    tracks = {TASKS[t] for t in first.spot_check_queue + later.spot_check_queue}
    assert tracks == set(TRACKS)


def test_a_same_family_candidate_gets_every_task_queued(ws_dir: Path):
    write_judge_setup(ws_dir, models=[
        {"provider": "openai", "model": "gpt-6", "family": ["other"]},
    ])
    ws = Workspace.open(ws_dir, judges=FakeJudges())

    run = judged_run(ws)

    assert run.spot_check_queue == tuple(TASKS)


def test_the_queue_shows_what_is_left_until_every_criterion_is_scored(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = judged_run(ws)
    criteria = tuple(c.id for c in run.task(run.spot_check_queue[0]).rubric.criteria)
    assert run.spot_checks_left == {t: criteria for t in run.spot_check_queue}

    first = run.spot_check_queue[0]
    run.record_spot_check(first, criteria[0], 3)
    assert run.spot_checks_left[first] == criteria[1:]

    finish_spot_checks(run)
    assert run.spot_checks_left == {}


def compare_runs(ws: Workspace):
    baseline = judged_run(ws, BASELINE)
    challenger = judged_run(ws, CHALLENGER)
    return challenger, baseline


def test_a_comparison_refuses_a_verdict_while_spot_checks_are_unfinished(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    challenger, baseline = compare_runs(ws)
    finish_spot_checks(baseline)

    with pytest.raises(SpotChecksUnfinished, match=challenger.id):
        ws.compare(challenger.id, baseline.id)

    finish_spot_checks(challenger)
    assert ws.compare(challenger.id, baseline.id).verdict.outcome == "adopt"


def test_finished_spot_checks_more_than_a_point_from_the_judge_still_re_test(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())  # the Judge scores 4 throughout
    challenger, baseline = compare_runs(ws)
    finish_spot_checks(baseline)
    finish_spot_checks(challenger, score=2)

    verdict = ws.compare(challenger.id, baseline.id).verdict

    assert verdict.outcome == "re-test"
    assert verdict.rationale[0].startswith("Spot-check:")


def test_a_spot_check_shows_the_output_blind_with_the_criteria_left(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = judged_run(ws)
    task_id = run.spot_check_queue[0]
    run.record_spot_check(task_id, "clarity", 4)

    blind = ws.spot_check_item(run, task_id)

    assert blind.id == task_id
    assert blind.output == "final"
    assert "Churn rose 4% last quarter" in blind.input
    assert blind.gold_answer.startswith("Problem: predict SMB churn")
    assert [c.id for c in blind.criteria] == list(run.spot_checks_left[task_id])
    assert "clarity" not in [c.id for c in blind.criteria]
