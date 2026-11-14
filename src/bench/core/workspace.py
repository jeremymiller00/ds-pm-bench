"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

import datetime
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.core.adapters import Clock, Editor, ExternalEditor, SystemClock
from bench.core.attempts import Attempt
from bench.core.candidates import (
    CANDIDATES_FOLDER,
    Candidate,
    candidate_data,
    candidate_id_for,
    gate_data,
    load_candidates,
    write_candidate,
)
from bench.core.catalog import CATALOG
from bench.core.decisions import log_verdict, read_log
from bench.core.errors import (
    AttemptError,
    CandidateAuthoringError,
    GateNotPassed,
    TaskAuthoringError,
    TaskNotRunnable,
    UnknownCandidateError,
    UnknownTaskError,
)
from bench.core.fields import FieldReader
from bench.core.kinds import AdapterKind, Clearance, Suite
from bench.core.lookup import find_by_id
from bench.core.manual_time import ManualTimeStopwatch
from bench.core.prices import PriceTable, load_prices
from bench.core.results import TaskResult
from bench.core.rubric import Rubric, load_rubric
from bench.core.runs import (
    Run,
    candidate_ids_used,
    check_clearance,
    check_gate,
    choose_suite,
    create_run,
    list_run_ids,
    open_run,
    suites_for,
    task_versions_used,
    tasks_in,
)
from bench.core.scoring import RunScore, score_run
from bench.core.settings import SETTINGS_FILE, Settings, load_settings
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

EDITABLE_CANDIDATE_FIELDS = ("adapter", "settings", "env_vars", "cost_per_task_usd")
_NOT_EDITABLE = {
    "tool": "a different tool is a different Candidate; create_candidate it",
    "version": "a different version is a different Candidate; create_candidate it",
    "baseline": "use mark_baseline",
    "gate": "use record_gate",
}


