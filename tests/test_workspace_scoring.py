from pathlib import Path

import pytest

from bench import TaskResult, Workspace


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
