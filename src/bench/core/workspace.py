"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.core.candidates import Candidate, load_candidates
from bench.core.decisions import log_verdict, read_log
from bench.core.errors import TaskNotRunnable, UnknownCandidateError, UnknownTaskError
from bench.core.lookup import find_by_id
from bench.core.prices import PriceTable, load_prices
from bench.core.results import TaskResult
from bench.core.rubric import Rubric, load_rubric
from bench.core.runs import Run, create_run, open_run, suite_for
from bench.core.scoring import RunScore, score_run
from bench.core.settings import Settings, load_settings
from bench.core.tasks import Task, load_tasks
from bench.core.verdicts import Comparison, Verdict, compare, gate_verdict


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
        task = find_by_id(self.tasks, task_id)
        if task is not None:
            return task
        hint = "" if self.settings.private_roots else (
            "; no private roots are configured, so Private Tasks are not loaded"
        )
        raise UnknownTaskError(f"no Task {task_id!r}{hint}")

    def runnable_task(self, task_id: str) -> Task:
        """The Task; TaskNotRunnable if its input, Gold Answer or Manual Time is a placeholder."""
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
        candidate = find_by_id(self.candidates, candidate_id)
        if candidate is None:
            raise UnknownCandidateError(f"no Candidate {candidate_id!r} in candidates/")
        return candidate

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

    def compare(
        self,
        challenger_run_id: str,
        baseline_run_id: str,
        *,
        spot_check_disagreement: float | None = None,
    ) -> Comparison:
        """Compare a challenger Run with a Baseline Run and reach a Verdict.

        spot_check_disagreement is the largest Judge-versus-human gap, in points,
        recorded on a Spot-check of these Runs; None when none was recorded.
        """
        challenger = self.resume_run(challenger_run_id)
        baseline = self.resume_run(baseline_run_id)
        return compare(
            self.settings,
            challenger,
            self.score(challenger.task_results()),
            baseline,
            self.score(baseline.task_results()),
            spot_check_disagreement,
        )

    def gate_verdict(self, candidate_id: str) -> Verdict:
        """The reject Verdict for a Candidate whose Gate has not passed; needs no Runs."""
        baseline = self.baseline
        if baseline is None:  # unreachable: candidate() found at least one Candidate
            raise UnknownCandidateError("no Baseline in candidates/")
        return gate_verdict(self.candidate(candidate_id), baseline)

    def log_verdict(
        self,
        verdict: Verdict,
        *,
        on: datetime.date | None = None,
        judge_setup_hash: str | None = None,
    ) -> Path:
        """Append a Verdict to the Decision Log; returns the new entry's file."""
        return log_verdict(self.root, verdict, on or datetime.date.today(), judge_setup_hash)

    def decision_log(self) -> tuple[dict[str, Any], ...]:
        """Every Decision Log entry, oldest first, as written to decisions/."""
        return read_log(self.root)
