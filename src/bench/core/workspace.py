"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bench.core.settings import Settings, load_settings
from bench.core.prices import PriceTable, load_prices
from bench.core.rubric import Rubric, load_rubric
from bench.core.candidates import Candidate, load_candidates
from bench.core.errors import TaskNotRunnable, UnknownCandidateError, UnknownTaskError
from bench.core.tasks import Task, load_tasks


@dataclass(frozen=True)
class Workspace:
    root: Path
    settings: Settings
    rubric: Rubric
    prices: PriceTable
    tasks: tuple[Task, ...]
    candidates: tuple[Candidate, ...]

    @classmethod
    def open(cls, path: str | Path) -> Workspace:
        root = Path(path).resolve()
        settings = load_settings(root)
        rubric = load_rubric(root)
        return cls(
            root=root,
            settings=settings,
            rubric=rubric,
            prices=load_prices(root),
            tasks=load_tasks(root, settings.private_roots, rubric),
            candidates=load_candidates(root),
        )

    def task(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        hint = "" if self.settings.private_roots else (
            "; no private roots are configured, so Private Tasks are not loaded"
        )
        raise UnknownTaskError(f"no Task {task_id!r}{hint}")

    def runnable_task(self, task_id: str) -> Task:
        """The Task, or TaskNotRunnable if its input, Gold Answer or Manual Time is a placeholder."""
        task = self.task(task_id)
        if task.placeholders:
            raise TaskNotRunnable(
                f"Task {task.id} cannot be Run: {', '.join(task.placeholders)} "
                "still a placeholder"
            )
        return task

    def candidate(self, candidate_id: str) -> Candidate:
        for candidate in self.candidates:
            if candidate.id == candidate_id:
                return candidate
        raise UnknownCandidateError(f"no Candidate {candidate_id!r} in candidates/")
