"""The Spot-check queue: which Tasks of a judged Run a human scores blind (PLAN.md).

A share of the Tasks (spot_check_rate, 3 of 12 by default) is sampled reproducibly,
seeded from the Run id. Tracks the same Candidate's previous queue did not cover are
taken first, one Task each, so every Track is checked across two Runs. Tasks the Judge
marked for a full human check (a same-family judge model, a panel disagreement) are
always queued, so a same-family Candidate gets every Task.
"""

from __future__ import annotations

import math
import random
from collections.abc import Collection, Iterable

from bench.core.tasks import Task


def sample_size(rate: float, tasks: int) -> int:
    """The share of Tasks to check, rounded half up; at least one for a nonzero rate."""
    if rate <= 0 or tasks == 0:
        return 0
    return min(tasks, max(1, math.floor(rate * tasks + 0.5)))


def spot_check_queue(
    run_id: str,
    tasks: tuple[Task, ...],
    rate: float,
    *,
    covered_before: Collection[str] = (),
    full_checks: Iterable[str] = (),
) -> tuple[str, ...]:
    """The Task ids to Spot-check, in Suite order.

    covered_before is the Tracks the same Candidate's previous queue covered.
    """
    rng = random.Random(run_id)
    size = sample_size(rate, len(tasks))
    pool = list(tasks)
    rng.shuffle(pool)
    uncovered: dict[str, Task] = {}
    for task in pool:
        if task.track not in covered_before:
            uncovered.setdefault(task.track, task)
    first = list(uncovered.values())
    rng.shuffle(first)
    picked = {t.id for t in first[:size]}
    for task in pool:
        if len(picked) >= size:
            break
        picked.add(task.id)
    picked.update(full_checks)
    return tuple(t.id for t in tasks if t.id in picked)