@dataclass
class Workspace:
    """Opened from the repo root holding config/, Tasks, Candidates and Runs.

    tasks is replaced when a Task is created or edited, candidates when a Candidate is.
    clock and editor are the Adapters for timing and $EDITOR; tests inject fakes.
    """

    root: Path
    settings: Settings
    rubric: Rubric
    prices: PriceTable
    tasks: tuple[Task, ...]
    candidates: tuple[Candidate, ...]
    clock: Clock = field(default_factory=SystemClock)
    editor: Editor = field(default_factory=ExternalEditor)

    @classmethod
    def open(
        cls, path: str | Path, *, clock: Clock | None = None, editor: Editor | None = None
    ) -> Workspace:
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
            clock=clock or SystemClock(),
            editor=editor or ExternalEditor(),
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
        name: str | None = None,
        output_shape: str | None = None,
    ) -> Task:
        """Scaffold a new Task at version 1 with a TODO input and Gold Answer.

        It is a Public Task under tasks/ unless private_root names one of the
        configured private roots (ADR-0006). criteria are the task-specific
        Criteria, each {id, name, description}. name and output_shape are the
        catalog's task name and Output shape, optional. Invalid fields raise ConfigError
        naming the field; a taken id or an unconfigured root raises
        TaskAuthoringError. Either way nothing is written.
        """
        if find_by_id(self.tasks, task_id) is not None:
            raise TaskAuthoringError(
                f"Task {task_id} already exists in {self.task(task_id).folder}"
            )
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
            name=name,
            output_shape=output_shape,
        )
        self._reload_tasks()
        return task

    def update_task(self, task_id: str, **changes: Any) -> Task:
        """Edit a Task's fields: name, track, trap, output_shape, canonical_prompt,
        criteria, manual_time_minutes, input (text) and gold_answer (text).

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

    def record_manual_time(self, task_id: str, minutes: int | float) -> Task:
        """Record how long a person takes on the Task by hand, for the Time-saved
        Dimension; an edit like any other (update_task)."""
        return self.update_task(task_id, manual_time_minutes=minutes)

    def begin_manual_time(self, task_id: str) -> ManualTimeStopwatch:
        """Start a stopwatch on a person doing the Task by hand; its finish records the
        Manual Time (record_manual_time)."""
        self.task(task_id)
        return ManualTimeStopwatch(task_id, self.clock, self.record_manual_time)

    def scaffold_catalog(self, private_root: str | Path) -> tuple[Task, ...]:
        """Create the 12 catalog Tasks as Private Tasks in a configured private root.

        Each gets its Track, Trap and task-specific Criteria, with a placeholder
        input, Gold Answer, Manual Time and Canonical Prompt. Refuses, writing
        nothing, if any catalog Task already exists or its Track is not configured;
        should a Task still fail to scaffold, the ones already written are removed.
        """
        root = self._private_root(private_root)
        unknown = sorted({e.track for e in CATALOG} - set(self.settings.tracks))
        if unknown:
            raise TaskAuthoringError(
                f"catalog Tracks missing from {SETTINGS_FILE}: {', '.join(unknown)}"
            )
        clashes = [
            entry.id
            for entry in CATALOG
            if find_by_id(self.tasks, entry.id) is not None
            or (root.tasks_folder / entry.id).exists()
        ]
        if clashes:
            raise TaskAuthoringError(
                f"catalog Tasks already exist, not overwriting: {', '.join(clashes)}"
            )
        folder_existed = root.tasks_folder.exists()
        created: list[Task] = []
        try:
            for entry in CATALOG:
                created.append(
                    self.create_task(entry.id, private_root=root.path, **entry.create_args())
                )
        except BaseException:
            for task in created:
                shutil.rmtree(task.folder)
            if not folder_existed:
                shutil.rmtree(root.tasks_folder, ignore_errors=True)
            self._reload_tasks()
            raise
        return tuple(created)

    def _private_root(self, path: str | Path) -> TaskRoot:
        resolved = (self.root / Path(path).expanduser()).resolve()
        if resolved not in self.settings.private_roots:
            configured = ", ".join(map(str, self.settings.private_roots)) or "none"
            raise TaskAuthoringError(
                f"{resolved} is not a configured private root (configured: {configured})"
            )
        return self.task_context.private_root(resolved)

    def _reload_tasks(self) -> None:
        self.tasks = load_tasks(self.task_context)

    def runnable_task(self, task_id: str) -> Task:
        """The Task; TaskNotRunnable if any of its input, Gold Answer, Manual Time or
        Canonical Prompt is still a placeholder."""
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

    def create_candidate(
        self,
        *,
        tool: str,
        version: str,
        adapter: str,
        settings: dict[str, Any] | None = None,
        env_vars: Iterable[str] = (),
        cost_per_task_usd: float | None = None,
        baseline: bool = False,
    ) -> Candidate:
        """Write a new Candidate, ungated, to candidates/<tool>@<version>.yaml.

        adapter is "automated" or "manual_assisted"; env_vars are the names of the
        environment variables holding its secrets, never the values (ADR-0005).
        cost_per_task_usd is a manual-assisted Candidate's flat cost per Task.
        baseline=True moves the Baseline mark to it; the first Candidate must be the
        Baseline. Invalid fields raise ConfigError naming the field; a taken id or a
        first Candidate that is not the Baseline raises CandidateAuthoringError.
        Either way nothing is written.
        """
        candidate_id = candidate_id_for(tool, version)
        if find_by_id(self.candidates, candidate_id) is not None or (
            self.root / CANDIDATES_FOLDER / f"{candidate_id}.yaml"
        ).exists():
            raise CandidateAuthoringError(f"Candidate {candidate_id} already exists")
        if not baseline and self.baseline is None:
            raise CandidateAuthoringError(
                f"{candidate_id} would be the first Candidate, so it must be the Baseline"
            )
        data: dict[str, Any] = {
            "tool": tool,
            "version": version,
            "adapter": adapter,
            "settings": {} if settings is None else dict(settings),
            "env_vars": list(env_vars),
            "cost_per_task_usd": cost_per_task_usd,
            "baseline": baseline,
            "gate": None,
        }
        created = write_candidate(self.root, candidate_id, data)
        if baseline:
            self._unmark_baselines_except(created.id)
        self._reload_candidates()
        return self.candidate(created.id)

    def mark_baseline(self, candidate_id: str) -> Candidate:
        """Make the Candidate the Baseline, unmarking the previous one.

        The mark is a designation, not part of what a Run freezes, so it moves in place
        even on a used Candidate. A Candidate whose Gate failed cannot hold it
        (GateNotPassed); one not yet reviewed can, but cannot be compared until it passes.
        """
        target = self.candidate(candidate_id)
        if target.gate is not None and not target.gate.passed:
            raise GateNotPassed(
                f"Candidate {target.id}'s Gate failed, so it cannot be the Baseline"
            )
        self._write(target, baseline=True)
        self._unmark_baselines_except(target.id)
        self._reload_candidates()
        return self.candidate(candidate_id)

    def _unmark_baselines_except(self, candidate_id: str) -> None:
        for other in self.candidates:
            if other.baseline and other.id != candidate_id:
                self._write(other, baseline=False)

    def record_gate(
        self,
        candidate_id: str,
        *,
        reviewer: str,
        on: datetime.date,
        checklist: dict[str, Any],
        passed: bool,
        clearance: Clearance | str | None,
    ) -> Verdict | None:
        """Add a review to the Candidate's Gate; the latest review applies (ADR-0003).

        The Gate is a review record, not part of what a Run freezes, so it is updated in
        place, even on a used Candidate, and every earlier review is kept. The review
        applies at once to every Run of the Candidate, including one in progress.
        checklist holds retention (text), sso (bool), enterprise_terms (bool) and
        connector_support (text). A passing Gate needs a Clearance; a failed one grants
        none. A review that moves the Gate to failed appends a reject Verdict to the
        Decision Log dated on (story 18) and returns it; a repeated fail, or a pass,
        logs nothing and returns None. Invalid fields raise ConfigError naming the field
        and change nothing.
        """
        candidate = self.candidate(candidate_id)
        was_failed = candidate.gate is not None and not candidate.gate.passed
        f = FieldReader(f"{CANDIDATES_FOLDER}/{candidate.id}.yaml")
        review = gate_data(
            f, reviewer=reviewer, on=on, checklist=checklist, passed=passed,
            clearance=clearance,
        )
        reviews = candidate_data(candidate)["gate"]
        self._write(candidate, gate=[*reviews, review])
        self._reload_candidates()
        if passed or was_failed:
            return None
        verdict = self.gate_verdict(candidate.id)
        self.log_verdict(verdict, on=on)
        return verdict

    def update_candidate(self, candidate_id: str, **changes: Any) -> Candidate:
        """Change a Candidate's adapter, settings, env_vars or cost_per_task_usd.

        A Candidate a Run has used is frozen (ADR-0003), so the change is written as
        the next Candidate in its Lineage, <tool>@<version>~<n>, which keeps the Gate
        and takes the Baseline mark if the old one held it; the used file stays as it
        was. Otherwise
        the change is made in place. A superseded Candidate, or a field naming a
        different Candidate (tool, version) or set elsewhere (baseline, gate), raises
        CandidateAuthoringError; invalid values raise ConfigError. Nothing is written.
        """
        for field_name in changes:
            if field_name not in EDITABLE_CANDIDATE_FIELDS:
                raise CandidateAuthoringError(
                    f"cannot change {field_name}: {_NOT_EDITABLE.get(field_name, 'unknown')}"
                )
        candidate = self._changeable(candidate_id)
        if "env_vars" in changes:
            changes["env_vars"] = list(changes["env_vars"])
        return self._change(candidate, changes)

    @property
    def current_candidates(self) -> tuple[Candidate, ...]:
        """The latest Candidate in each Lineage, leaving out superseded ones."""
        return tuple(c for c in self.candidates if self._superseding(c) is None)

    def _superseding(self, candidate: Candidate) -> Candidate | None:
        return max(
            (c for c in self.candidates
             if c.lineage == candidate.lineage
             and c.lineage_position > candidate.lineage_position),
            key=lambda c: c.lineage_position,
            default=None,
        )

    def _changeable(self, candidate_id: str) -> Candidate:
        candidate = self.candidate(candidate_id)
        newer = self._superseding(candidate)
        if newer is not None:
            raise CandidateAuthoringError(
                f"Candidate {candidate.id} is superseded by {newer.id}; change that instead"
            )
        return candidate

    def _change(self, candidate: Candidate, changes: dict[str, Any]) -> Candidate:
        """Write the change in place, or as the next Candidate in its Lineage if a Run
        used it."""
        if candidate.id not in candidate_ids_used(self.root):
            changed = self._write(candidate, **changes)
        else:
            next_id = f"{candidate.lineage}~{candidate.lineage_position + 1}"
            data = candidate_data(candidate)
            data.update(changes)
            changed = write_candidate(self.root, next_id, data)
            if changed.baseline:
                self._unmark_baselines_except(changed.id)
        self._reload_candidates()
        return self.candidate(changed.id)

    def _write(self, candidate: Candidate, **changes: Any) -> Candidate:
        data = candidate_data(candidate)
        data.update(changes)
        return write_candidate(self.root, candidate.id, data)

    def _reload_candidates(self) -> None:
        self.candidates = load_candidates(self.root)

    def suites_for(self, candidate_id: str) -> tuple[Suite, ...]:
        """The Suites a Run of the Candidate may cover, its default first: what its
        Clearance allows, or none without a passing Gate."""
        return suites_for(self.candidate(candidate_id))

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

        The Candidate's latest Gate review applies, even to a Run already open: a failed
        Gate raises GateNotPassed and a narrowed one ClearanceError (ADR-0003).

        A Task the Workspace has but the Candidate is not cleared for raises
        ClearanceError before any of its content is read; one it lacks is unknown.
        """
        task = self.task(task_id)
        candidate = self.candidate(run.candidate_id)  # its latest Gate review applies
        check_gate(candidate)
        check_clearance(candidate, task)
        return run.task_input(task_id)

    def begin_manual_task(self, run: Run, task_id: str) -> Attempt:
        """Begin working a Task of the Run by hand: returns the Attempt showing its
        Canonical Prompt and input, with the clock started.

        Only a Run of a manual-assisted Candidate can be worked by hand (AttemptError).
        The latest Gate and Clearance are checked before any content is read
        (GateNotPassed, ClearanceError). A Task whose Output is already recorded cannot
        be begun again (AttemptError).
        """
        if not run.candidate.manual_assisted:
            raise AttemptError(
                f"Run {run.id}: Candidate {run.candidate_id} is {run.candidate.adapter}, "
                f"so its Tasks cannot be worked by hand; only {AdapterKind.MANUAL_ASSISTED} "
                "Candidates can"
            )
        prompt = self.task_input(run, task_id)
        if task_id not in run.pending:
            raise AttemptError(f"Run {run.id}: Task {task_id}'s Output is already recorded")
        return Attempt(run, task_id, prompt, self.clock, self.editor)

    def run_ids(self) -> tuple[str, ...]:
        """The id of every Run under runs/, newest first, for choosing one to resume."""
        return list_run_ids(self.root)

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
        The Baseline must hold a passing Gate: its latest review applies (GateNotPassed).
        """
        baseline = self.baseline
        if baseline is not None and not baseline.gate_passed:
            raise GateNotPassed(
                f"Baseline {baseline.id}'s latest Gate review has not passed, so nothing "
                "can be compared with it; re-review its Gate or mark another Baseline"
            )
        challenger = self.resume_run(challenger_run_id)
        baseline_run = self.resume_run(baseline_run_id)
        return compare(
            self.settings,
            challenger,
            self.score(challenger.task_results()),
            baseline_run,
            self.score(baseline_run.task_results()),
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
