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


def score_run(settings: Settings, results: Iterable[TaskResult]) -> RunScore:
    bands = settings.normalization
    return RunScore(
        tasks=tuple(
            TaskScore(
                task_id=r.task_id,
                track=r.track,
                dimensions={"edit_distance": band_score(bands["edit_distance"], r.edit_distance)},
            )
            for r in results
        )
    )
