"""Timing a person doing a Task by hand, to record its Manual Time."""

from __future__ import annotations

from collections.abc import Callable

from bench.core.adapters import Clock
from bench.core.tasks import Task


class ManualTimeStopwatch:
    """Times a person doing a Task by hand, to record its Manual Time.

    Started on creation (Workspace.begin_manual_time); finish records the elapsed
    minutes, to a tenth, through record and returns what it returns.
    """

    def __init__(
        self, task_id: str, clock: Clock, record: Callable[[str, float], Task]
    ) -> None:
        self.task_id = task_id
        self._clock = clock
        self._record = record
        self._started = clock.now()

    @property
    def elapsed_seconds(self) -> float:
        return self._clock.now() - self._started

    def finish(self) -> Task:
        return self._record(self.task_id, round(self.elapsed_seconds / 60, 1))

