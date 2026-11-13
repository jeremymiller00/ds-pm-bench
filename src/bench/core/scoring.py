"""Scoring: normalize each Dimension to 1-5 and combine them with the configured weights."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bench.core.results import TaskResult
from bench.core.settings import Band, Settings


@dataclass(frozen=True)
class TaskScore:
    task_id: str
    track: str
    dimensions: dict[str, float]


@dataclass(frozen=True)
class RunScore:
    tasks: tuple[TaskScore, ...]


def band_score(band: Band, value: float) -> int:
    """Map a raw measure to 1-5 through a Band. Threshold values fall in the better band."""
    t = band.thresholds
    if band.better == "lower":
        return 5 - sum(value > x for x in t)
    return 1 + sum(value >= x for x in t)


def time_saved(result: TaskResult) -> float:
    """Share of Manual Time saved; negative when the Task took longer than by hand."""
    manual_seconds = result.manual_minutes * 60
    return (manual_seconds - result.active_seconds) / manual_seconds


def score_task(settings: Settings, result: TaskResult) -> TaskScore:
    bands = settings.normalization
    return TaskScore(
        task_id=result.task_id,
        track=result.track,
        dimensions={
            "edit_distance": band_score(bands["edit_distance"], result.edit_distance),
            "time_saved": band_score(bands["time_saved"], time_saved(result)),
        },
    )


def score_run(settings: Settings, results: Iterable[TaskResult]) -> RunScore:
    return RunScore(tasks=tuple(score_task(settings, r) for r in results))
