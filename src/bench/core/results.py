"""The per-Task result record: the raw measures a Run produces for one Task, before scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskResult:
    """What one Task in a Run measured. Scoring turns these into 1-5 Dimension scores.

    task_id: the Task's id, e.g. "T1.2".
    track: the Track the Task belongs to, e.g. "Framing".
    criterion_scores: Criterion id -> 1-5 score from the Judge (shared plus task Criteria).
    edit_distance: share of words changed between raw Output and edited final, 0-1.
    active_seconds: PM active time on the Task with the Candidate (prompting plus editing).
    manual_minutes: the Task's Manual Time.
    cost_usd: cost of the Task in USD, or None when the Candidate has no per-Task cost.
    latency_seconds: seconds to first draft.
    """

    task_id: str
    track: str
    criterion_scores: Mapping[str, float]
    edit_distance: float
    active_seconds: float
    manual_minutes: float
    cost_usd: float | None
    latency_seconds: float
