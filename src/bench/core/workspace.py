"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

import datetime
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.core.adapters import (
    AdapterFactory,
    CandidateAdapter,
    Clock,
    Editor,
    ExternalEditor,
    JudgeModel,
    JudgeModels,
    SystemClock,
)
from bench.core.archive import (
    archive_judge_prompt,
    archive_rubric,
    archived_judge_prompt,
    archived_rubric,
)
from bench.core.attempts import Attempt
from bench.core.automated import ReturnedOutput, load_returned, run_task
from bench.core.calibration import Agreement, CalibrationItem, CalibrationSet
from bench.core.candidates import (
    CANDIDATES_FOLDER,
    Candidate,
    candidate_data,
    candidate_id_for,
    edit_refusal,
    load_candidates,
    model_for,
    new_candidate_data,
    new_gate_review,
    provider_for,
    review_data,
    write_candidate,
)
from bench.core.catalog import CATALOG
from bench.core.decisions import (
    certified,
    log_certification,
    log_override,
    log_verdict,
    override_logged,
    read_log,
)
from bench.core.errors import (
    AttemptError,
    CandidateAuthoringError,
    CandidateUnavailable,
    CertificationFailed,
    ConfigEditRefused,
    ConfigError,
    GateNotPassed,
    JudgeNotCertified,
    JudgeRefused,
    JudgeUnavailable,
    MissingSecret,
    RunNotFinished,
    TaskAuthoringError,
    TaskNotRunnable,
    UnknownCandidateError,
    UnknownTaskError,
)
from bench.core.judging import (
    JUDGE_FILE,
    BlindOutput,
    JudgeModelSpec,
    JudgeRequest,
    JudgeSetup,
    TaskJudging,
    blind_output,
    check_judge_clearance,
    edit_judge_setup,
    is_candidate_under_test,
    load_judge_setup,
    parse_reply,
    same_family,
)
from bench.core.kinds import AdapterKind, Clearance, Suite
from bench.core.lookup import find_by_id
from bench.core.manual_time import ManualTimeStopwatch
from bench.core.prices import PriceTable, load_prices, remove_price, set_price
from bench.core.results import TaskResult
from bench.core.rubric import RUBRIC_FILE, Rubric, load_rubric, parse_rubric_edit, write_rubric
from bench.core.runs import (
    Run,
    SpotCheck,
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
    tracks_spot_checked_before,
)
from bench.core.scoring import RunScore, score_run
from bench.core.settings import SETTINGS_FILE, Settings, edit_settings, load_settings
from bench.core.spot_checks import spot_check_queue
from bench.core.tasks import (
    CriterionSpec,
    Task,
    TaskContext,
    TaskRoot,
    edit_task,
    load_tasks,
    load_version,
    scaffold_task,
)
from bench.core.verdicts import Comparison, Verdict, compare, gate_verdict


