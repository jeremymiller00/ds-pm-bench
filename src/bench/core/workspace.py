"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.core.candidates import Candidate, load_candidates
from bench.core.catalog import CATALOG
from bench.core.decisions import log_verdict, read_log
from bench.core.errors import TaskNotRunnable, UnknownCandidateError, UnknownTaskError
from bench.core.kinds import Suite
from bench.core.lookup import find_by_id
from bench.core.prices import PriceTable, load_prices
from bench.core.results import TaskResult
from bench.core.rubric import Rubric, load_rubric
from bench.core.runs import (
    Run,
    check_clearance,
    check_gate,
    choose_suite,
    create_run,
    open_run,
    task_versions_used,
    tasks_in,
)
from bench.core.scoring import RunScore, score_run
from bench.core.settings import Settings, load_settings
from bench.core.tasks import (
    CriterionSpec,
    Task,
    TaskContext,
    TaskRoot,
    edit_task,
    load_tasks,
    scaffold_task,
)
from bench.core.verdicts import Comparison, Verdict, compare, gate_verdict


@dataclass
class Workspace:
    """Opened from a benchmark repo. tasks is replaced when a Task is created or edited."""

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
        ctx = TaskContext(root, settings.private_roots, rubric, settings.tracks)
        return cls(
            root=root,
            settings=settings,
            rubric=rubric,
            prices=load_prices(root),
            tasks=load_tasks(ctx),
            candidates=load_candidates(root),
        )

    @property
    def task_context(self) -> TaskContext:
        """What loading, scaffolding or editing a Task needs from this Workspace."""
        return TaskContext(self.root, self.settings.private_roots, self.rubric,
                           self.settings.tracks)

    def task(self, task_id: str) -> Task:
        task = find_by_id(self.tasks, task_id)
        if task is not None:
            return task
        hint = "" if self.settings.private_roots else (
            "; no private roots are configured, so Private Tasks are not loaded"
        )
        raise UnknownTaskError(f"no Task {task_id!r}{hint}")

    def create_task(
        self,
        task_id: str,
        *,
        track: str,
        trap: str,
        canonical_prompt: str,
        criteria: Iterable[CriterionSpec],
        manual_time_minutes: int | float | None = None,
        private_root: str | Path | None = None,
    ) -> Task:
        """Scaffold a new Task at version 1 with a TODO input and Gold Answer.

        It is a Public Task under tasks/ unless private_root names one of the
        configured private roots (ADR-0006). criteria are the task-specific
        Criteria, as Criterion or {id, name, description}. Invalid fields raise
        ConfigError naming the field, and nothing is written.
        """
        if find_by_id(self.tasks, task_id) is not None:
            raise ValueError(f"Task {task_id} already exists in {self.task(task_id).folder}")
        ctx = self.task_context
        root = ctx.public_root if private_root is None else self._private_root(private_root)
        task = scaffold_task(
            ctx,
            root,
            task_id,
            track=track,
            trap=trap,
            canonical_prompt=canonical_prompt,
            criteria=criteria,
            manual_time_minutes=manual_time_minutes,
        )
        self._reload_tasks()
        return task

    def update_task(self, task_id: str, **changes: Any) -> Task:
        """Edit a Task's fields: track, trap, canonical_prompt, criteria,
        manual_time_minutes, input (text) and gold_answer (text).

        If any Run used the Task's current version, the edit is a new version;
        otherwise it is made in place (ADR-0003). Visibility, id and version cannot
        be edited (TaskEditRefused). Invalid fields raise ConfigError and the files
        are left as they were.
        """
        task = self.task(task_id)
        edited = edit_task(
            self.task_context,
            task,
            changes,
            used=(task.id, task.version) in task_versions_used(self.root),
        )
        self._reload_tasks()
        return edited

    def scaffold_catalog(self, private_root: str | Path) -> tuple[Task, ...]:
        """Create the 12 catalog Tasks as Private Tasks in a configured private root.

        Each gets its Track, Trap and task-specific Criteria, with a placeholder
        input, Gold Answer, Manual Time and Canonical Prompt. Refuses, writing
        nothing, if any catalog Task already exists.
        """
        root = self._private_root(private_root)
        clashes = [
            entry.id
            for entry in CATALOG
            if find_by_id(self.tasks, entry.id) is not None
            or (root.tasks_folder / entry.id).exists()
        ]
        if clashes:
            raise ValueError(
                f"catalog Tasks already exist, not overwriting: {', '.join(clashes)}"
            )
        return tuple(
            self.create_task(entry.id, private_root=root.path, **entry.create_args())
            for entry in CATALOG
        )

    def _private_root(self, path: str | Path) -> TaskRoot:
        resolved = (self.root / Path(path).expanduser()).resolve()
        if resolved not in self.settings.private_roots:
            configured = ", ".join(map(str, self.settings.private_roots)) or "none"
            raise ValueError(
                f"{resolved} is not a configured private root (configured: {configured})"
            )
        return self.task_context.private_root(resolved)

    def _reload_tasks(self) -> None:
        self.tasks = load_tasks(self.task_context)

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

    def start_run(
        self,
        candidate_id: str,
        *,
        suite: Suite | str | None = None,
        on: datetime.date | None = None,
    ) -> Run:
        """Start a Run of the Candidate over a Suite.

        suite defaults to every Task the Candidate's Clearance allows; public_only
        narrows a private Clearance. A Suite never exceeds the Clearance (ClearanceError).
        Tasks still holding a placeholder are left out and listed in Run.excluded.
        """
        candidate = self.candidate(candidate_id)
        check_gate(candidate)
        chosen = choose_suite(candidate, suite, self.tasks)
        tasks = tasks_in(chosen, self.tasks)
        return create_run(self.root, candidate, chosen, tasks, on or datetime.date.today())

    def task_input(self, run: Run, task_id: str) -> str:
        """The Canonical Prompt and input of a Task in the Run's Suite.

        A Task the Workspace has but the Candidate is not cleared for raises
        ClearanceError before any of its content is read; one it lacks is unknown.
        """
        check_clearance(run.candidate, self.task(task_id))
        return run.task_input(task_id)

    def resume_run(self, run_id: str) -> Run:
        """Reopen a Run, such as one left half-finished; see Run.pending.

        A finished Run whose Tasks were bumped since loads them at the versions it used,
        from their archives (ADR-0003); an unfinished one raises RunMismatch.
        """
        return open_run(
            self.task_context, run_id, {c.id: c for c in self.candidates}, self.tasks
        )

    def score(self, results: Iterable[TaskResult]) -> RunScore:
        """Score a Run's per-Task result records with this Workspace's weights and bands."""
        return score_run(self.settings, results)

    def compare(self, challenger_run_id: str, baseline_run_id: str) -> Comparison:
        """Compare a challenger Run with a Baseline Run and reach a Verdict.

        Spot-checks recorded on either Run (Run.record_spot_check) are weighed too.
        """
        challenger = self.resume_run(challenger_run_id)
        baseline = self.resume_run(baseline_run_id)
        return compare(
            self.settings,
            challenger,
            self.score(challenger.task_results()),
            baseline,
            self.score(baseline.task_results()),
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
