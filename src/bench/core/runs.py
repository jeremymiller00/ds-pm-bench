"""Runs: one Candidate against a Suite of Tasks on one date (ADR-0003, ADR-0006).

runs/<date>-<candidate>/ in the public repo holds:
  run.yaml              the snapshot: Candidate, Task versions, date, Suite, and the
                        Tasks left out of the Suite as placeholders (excluded)
  results/<task>.yaml   numbers only per Task (timings, cost, Edit Distance, Criterion
                        scores), with the Task's visibility
  raw/<task>.md         raw Output       } Public Tasks only; for a Private Task these
  final/<task>.md       edited final     } go under <private root>/runs/<run id>/
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bench.core.candidates import Candidate
from bench.core.errors import (
    ClearanceError,
    GateNotPassed,
    RunExists,
    RunIncomplete,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
    UnknownTaskError,
)
from bench.core.fields import FieldReader
from bench.core.kinds import Clearance, Suite, Visibility
from bench.core.lookup import find_by_id
from bench.core.results import TaskResult
from bench.core.rubric import check_criterion_score
from bench.core.tasks import Task

RUNS_FOLDER = "runs"
RUN_FILE = "run.yaml"
RESULTS_FOLDER = "results"

# What a results file (the input to record_from_file) may hold.
RESULTS_FILE_FIELDS = (
    "raw",
    "final",
    "active_seconds",
    "latency_seconds",
    "cost_usd",
    "criterion_scores",
)


@dataclass(frozen=True)
class Run:
    id: str
    candidate: Candidate
    date: datetime.date
    suite: Suite
    tasks: tuple[Task, ...]  # the Suite, at the versions this Run uses
    folder: Path  # runs/<id>/ in the public repo
    # Tasks the Suite would cover but left out because they still hold a placeholder:
    # Task id -> what is a placeholder ("input", "Gold Answer", "Manual Time").
    excluded: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def candidate_id(self) -> str:
        return self.candidate.id

    @property
    def task_versions(self) -> dict[str, int]:
        return {t.id: t.version for t in self.tasks}

    def task(self, task_id: str) -> Task:
        task = find_by_id(self.tasks, task_id)
        if task is None:
            raise UnknownTaskError(f"Task {task_id!r} is not in Run {self.id}")
        return task

    def task_input(self, task_id: str) -> str:
        """What is sent to (or shown for) the Candidate: the Canonical Prompt, then the input.

        Only for a Task in the Suite; Workspace.task_input tells a Task the Candidate is
        not cleared for apart from one that does not exist.
        """
        task = self.task(task_id)
        check_clearance(self.candidate, task)  # before any content is read
        return f"{task.canonical_prompt}\n\n{task.input_path.read_text().strip()}"

    def content_folder(self, task: Task) -> Path:
        """Where a Task's content-bearing files go: the private root for a Private Task."""
        if task.visibility == Visibility.PRIVATE:
            return task.root / RUNS_FOLDER / self.id
        return self.folder

    def record_output(
        self,
        task_id: str,
        *,
        raw: str,
        final: str,
        active_seconds: float,
        latency_seconds: float,
        cost_usd: float | None,
    ) -> OutputRecord:
        """Save a Task's raw Output and edited final with its timings and cost.

        active_seconds is the PM's whole active time on the Task (prompting plus editing);
        latency_seconds is the time to first draft.
        """
        task = self.task(task_id)
        if self._task_record(task.id) is not None:
            raise ValueError(f"Run {self.id}: Task {task.id}'s Output is already recorded")
        record = OutputRecord(
            task_id=task.id,
            edit_distance=edit_distance(raw, final),
            active_seconds=active_seconds,
            latency_seconds=latency_seconds,
            cost_usd=cost_usd,
        )
        content = self.content_folder(task)
        for name, text in (("raw", raw), ("final", final)):
            (content / name).mkdir(parents=True, exist_ok=True)
            (content / name / f"{task.id}.md").write_text(text)
        task_record = {
            "task": task.id,
            "version": task.version,
            "visibility": str(task.visibility),
            "edit_distance": record.edit_distance,
            "active_seconds": active_seconds,
            "latency_seconds": latency_seconds,
            "cost_usd": cost_usd,
        }
        self._write_task_record(task.id, task_record)
        return record

    def record_criterion_scores(self, task_id: str, scores: Mapping[str, float]) -> None:
        """Save the Judge's 1-5 Criterion scores for a Task whose Output is recorded."""
        task = self.task(task_id)
        task_record = self._task_record(task.id)
        if task_record is None:
            raise ValueError(f"Run {self.id}: Task {task.id} has no Output to score yet")
        expected = {c.id for c in task.rubric.criteria}
        if set(scores) != expected:
            missing = sorted(expected - set(scores))
            extra = sorted(set(scores) - expected)
            raise ValueError(
                f"Task {task.id}: Criterion scores must match its Rubric; "
                f"missing {missing}, unknown {extra}"
            )
        for cid, value in scores.items():
            check_criterion_score(value, f"Task {task.id}: {cid}")
        task_record["criterion_scores"] = dict(scores)
        self._write_task_record(task.id, task_record)

    def record_from_file(self, path: Path) -> None:
        """Record one Task's Output, and its Criterion scores if present, from a results file.

        The file is <task id>.yaml holding raw, final, active_seconds, latency_seconds,
        cost_usd (null or absent when there is no per-Task cost) and, optionally,
        criterion_scores.
        """
        task_id = path.stem
        f = FieldReader(str(path))
        data = f.mapping(_load_yaml(f, path), "(file)")
        for key in data:
            if key not in RESULTS_FILE_FIELDS:
                raise f.fail(str(key), f"unknown, expected one of {', '.join(RESULTS_FILE_FIELDS)}")
        for key in ("raw", "final", "active_seconds", "latency_seconds"):
            if key not in data:
                raise f.fail(key, "is missing")
        self.record_output(
            task_id,
            raw=str(data["raw"]),
            final=str(data["final"]),
            active_seconds=f.number(data["active_seconds"], "active_seconds", minimum=0),
            latency_seconds=f.number(data["latency_seconds"], "latency_seconds", minimum=0),
            cost_usd=(
                None
                if data.get("cost_usd") is None
                else f.number(data["cost_usd"], "cost_usd", minimum=0)
            ),
        )
        if "criterion_scores" in data:
            scores = f.mapping(data["criterion_scores"], "criterion_scores")
            self.record_criterion_scores(task_id, scores)

    def record_from_folder(self, folder: Path) -> tuple[str, ...]:
        """Record every pending Task that has a <task id>.yaml in folder; returns their ids."""
        recorded = []
        for task_id in self.pending:
            path = folder / f"{task_id}.yaml"
            if path.is_file():
                self.record_from_file(path)
                recorded.append(task_id)
        return tuple(recorded)

    @property
    def pending(self) -> tuple[str, ...]:
        """Task ids in the Suite whose Output is not recorded yet, in Suite order."""
        return tuple(t.id for t in self.tasks if self._task_record(t.id) is None)

    def task_results(self) -> tuple[TaskResult, ...]:
        """One TaskResult per Task, for Workspace.score. Refuses an incomplete Run."""
        missing = []
        for task in self.tasks:
            task_record = self._task_record(task.id)
            if task_record is None:
                missing.append(f"{task.id} has no Output")
            elif "criterion_scores" not in task_record:
                missing.append(f"{task.id} has no Criterion scores")
        if missing:
            raise RunIncomplete(f"Run {self.id} is incomplete: {'; '.join(missing)}")
        results = []
        for task in self.tasks:
            n = self._task_record(task.id)
            results.append(
                TaskResult(
                    task_id=task.id,
                    track=task.track,
                    criterion_scores=n["criterion_scores"],
                    edit_distance=n["edit_distance"],
                    active_seconds=n["active_seconds"],
                    manual_minutes=task.manual_time_minutes,
                    cost_usd=n["cost_usd"],
                    latency_seconds=n["latency_seconds"],
                )
            )
        return tuple(results)

    def _task_record(self, task_id: str) -> dict[str, Any] | None:
        """The Task's results/<task>.yaml (numbers only), or None before its Output."""
        path = self._results_file(task_id)
        return yaml.safe_load(path.read_text()) if path.is_file() else None

    def _write_task_record(self, task_id: str, task_record: dict[str, Any]) -> None:
        (self.folder / RESULTS_FOLDER).mkdir(exist_ok=True)
        self._results_file(task_id).write_text(yaml.safe_dump(task_record, sort_keys=False))

    def _results_file(self, task_id: str) -> Path:
        return self.folder / RESULTS_FOLDER / f"{task_id}.yaml"


