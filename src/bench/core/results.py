"""The per-Task result record: the raw measures a Run produces for one Task, before scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bench.core.rubric import check_criterion_score


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

    def __post_init__(self) -> None:
        if not self.criterion_scores:
            self._fail("criterion_scores", "must hold at least one Criterion score")
        for cid, value in self.criterion_scores.items():
            check_criterion_score(value, f"Task {self.task_id}: criterion_scores.{cid}")
        if not 0 <= self.edit_distance <= 1:
            self._fail("edit_distance", f"must be between 0 and 1, got {self.edit_distance}")
        if self.manual_minutes <= 0:
            self._fail("manual_minutes", f"must be positive, got {self.manual_minutes}")
        for field in ("active_seconds", "latency_seconds"):
            if getattr(self, field) < 0:
                self._fail(field, f"must not be negative, got {getattr(self, field)}")
        if self.cost_usd is not None and self.cost_usd < 0:
            self._fail("cost_usd", f"must not be negative, got {self.cost_usd}")

    def _fail(self, field: str, message: str) -> None:
        raise ValueError(f"Task {self.task_id}: {field}: {message}")
