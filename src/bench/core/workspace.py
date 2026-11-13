"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bench.core.settings import Settings, load_settings
from bench.core.prices import PriceTable, load_prices
from bench.core.results import TaskResult
from bench.core.rubric import Rubric, load_rubric
from bench.core.candidates import Candidate, load_candidates
from bench.core.errors import TaskNotRunnable, UnknownCandidateError, UnknownTaskError
from bench.core.runs import Run, create_run, open_run, suite_for
from bench.core.scoring import RunScore, score_run
from bench.core.tasks import Task, load_tasks
from bench.core.verdicts import Comparison, compare


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

    @property
    def baseline(self) -> Candidate | None:
        """The Candidate marked as Baseline; None only while there are no Candidates."""
        return next((c for c in self.candidates if c.baseline), None)

    def candidate(self, candidate_id: str) -> Candidate:
        for candidate in self.candidates:
            if candidate.id == candidate_id:
                return candidate
        raise UnknownCandidateError(f"no Candidate {candidate_id!r} in candidates/")

    def start_run(self, candidate_id: str, *, on: datetime.date | None = None) -> Run:
        """Start a Run of the Candidate over every Task its Clearance allows."""
        candidate = self.candidate(candidate_id)
        suite = tuple(self.runnable_task(t.id) for t in suite_for(candidate, self.tasks))
        return create_run(self.root, candidate, suite, on or datetime.date.today(), self.tasks)

    def resume_run(self, run_id: str) -> Run:
        """Reopen a Run, such as one left half-finished; see Run.pending."""
        return open_run(self.root, run_id, {c.id: c for c in self.candidates}, self.tasks)

    def score(self, results: Iterable[TaskResult]) -> RunScore:
        """Score a Run's per-Task result records with this Workspace's weights and bands."""
        return score_run(self.settings, results)

    def compare(self, challenger_run_id: str, baseline_run_id: str) -> Comparison:
        """Compare a challenger Run with a Baseline Run and reach a Verdict."""
        challenger = self.resume_run(challenger_run_id)
        baseline = self.resume_run(baseline_run_id)
        return compare(
            self.settings,
            challenger,
            self.score(challenger.task_results()),
            baseline,
            self.score(baseline.task_results()),
        )