@dataclass(frozen=True)
class OutputRecord:
    """The numbers recorded for one Task's Output. Never holds content."""

    task_id: str
    edit_distance: float
    active_seconds: float
    latency_seconds: float
    cost_usd: float | None


def edit_distance(raw: str, final: str) -> float:
    """Share of words changed: word-level Levenshtein distance over the longer word count."""
    a, b = raw.split(), final.split()
    if not a and not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other))
            )
        previous = current
    return previous[-1] / max(len(a), len(b))


def _load_yaml(f: FieldReader, path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise f.fail("(file)", f"is not valid YAML: {exc}") from exc


def run_id_for(candidate: Candidate, date: datetime.date) -> str:
    return f"{date.isoformat()}-{candidate.id}"


def check_gate(candidate: Candidate) -> None:
    if not candidate.gate_passed:
        raise GateNotPassed(f"Candidate {candidate.id} has no passing Gate, so it cannot be Run")


def check_clearance(candidate: Candidate, task: Task) -> None:
    """Call before reading any of a Private Task's content on the Candidate's behalf."""
    private = task.visibility == Visibility.PRIVATE
    if private and candidate.clearance != Clearance.PUBLIC_AND_PRIVATE:
        raise ClearanceError(
            f"Task {task.id} is private and Candidate {candidate.id} is cleared for "
            f"{candidate.clearance or 'nothing'}"
        )


def choose_suite(
    candidate: Candidate, requested: Suite | str | None, tasks: tuple[Task, ...]
) -> Suite:
    """The Suite a Run covers: the requested one, or by default all the Clearance allows.

    A Suite may narrow the Clearance to public only but never exceed it. A privately
    cleared Candidate with no Private Tasks loaded is refused rather than silently given
    a public-only Suite; ask for public_only explicitly.
    """
    cleared = Suite(candidate.clearance)
    suite = cleared if requested is None else Suite(requested)
    if suite == Suite.PUBLIC_AND_PRIVATE and cleared != Suite.PUBLIC_AND_PRIVATE:
        raise ClearanceError(
            f"Candidate {candidate.id} is cleared for {cleared}, not a {suite} Suite"
        )
    has_private = any(t.visibility == Visibility.PRIVATE for t in tasks)
    if suite == Suite.PUBLIC_AND_PRIVATE and not has_private:
        raise SuiteUnavailable(
            f"Candidate {candidate.id} is cleared for {suite} but no Private Tasks are "
            f"loaded (check private_roots); ask for a {Suite.PUBLIC_ONLY} Suite explicitly"
        )
    return suite


def tasks_in(suite: Suite, tasks: tuple[Task, ...]) -> tuple[Task, ...]:
    """The Tasks a Suite covers."""
    if suite == Suite.PUBLIC_AND_PRIVATE:
        return tasks
    return tuple(t for t in tasks if t.visibility == Visibility.PUBLIC)


def create_run(
    workspace_root: Path,
    candidate: Candidate,
    suite: Suite,
    tasks: tuple[Task, ...],
    date: datetime.date,
) -> Run:
    check_gate(candidate)
    for task in tasks:
        check_clearance(candidate, task)
    runnable = tuple(t for t in tasks if not t.placeholders)
    run_id = run_id_for(candidate, date)
    if (workspace_root / RUNS_FOLDER / run_id).exists():
        raise RunExists(f"Run {run_id} already exists; resume it instead")
    run = Run(
        id=run_id,
        candidate=candidate,
        date=date,
        suite=suite,
        tasks=runnable,
        folder=workspace_root / RUNS_FOLDER / run_id,
        excluded={t.id: t.placeholders for t in tasks if t.placeholders},
    )
    run.folder.mkdir(parents=True)
    (run.folder / RUN_FILE).write_text(yaml.safe_dump(_snapshot(run), sort_keys=False))
    return run


def _snapshot(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "date": run.date.isoformat(),
        "suite": str(run.suite),
        "candidate": _candidate_snapshot(run.candidate),
        "tasks": run.task_versions,
        "excluded": {task_id: list(missing) for task_id, missing in run.excluded.items()},
    }


def _candidate_snapshot(c: Candidate) -> dict[str, Any]:
    """What a Run freezes of its Candidate (ADR-0003): any change here is a new Candidate."""
    return {
        "id": c.id,
        "tool": c.tool,
        "version": c.version,
        "adapter": c.adapter,
        "settings": c.settings,
    }


def open_run(
    workspace_root: Path,
    run_id: str,
    candidates: Mapping[str, Candidate],
    workspace_tasks: tuple[Task, ...],
) -> Run:
    """Load a Run from its snapshot, refusing if anything it used has changed (ADR-0003)."""
    folder = workspace_root / RUNS_FOLDER / run_id
    if not (folder / RUN_FILE).is_file():
        raise UnknownRunError(f"no Run {run_id!r} in {RUNS_FOLDER}/")
    snapshot = yaml.safe_load((folder / RUN_FILE).read_text())
    snapped = snapshot["candidate"]
    candidate = candidates.get(snapped["id"])
    if candidate is None or _candidate_snapshot(candidate) != snapped:
        raise RunMismatch(f"Run {run_id}: Candidate {snapped['id']} is missing or has changed")
    check_gate(candidate)
    by_id = {t.id: t for t in workspace_tasks}
    tasks = []
    for task_id, version in snapshot["tasks"].items():
        task = by_id.get(task_id)
        if task is None or task.version != version:
            found = "missing" if task is None else f"now version {task.version}"
            raise RunMismatch(f"Run {run_id} used Task {task_id} version {version}, {found}")
        check_clearance(candidate, task)
        tasks.append(task)
    return Run(
        id=run_id,
        candidate=candidate,
        date=datetime.date.fromisoformat(snapshot["date"]),
        suite=Suite(snapshot["suite"]),
        tasks=tuple(tasks),
        folder=folder,
        excluded={
            task_id: tuple(missing)
            for task_id, missing in snapshot.get("excluded", {}).items()
        },
    )
