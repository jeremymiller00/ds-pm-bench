"""Scoring: normalize each Dimension to 1-5 and combine them with the configured weights."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean

from bench.core.results import TaskResult
from bench.core.settings import DIMENSIONS, Band, Settings


@dataclass(frozen=True)
class TaskScore:
    task_id: str
    track: str
    dimensions: dict[str, float]
    latency_only: bool  # no per-Task cost: Cost/latency is the latency score alone
    weighted: float  # weighted total of the Dimension scores, 1-5


@dataclass(frozen=True)
class RunScore:
    tasks: tuple[TaskScore, ...]
    dimensions: dict[str, float]  # Dimension -> average 1-5 score over the Run's Tasks
    weighted: float  # weighted total of the Run's Dimension scores (= mean Task weighted)
    track_quality: dict[str, float]  # Track -> average Quality over its Tasks
    latency_only_tasks: tuple[str, ...]  # Task ids whose Cost/latency is latency only
    notes: tuple[str, ...]  # human-readable scoring notes for the scorecard


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


def cost_latency(settings: Settings, result: TaskResult) -> float:
    """Average of the cost and latency scores, or the latency score alone when cost is None."""
    bands = settings.normalization
    latency = band_score(bands["latency"], result.latency_seconds)
    if result.cost_usd is None:
        return latency
    return (band_score(bands["cost"], result.cost_usd) + latency) / 2


def weighted(settings: Settings, dimensions: dict[str, float]) -> float:
    """Weighted total of 1-5 Dimension scores; weights are percents summing to 100."""
    return sum(settings.weights[d] * dimensions[d] for d in DIMENSIONS) / 100


def score_task(settings: Settings, result: TaskResult) -> TaskScore:
    bands = settings.normalization
    dimensions = {
        "quality": mean(result.criterion_scores.values()),
        "edit_distance": band_score(bands["edit_distance"], result.edit_distance),
        "time_saved": band_score(bands["time_saved"], time_saved(result)),
        "cost_latency": cost_latency(settings, result),
    }
    return TaskScore(
        task_id=result.task_id,
        track=result.track,
        dimensions=dimensions,
        latency_only=result.cost_usd is None,
        weighted=weighted(settings, dimensions),
    )


def score_run(settings: Settings, results: Iterable[TaskResult]) -> RunScore:
    tasks = tuple(score_task(settings, r) for r in results)
    if not tasks:
        raise ValueError("cannot score a Run with no Task results")
    tracks = dict.fromkeys(t.track for t in tasks)
    dimensions = {d: mean(t.dimensions[d] for t in tasks) for d in DIMENSIONS}
    latency_only = tuple(t.task_id for t in tasks if t.latency_only)
    notes = ()
    if latency_only:
        notes = (
            f"Cost/latency scored on latency only for {', '.join(latency_only)}: "
            "no per-Task cost, so the cost share of Cost/latency moves to latency.",
        )
    return RunScore(
        tasks=tasks,
        dimensions=dimensions,
        weighted=weighted(settings, dimensions),
        latency_only_tasks=latency_only,
        notes=notes,
        track_quality={
            track: mean(t.dimensions["quality"] for t in tasks if t.track == track)
            for track in tracks
        },
    )