@dataclass
class Workspace:
    """Opened from the repo root holding config/, Tasks, Candidates and Runs.

    tasks is replaced when a Task is created or edited, candidates when a Candidate is.
    clock and editor are the Adapters for timing and $EDITOR, candidate_adapters
    gives an automated Candidate its Adapter, and judges gives the JudgeModel for
    each judge model of the Judge Setup; tests inject fakes, and the composition root
    (bench.wiring.open_workspace) injects the provider SDKs.
    """

    root: Path
    settings: Settings
    rubric: Rubric
    prices: PriceTable
    tasks: tuple[Task, ...]
    candidates: tuple[Candidate, ...]
    clock: Clock
    editor: Editor
    candidate_adapters: AdapterFactory | None  # None: no automated Task can be sent
    judge_setup: JudgeSetup | None  # config/judge.yaml; None when absent
    judges: JudgeModels | None  # None: no Run can be judged

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        clock: Clock | None = None,
        editor: Editor | None = None,
        candidate_adapters: AdapterFactory | None = None,
        judges: JudgeModels | None = None,
    ) -> Workspace:
        """Without candidate_adapters, sending an automated Task raises
        CandidateUnavailable; without judges, judging raises JudgeUnavailable. Nothing
        else needs them. bench.wiring.open_workspace passes the provider SDKs."""
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
            candidate_adapters=candidate_adapters,
            judge_setup=load_judge_setup(root),
            judges=judges,
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
        data = new_candidate_data(
            tool=tool,
            version=version,
            adapter=adapter,
            settings={} if settings is None else dict(settings),
            env_vars=list(env_vars),
            cost_per_task_usd=cost_per_task_usd,
            baseline=baseline,
        )
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
        self._write(target, {"baseline": True})
        self._unmark_baselines_except(target.id)
        self._reload_candidates()
        return self.candidate(candidate_id)

    def _unmark_baselines_except(self, candidate_id: str) -> None:
        for other in self.candidates:
            if other.baseline and other.id != candidate_id:
                self._write(other, {"baseline": False})

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
        review = new_gate_review(
            candidate.id, reviewer=reviewer, on=on, checklist=checklist, passed=passed,
            clearance=clearance,
        )
        self._write(candidate, {"gate": [review_data(r) for r in (*candidate.gate_reviews,
                                                                  review)]})
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
            refusal = edit_refusal(field_name)
            if refusal is not None:
                raise CandidateAuthoringError(f"cannot change {field_name}: {refusal}")
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
            changed = self._write(candidate, changes)
        else:
            next_id = f"{candidate.lineage}~{candidate.lineage_position + 1}"
            changed = self._write(candidate, changes, as_id=next_id)
            if changed.baseline:
                self._unmark_baselines_except(changed.id)
        self._reload_candidates()
        return self.candidate(changed.id)

    def _write(
        self, candidate: Candidate, changes: dict[str, Any], *, as_id: str | None = None
    ) -> Candidate:
        """Write the Candidate with the changes to its own file, or to as_id's."""
        return write_candidate(
            self.root, as_id or candidate.id, {**candidate_data(candidate), **changes}
        )

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
        return create_run(self.root, candidate, chosen, tasks, on or self.clock.today(),
                          archive_rubric(self.root, self.rubric))

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

    def begin_attempt(self, run: Run, task_id: str) -> Attempt:
        """Begin an Attempt at a Task of the Run, the clock starting now.

        For a manual-assisted Candidate it shows the Canonical Prompt and input and waits
        for the raw Output to be pasted. For an automated one it starts with the Returned
        Output already in (run_automated_task sends it first); finishing records its
        tokens, model, tries, latency and cost and removes the Returned Output, while
        abandoning keeps it for another Attempt.

        The latest Gate and Clearance are checked before any content is read, and again
        at finish (GateNotPassed, ClearanceError). A Task whose Output is already
        recorded, or an automated Task with no Returned Output, raises AttemptError.
        """
        prompt = self._unrecorded_task_input(run, task_id)
        returned = None
        if not run.candidate.manual_assisted:
            returned = load_returned(run, task_id)
            if returned is None:
                raise AttemptError(
                    f"Run {run.id}: Task {task_id} has no raw Output to edit; send it first"
                )
        return Attempt(run, task_id, prompt, self.clock, self.editor,
                       self._gate_and_clearance_check(run, task_id), returned=returned)

    def _unrecorded_task_input(self, run: Run, task_id: str) -> str:
        """The Task's Canonical Prompt and input after the latest Gate and Clearance are
        checked; AttemptError if its Output is already recorded."""
        prompt = self.task_input(run, task_id)
        if task_id not in run.pending:
            raise AttemptError(f"Run {run.id}: Task {task_id}'s Output is already recorded")
        return prompt

    def _gate_and_clearance_check(self, run: Run, task_id: str) -> Callable[[], None]:
        def check_gate_and_clearance() -> None:
            candidate = self.candidate(run.candidate_id)  # its latest Gate review
            check_gate(candidate)
            check_clearance(candidate, self.task(task_id))

        return check_gate_and_clearance

    def sendable_tasks(self, run: Run) -> tuple[str, ...]:
        """Task ids of an automated Run that run_automated_task would send: not yet
        recorded and with no Returned Output. None for a manual-assisted Run."""
        if run.candidate.manual_assisted:
            return ()
        return tuple(t for t in run.pending if load_returned(run, t) is None)

    def run_automated_task(self, run: Run, task_id: str) -> ReturnedOutput:
        """Send a Task of an automated Run to its Candidate and store its Returned
        Output for an Attempt to edit; returns it (see bench.core.automated).

        The latest Gate and Clearance are checked before any content is read
        (GateNotPassed, ClearanceError), and the Adapter is built (MissingSecret for an
        unset API key) before anything is sent. The Canonical Prompt and input go word
        for word; latency is the successful call's, timed with the Clock, and cost is
        tokens times the price table, null with a note for a model it lacks. A tool
        error is retried exactly once; a second raises CandidateToolError and stores
        nothing. A manual-assisted Run, or a Task already recorded or already holding a
        Returned Output, raises AttemptError.
        """
        if run.candidate.manual_assisted:
            raise AttemptError(
                f"Run {run.id}: Candidate {run.candidate_id} is {run.candidate.adapter}; "
                f"only {AdapterKind.AUTOMATED} Candidates' Tasks can be sent automatically"
            )
        prompt = self._unrecorded_task_input(run, task_id)
        if load_returned(run, task_id) is not None:
            raise AttemptError(
                f"Run {run.id}: Task {task_id} already has a Returned Output ready to edit"
            )
        adapter = self._candidate_adapter(run.candidate)
        return run_task(run, task_id, prompt, adapter, self.clock, self.prices)

    def returned_output(self, run: Run, task_id: str) -> ReturnedOutput | None:
        """A Task's Returned Output, ready for an Attempt to edit, or None if it has none."""
        return load_returned(run, task_id)

    def _candidate_adapter(self, candidate: Candidate) -> CandidateAdapter:
        if self.candidate_adapters is None:
            raise CandidateUnavailable(
                f"Candidate {candidate.id} cannot be sent Tasks: this Workspace was opened "
                "without Candidate Adapters (open it with bench.wiring.open_workspace)"
            )
        return self.candidate_adapters(candidate)

    def judge_run(self, run: Run, *, allow_uncertified: bool = False) -> tuple[TaskJudging, ...]:
        """Score every Task of a finished Run whose Output is not yet scored.

        Each judge model sees the input, the Rubric, the Gold Answer and the final
        Output, never the Candidate. A Panel's median per Criterion becomes the Criterion
        scores; every judge model's scores and reasons are saved (Run.record_judging).

        Every refusal is a JudgingError, raised before any judge model is called: a Run
        with pending Tasks (RunNotFinished); no Judge Setup, or a judge model that
        cannot be made, its API key missing included (JudgeUnavailable); an uncertified
        Judge Setup without allow_uncertified (JudgeNotCertified); a Run already judged
        by another Judge Setup (JudgeSetupMismatch); a judge model that is the model the
        Candidate calls (JudgeRefused); a Private Task to a Judge Setup without private
        Clearance (JudgeClearanceError). A certified Judge Setup (certify_judge) needs no
        override and logs none.

        For an uncertified Judge Setup the override is appended to the Decision Log once
        the first Task is judged, and only once per Run and Judge Setup, so a resumed
        judging logs nothing new. A malformed reply raises JudgeReplyError; Tasks judged
        before it stay recorded, so judging again resumes. A same-family judge model
        marks every Task for a full human check. Once every Task is judged, the Run's
        Spot-check queue is sampled (bench.core.spot_checks), once.
        """
        setup = self._require_judge_setup()
        if run.pending:
            raise RunNotFinished(
                f"Run {run.id} is not finished: {', '.join(run.pending)} still pending; "
                "judge it once every Task's Output is recorded"
            )
        is_certified = certified(self.root, setup)
        if not is_certified and not allow_uncertified:
            raise JudgeNotCertified(
                f"Judge Setup {setup.hash} is not certified; recalibrate it on the "
                "Calibration Set and certify it, or pass allow_uncertified=True to judge "
                "anyway, which is logged to the Decision Log"
            )
        run.check_judge_setup(setup)
        candidate = run.candidate
        provider, model = provider_for(candidate), model_for(candidate)
        for spec in setup.models:
            if is_candidate_under_test(spec, provider, model):
                raise JudgeRefused(
                    f"judge model {spec.label} is the model Candidate {candidate.id} "
                    "calls; a model never judges its own Output"
                )
        tasks = [run.task(task_id) for task_id in run.unjudged]
        for task in tasks:  # before any content is read
            check_judge_clearance(setup, task.visibility, f"Task {task.id}")
        models = {spec.label: self._judge_model(spec) for spec in setup.models}
        archive_judge_prompt(self.root, setup.prompt)  # rebuildable later (ADR-0003)
        family = any(same_family(spec, candidate.tool) for spec in setup.models)
        judged = []
        for task in tasks:
            blind = blind_output(task.id, task, run.final_output(task.id))
            judging = _judging(blind, setup, models, same_family=family,
                               where=f"Task {task.id}")
            run.record_judging(judging, setup)
            if not is_certified and not override_logged(self.root, run.id, setup.hash):
                log_override(self.root, self.clock.today(), run.id, candidate.id, setup.hash)
            judged.append(judging)
        self._queue_spot_checks(run)
        return tuple(judged)

    def _queue_spot_checks(self, run: Run) -> None:
        """Once every Task is judged, sample the Run's Spot-check queue (set only once)."""
        if run.unjudged or run.judge_setup_hash is None:
            return
        run.queue_spot_checks(spot_check_queue(
            run.id,
            run.tasks,
            self.settings.spot_check_rate,
            covered_before=tracks_spot_checked_before(self.root, run),
            full_checks=run.full_human_checks,
        ))

    def spot_check_item(self, run: Run, task_id: str) -> BlindOutput:
        """What a human Spot-checking a queued Task of a judged Run sees: its input, Gold
        Answer, final Output and the Criteria still to check; never the Judge's scores,
        the Run or the Candidate. id is the Task id."""
        task = run.task(task_id)
        check_clearance(run.candidate, task)  # the Run's Clearance, before any content
        left = run.spot_checks_left.get(task.id, ())
        criteria = tuple(c for c in task.rubric.criteria if c.id in left)
        return blind_output(task.id, task, run.final_output(task.id), criteria)

    def record_spot_checks(
        self, run: Run, task_id: str, scores: Mapping[str, Any]
    ) -> tuple[SpotCheck, ...]:
        """Record a human's 1-5 Spot-check score of each Criterion in scores for a judged
        Task of the Run. Every score is checked before any is recorded: one refused
        raises SpotCheckError naming it, and nothing is recorded."""
        for criterion, score in scores.items():
            run.check_spot_check(task_id, criterion, score)
        return tuple(
            run.record_spot_check(task_id, criterion, score)
            for criterion, score in scores.items()
        )

    @property
    def calibration_set(self) -> CalibrationSet:
        return CalibrationSet(self.root, self.settings.private_roots)

    def add_calibration_item(
        self, run_id: str, task_id: str, *, on: datetime.date | None = None
    ) -> CalibrationItem:
        """Add a finished Run's final Output of a Task to the Calibration Set, Spot-checked
        or not (stories 40, 48). A Private Task's content goes to its private root and
        the public record keeps numbers only (ADR-0006). An unfinished Run, or an Output
        already in the set, raises CalibrationError."""
        run = self.resume_run(run_id)
        return self.calibration_set.add(run, task_id, on or self.clock.today())

    def calibration_items(self) -> tuple[CalibrationItem, ...]:
        """Every Calibration Set item's numbers, oldest first."""
        return self.calibration_set.items()

    def unscored_calibration_items(self) -> tuple[BlindOutput, ...]:
        """The items still awaiting a human score, as the human sees them: content only,
        never the Judge's scores, the Run or the Candidate."""
        cal = self.calibration_set
        return tuple(cal.blind(i.id) for i in cal.items() if not i.human_scored)

    def score_calibration_item(self, item_id: str, scores: Mapping[str, int]) -> CalibrationItem:
        """Record the human's blind score, a whole number 1-5, of every Criterion of a
        Calibration Set item; scoring it again replaces the earlier scores. Scores not
        matching its Criteria raise CalibrationError and change nothing."""
        return self.calibration_set.record_human_scores(item_id, scores)

    def judge_calibration_set(self) -> tuple[TaskJudging, ...]:
        """Have the Judge Setup score every Calibration Set item it has not scored yet.

        The Judge sees what it sees in a Run (input, Rubric, Gold Answer, final Output),
        never the human scores or the Candidate; TaskJudging.output_id is the item id.
        Refusals are raised before any judge model is called: no Judge Setup or judge
        models (JudgeUnavailable), a private item to a Judge Setup without private
        Clearance (JudgeClearanceError). Items scored before a malformed reply stay
        recorded, so judging again resumes.
        """
        setup = self._require_judge_setup()
        cal = self.calibration_set
        items = [i for i in cal.items() if setup.hash not in i.judge_scores]
        for item in items:
            check_judge_clearance(setup, item.visibility, f"calibration item {item.id}")
        models = {spec.label: self._judge_model(spec) for spec in setup.models}
        archive_judge_prompt(self.root, setup.prompt)
        judged = []
        for item in items:
            judging = _judging(cal.blind(item.id), setup, models, same_family=False,
                               where=f"calibration item {item.id}")
            cal.record_judging(item.id, judging, setup)
            judged.append(judging)
        return tuple(judged)

    def calibration_agreement(self) -> Agreement:
        """The current Judge Setup's agreement with the human scores on the Calibration
        Set: per Criterion and pooled, the share within 1 point and the quadratic
        weighted kappa, against the configured thresholds."""
        setup = self._require_judge_setup()
        thresholds = self.settings.calibration
        return self.calibration_set.agreement(
            setup.hash, thresholds.within_one_point, thresholds.weighted_kappa,
            thresholds.min_items,
        )

    @property
    def judge_certified(self) -> bool:
        """Whether the current Judge Setup is certified: a Certification of its hash at
        its Clearance is in the Decision Log (ADR-0002). Editing its mode, models or
        prompt makes a new hash, uncertified until it is calibrated and certified itself
        (story 44), and widening its Clearance to Private Tasks drops it. False with no
        Judge Setup."""
        return self.judge_setup is not None and certified(self.root, self.judge_setup)

    def certify_judge(self, *, on: datetime.date | None = None) -> Agreement:
        """Certify the current Judge Setup on the Calibration Set and log it to the
        Decision Log; returns the agreement it was certified on.

        Every item must have a human score and this Judge Setup's score, there must be at
        least calibration.min_items of them (default 12), and every Criterion must meet
        the configured thresholds (calibration.within_one_point, weighted_kappa);
        otherwise CertificationFailed names each shortfall and nothing is logged. The
        Certification records the Judge Setup's Clearance (ADR-0002). Certifying a
        certified Judge Setup logs nothing new.
        """
        setup = self._require_judge_setup()
        agreement = self.calibration_agreement()
        if not agreement.meets_thresholds:
            raise CertificationFailed(
                f"Judge Setup {setup.hash} is not certified: " + "; ".join(agreement.shortfalls)
            )
        if not self.judge_certified:
            item_ids = tuple(i.id for i in self.calibration_set.items())
            archive_judge_prompt(self.root, setup.prompt)
            log_certification(self.root, on or self.clock.today(), setup, agreement, item_ids,
                              archive_rubric(self.root, self.rubric))
        return agreement

    def _require_judge_setup(self) -> JudgeSetup:
        if self.judge_setup is None:
            raise JudgeUnavailable(f"no Judge Setup: {JUDGE_FILE} is missing")
        return self.judge_setup

    def _judge_model(self, spec: JudgeModelSpec) -> JudgeModel:
        if self.judges is None:
            raise JudgeUnavailable(
                f"judge model {spec.label} cannot be made: this Workspace was opened "
                "without judge models (open it with bench.wiring.open_workspace)"
            )
        try:
            return self.judges(spec)
        except MissingSecret as exc:
            raise JudgeUnavailable(f"judge model {spec.label}: {exc}") from exc

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

    def update_settings(self, **changes: Any) -> Settings:
        """Change settings and write config/settings.yaml back (ADR-0001).

        changes are among tracks, weights, regression_margin, retest_margin,
        spot_check_rate, calibration and normalization; a mapping merges into the
        current one by key, so weights={"quality": 40, "edit_distance": 30} changes two
        weights. The result is validated as on load: an invalid change, weights not
        summing to 100 included, raises ConfigError naming the field and writes nothing.
        Private roots are machine-specific (config/local.yaml) and not edited here.

        Removing (or renaming) a Track that a Task, or a Task version a Run used, still
        declares raises ConfigEditRefused naming them, since that Run could no longer
        load.
        """
        self.settings = edit_settings(self.root, changes, self._tracks_in_use())
        return self.settings

    def set_price(
        self, model: str, *, input_per_mtok: float, output_per_mtok: float
    ) -> PriceTable:
        """Add or change a model's token prices (USD per million tokens) in
        config/prices.yaml. The model is the exact string a Candidate uses. An invalid
        price raises ConfigError naming the field and writes nothing."""
        self.prices = set_price(self.root, model, input_per_mtok, output_per_mtok)
        return self.prices

    def remove_price(self, model: str) -> PriceTable:
        """Remove a model from the price table; ConfigError if it has no price. A Run's
        recorded costs are kept: cost is computed when a Task is sent."""
        self.prices = remove_price(self.root, model)
        return self.prices

    def update_judge_setup(
        self,
        *,
        mode: str | None = None,
        models: Iterable[Mapping[str, Any]] | None = None,
        clearance: str | None = None,
        prompt_file: str | None = None,
        prompt: str | None = None,
    ) -> JudgeSetup:
        """Change the Judge Setup in config/judge.yaml, creating it if absent.

        mode is single or panel; models each {provider, model} with optional family and
        env_var, replacing the current list; clearance public_only or
        public_and_private; prompt_file the judge prompt's path relative to the
        Workspace root; prompt the new text of the prompt file. Validated as on load:
        an invalid change raises ConfigError naming the field and writes nothing.

        Changing the mode, the models or the prompt text makes a new Judge Setup with a
        new hash, which has no Certification (ADR-0003); clearance and env_var are not
        part of the hash, but widening clearance to public_and_private drops the
        Certification (ADR-0002). Runs keep the hash of the Judge Setup that judged them.
        """
        given = {"mode": mode, "clearance": clearance, "prompt": prompt_file,
                 "models": None if models is None else [dict(m) for m in models]}
        changes = {k: v for k, v in given.items() if v is not None}
        self.judge_setup = edit_judge_setup(self.root, changes, prompt)
        return self.judge_setup

    def update_rubric(self, criteria: Iterable[Mapping[str, Any]]) -> Rubric:
        """Replace the shared Rubric's Criteria, each {id, name, description}, in order:
        adding, editing or removing a shared Criterion (story 63).

        Validated as on load (ConfigError naming the field), and every Task must still
        load with it: a change that would leave a Task invalid, such as a shared Criterion
        a Task already defines, raises ConfigEditRefused naming the Tasks. Either way
        nothing is written. The Rubric being replaced is archived by hash first, so a Run
        keeps scoring on the Rubric it started with (ADR-0003).
        """
        rubric = parse_rubric_edit(list(criteria))
        ctx = TaskContext(self.root, self.settings.private_roots, rubric, self.settings.tracks)
        try:
            load_tasks(ctx)
        except ConfigError as exc:
            raise ConfigEditRefused(
                RUBRIC_FILE, "criteria", f"would leave a Task invalid: {exc}"
            ) from None
        archive_rubric(self.root, self.rubric)
        write_rubric(self.root, rubric)
        self.rubric = rubric
        self._reload_tasks()
        return rubric

    def run_rubric(self, run: Run) -> Rubric | None:
        """The shared Rubric the Run was started on, rebuilt from the archive
        (ADR-0003); None for a Run started before Runs recorded it."""
        return None if run.rubric_hash is None else self.archived_rubric(run.rubric_hash)

    def run_judge_prompt(self, run: Run) -> str | None:
        """The judge prompt that scored the Run, rebuilt from the archive (ADR-0003);
        None until it is judged."""
        digest = run.judge_prompt_hash
        return None if digest is None else self.archived_judge_prompt(digest)

    def archived_rubric(self, digest: str) -> Rubric | None:
        """A shared Rubric by its hash, as a Run or a Certification records it."""
        return archived_rubric(self.root, digest)

    def archived_judge_prompt(self, digest: str) -> str | None:
        """A judge prompt's text by its hash, as a Run or a Certification records it."""
        return archived_judge_prompt(self.root, digest)

    def _tracks_in_use(self) -> dict[str, list[str]]:
        """Each Track a loaded Task or a Run's Task version declares -> what declares it."""
        used: dict[str, list[str]] = {}
        for task in self.tasks:
            used.setdefault(task.track, []).append(f"Task {task.id}")
        for task_id, version in sorted(task_versions_used(self.root)):
            task = find_by_id(self.tasks, task_id)
            if task is None or task.version == version:
                continue
            old = load_version(self.task_context, task, version)
            if old is not None:
                used.setdefault(old.track, []).append(f"Task {task_id} version {version}")
        return used

    def score(self, results: Iterable[TaskResult]) -> RunScore:
        """Score a Run's per-Task result records with this Workspace's weights and bands."""
        return score_run(self.settings, results)

    def compare(self, challenger_run_id: str, baseline_run_id: str) -> Comparison:
        """Compare a challenger Run with a Baseline Run and reach a Verdict.

        Spot-checks recorded on either Run (Run.record_spot_check) are weighed too, and
        a Run with queued Spot-checks left (Run.spot_checks_left) is refused
        (SpotChecksUnfinished). A Run scored from results files (human Criterion scores,
        never judged) has no queue and needs none.
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
        return log_verdict(self.root, verdict, on or self.clock.today(), judge_setup_hash)

    def log_comparison(
        self,
        comparison: Comparison,
        *,
        on: datetime.date | None = None,
        judge_setup_hash: str | None = None,
    ) -> Path:
        """Append a Comparison's Verdict to the Decision Log with the scorecard it was
        reached on (story 59), so the entry can be shown as logged; judge_setup_hash
        defaults to the Judge Setup that scored both Runs."""
        return log_verdict(
            self.root, comparison.verdict, on or self.clock.today(),
            judge_setup_hash or comparison.judge_setup_hash, comparison.scorecard,
        )

    def decision_log(self) -> tuple[dict[str, Any], ...]:
        """Every Decision Log entry, oldest first, as written to decisions/."""
        return read_log(self.root)


def _judging(
    blind: BlindOutput,
    setup: JudgeSetup,
    models: Mapping[str, JudgeModel],
    *,
    same_family: bool,
    where: str,
) -> TaskJudging:
    """Every judge model's parsed reply to the Judge Setup's request for the Output;
    where names it in a malformed reply's error."""
    request = JudgeRequest.for_output(setup.prompt, blind)
    return TaskJudging(
        output_id=blind.id,
        judges={
            label: parse_reply(model.judge(request), request.criteria,
                               f"judge model {label} on {where}")
            for label, model in models.items()
        },
        same_family=same_family,
    )
