"""The Spot-check queue: which Tasks of a judged Run a human scores, and its effect on compare."""

import datetime
from pathlib import Path

import pytest

from bench import Workspace
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
