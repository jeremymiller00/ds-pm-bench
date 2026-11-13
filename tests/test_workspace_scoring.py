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
