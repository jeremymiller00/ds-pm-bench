from pathlib import Path

import pytest

from bench import TaskResult, Workspace
from test_workspace_config import set_setting


def result(**overrides) -> TaskResult:
    """A per-Task result record with neutral defaults; override the field under test."""
    fields = dict(
        task_id="T1.1",
        track="Framing",
        criterion_scores={"correctness": 3, "completeness": 3},
        edit_distance=0.20,
        active_seconds=1800,
        manual_minutes=60,
        cost_usd=0.10,
        latency_seconds=45,
    )
    fields.update(overrides)
    return TaskResult(**fields)


# Default band: better lower, thresholds [0.05, 0.15, 0.30, 0.50].
@pytest.mark.parametrize(
    "edit_distance, expected",
    [(0.0, 5), (0.05, 5), (0.051, 4), (0.15, 4), (0.30, 3), (0.31, 2), (0.50, 2), (0.51, 1), (1.0, 1)],
)
def test_edit_distance_is_banded_lower_is_better(workspace_dir: Path, edit_distance, expected):
    ws = Workspace.open(workspace_dir)

    score = ws.score([result(edit_distance=edit_distance)])

    assert score.tasks[0].dimensions["edit_distance"] == expected


# Time saved = share of Manual Time (60 min = 3600 s) saved.
# Default band: better higher, thresholds [0.20, 0.40, 0.60, 0.80].
@pytest.mark.parametrize(
    "active_seconds, expected",
    [
        (4000, 1),  # slower than by hand
        (3600, 1),  # nothing saved
        (2881, 1),  # just under 20% saved
        (2880, 2),  # 20% saved
        (2160, 3),  # 40%
        (1440, 4),  # 60%
        (721, 4),  # just under 80%
        (720, 5),  # 80%
        (0, 5),
    ],
)
def test_time_saved_is_share_of_manual_time_banded_higher_is_better(
    workspace_dir: Path, active_seconds, expected
):
    ws = Workspace.open(workspace_dir)

    score = ws.score([result(active_seconds=active_seconds, manual_minutes=60)])

    assert score.tasks[0].dimensions["time_saved"] == expected


# Default latency band: better lower, thresholds [10, 30, 60, 120] seconds.
@pytest.mark.parametrize(
    "latency_seconds, expected",
    [(0, 5), (10, 5), (10.5, 4), (30, 4), (60, 3), (61, 2), (120, 2), (121, 1)],
)
def test_null_cost_scores_cost_latency_on_latency_alone(
    workspace_dir: Path, latency_seconds, expected
):
    ws = Workspace.open(workspace_dir)

    task = ws.score([result(cost_usd=None, latency_seconds=latency_seconds)]).tasks[0]

    assert task.dimensions["cost_latency"] == expected
    assert task.latency_only


# Default cost band: better lower, thresholds [0.01, 0.05, 0.20, 1.00] USD.
# Latency 10 s scores 5, so Cost/latency = (cost score + 5) / 2.
@pytest.mark.parametrize(
    "cost_usd, cost_score",
    [(0.0, 5), (0.01, 5), (0.011, 4), (0.05, 4), (0.20, 3), (0.21, 2), (1.00, 2), (1.01, 1)],
)
def test_cost_latency_averages_cost_and_latency_scores(
    workspace_dir: Path, cost_usd, cost_score
):
    ws = Workspace.open(workspace_dir)

    task = ws.score([result(cost_usd=cost_usd, latency_seconds=10)]).tasks[0]

    assert task.dimensions["cost_latency"] == (cost_score + 5) / 2
    assert not task.latency_only


def test_quality_is_the_average_of_the_tasks_criteria(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    scores = {"correctness": 5, "completeness": 4, "clarity": 4, "pm_judgment": 2,
              "format_fit": 3, "names_the_metric": 3}

    task = ws.score([result(criterion_scores=scores)]).tasks[0]

    assert task.dimensions["quality"] == pytest.approx(3.5)


def mixed_result() -> TaskResult:
    """Quality 4, Edit distance 5, Time saved 3, Cost/latency 5."""
    return result(
        criterion_scores={"correctness": 4, "clarity": 4},
        edit_distance=0.0,
        active_seconds=2160,
        manual_minutes=60,
        cost_usd=0.0,
        latency_seconds=10,
    )


def test_task_weighted_score_uses_default_weights(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    task = ws.score([mixed_result()]).tasks[0]

    # (50*4 + 20*5 + 20*3 + 10*5) / 100
    assert task.weighted == pytest.approx(4.1)


def test_task_weighted_score_follows_edited_weights(workspace_dir: Path):
    set_setting(
        workspace_dir,
        "weights",
        "{quality: 25, edit_distance: 25, time_saved: 50, cost_latency: 0}",
    )
    ws = Workspace.open(workspace_dir)

    task = ws.score([mixed_result()]).tasks[0]

    # (25*4 + 25*5 + 50*3 + 0*5) / 100
    assert task.weighted == pytest.approx(3.75)
