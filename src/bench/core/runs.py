"""Runs: one Candidate against a Suite of Tasks on one date (ADR-0003, ADR-0006).

runs/<date>-<candidate>/ in the public repo holds:
  run.yaml              the snapshot: Candidate, Task versions, date, Suite
  results/<task>.yaml   numbers only per Task (timings, cost, Edit Distance, Criterion
                        scores), with the Task's visibility
  raw/<task>.md         raw Output       } Public Tasks only; for a Private Task these
  final/<task>.md       edited final     } go under <private root>/runs/<run id>/
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bench.core.candidates import Candidate
from collections.abc import Mapping

from bench.core.errors import (
    ClearanceError,
    GateNotPassed,
    RunExists,
    RunIncomplete,
    RunMismatch,
    UnknownRunError,
    UnknownTaskError,
)
from bench.core.results import TaskResult
from bench.core.tasks import Task

RUNS_FOLDER = "runs"
RUN_FILE = "run.yaml"
RESULTS_FOLDER = "results"

PRIVATE_CLEARANCE = "public_and_private"


@dataclass(frozen=True)
class Run:
    id: str
    candidate: Candidate
    date: datetime.date
    suite: str  # "public_only" or "public_and_private"
    tasks: tuple[Task, ...]  # the Suite, at the versions this Run uses
    folder: Path  # runs/<id>/ in the public repo
    # Every Task the Workspace loaded, so a request for one outside the Suite is
    # refused for the right reason (Clearance) rather than as unknown.
    workspace_tasks: tuple[Task, ...] = field(default=(), repr=False)

    @property
    def candidate_id(self) -> str:
        return self.candidate.id

    @property
    def task_versions(self) -> dict[str, int]:
        return {t.id: t.version for t in self.tasks}

    def task(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise UnknownTaskError(f"Task {task_id!r} is not in Run {self.id}")

    def task_input(self, task_id: str) -> str:
        """What is sent to (or shown for) the Candidate: the Canonical Prompt, then the input."""
        for task in self.workspace_tasks:
            if task.id == task_id:
                check_clearance(self.candidate, task)  # before any content is read
        task = self.task(task_id)
        return f"{task.canonical_prompt}\n\n{task.input_path.read_text().strip()}"


    def content_folder(self, task: Task) -> Path:
        """Where a Task's content-bearing files go: the private root for a Private Task."""
        if task.visibility == "private":
            private_root = task.folder.parent.parent  # <private root>/tasks/<id>
            return private_root / RUNS_FOLDER / self.id
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
        if self._numbers(task.id) is not None:
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
        numbers = {
            "task": task.id,
            "version": task.version,
            "visibility": task.visibility,
            "edit_distance": record.edit_distance,
            "active_seconds": active_seconds,
            "latency_seconds": latency_seconds,
            "cost_usd": cost_usd,
        }
        (self.folder / RESULTS_FOLDER).mkdir(exist_ok=True)
        _results_file(self, task.id).write_text(yaml.safe_dump(numbers, sort_keys=False))
        return record

    def record_criterion_scores(self, task_id: str, scores: Mapping[str, float]) -> None:
        """Save the Judge's 1-5 Criterion scores for a Task whose Output is recorded."""
        task = self.task(task_id)
        numbers = self._numbers(task.id)
        if numbers is None:
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
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Task {task.id}: {cid} must be a 1-5 number, got {value!r}")
            if not 1 <= value <= 5:
                raise ValueError(f"Task {task.id}: {cid} must be 1-5, got {value}")
        numbers["criterion_scores"] = dict(scores)
        _results_file(self, task.id).write_text(yaml.safe_dump(numbers, sort_keys=False))

    @property
    def pending(self) -> tuple[str, ...]:
        """Task ids in the Suite whose Output is not recorded yet, in Suite order."""
        return tuple(t.id for t in self.tasks if self._numbers(t.id) is None)

    def task_results(self) -> tuple[TaskResult, ...]:
        """One TaskResult per Task, for Workspace.score. Refuses an incomplete Run."""
        missing = []
        for task in self.tasks:
            numbers = self._numbers(task.id)
            if numbers is None:
                missing.append(f"{task.id} has no Output")
            elif "criterion_scores" not in numbers:
                missing.append(f"{task.id} has no Criterion scores")
        if missing:
            raise RunIncomplete(f"Run {self.id} is incomplete: {'; '.join(missing)}")
        results = []
        for task in self.tasks:
            n = self._numbers(task.id)
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

    def _numbers(self, task_id: str) -> dict[str, Any] | None:
        path = _results_file(self, task_id)
        return yaml.safe_load(path.read_text()) if path.is_file() else None


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


def _results_file(run: Run, task_id: str) -> Path:
    return run.folder / RESULTS_FOLDER / f"{task_id}.yaml"


def run_id(candidate: Candidate, date: datetime.date) -> str:
    return f"{date.isoformat()}-{candidate.id}"


def check_gate(candidate: Candidate) -> None:
    if not candidate.gate_passed:
        raise GateNotPassed(f"Candidate {candidate.id} has no passing Gate, so it cannot be Run")


def check_clearance(candidate: Candidate, task: Task) -> None:
    """Call before reading any of a Private Task's content on the Candidate's behalf."""
    if task.visibility == "private" and candidate.clearance != PRIVATE_CLEARANCE:
        raise ClearanceError(
            f"Task {task.id} is private and Candidate {candidate.id} is cleared for "
            f"{candidate.clearance or 'nothing'}"
        )


def suite_for(candidate: Candidate, tasks: tuple[Task, ...]) -> tuple[Task, ...]:
    """Every Task the Candidate's Clearance allows."""
    if candidate.clearance == PRIVATE_CLEARANCE:
        return tasks
    return tuple(t for t in tasks if t.visibility == "public")


def create_run(
    workspace_root: Path,
    candidate: Candidate,
    suite: tuple[Task, ...],
    date: datetime.date,
    workspace_tasks: tuple[Task, ...],
) -> Run:
    check_gate(candidate)
    for task in suite:
        check_clearance(candidate, task)
    rid = run_id(candidate, date)
    if (workspace_root / RUNS_FOLDER / rid).exists():
        raise RunExists(f"Run {rid} already exists; resume it instead")
    run = Run(
        id=rid,
        candidate=candidate,
        date=date,
        suite=(
            PRIVATE_CLEARANCE
            if any(t.visibility == "private" for t in suite)
            else "public_only"
        ),
        tasks=suite,
        folder=workspace_root / RUNS_FOLDER / rid,
        workspace_tasks=workspace_tasks,
    )
    run.folder.mkdir(parents=True)
    (run.folder / RUN_FILE).write_text(yaml.safe_dump(_snapshot(run), sort_keys=False))
    return run


def _snapshot(run: Run) -> dict[str, Any]:
    c = run.candidate
    return {
        "id": run.id,
        "date": run.date.isoformat(),
        "suite": run.suite,
        "candidate": {
            "id": c.id,
            "tool": c.tool,
            "version": c.version,
            "adapter": c.adapter,
            "settings": c.settings,
        },
        "tasks": run.task_versions,
    }


def open_run(
    workspace_root: Path,
    rid: str,
    candidates: Mapping[str, Candidate],
    workspace_tasks: tuple[Task, ...],
) -> Run:
    """Load a Run from its snapshot, refusing if anything it used has changed (ADR-0003)."""
    folder = workspace_root / RUNS_FOLDER / rid
    if not (folder / RUN_FILE).is_file():
        raise UnknownRunError(f"no Run {rid!r} in {RUNS_FOLDER}/")
    snapshot = yaml.safe_load((folder / RUN_FILE).read_text())
    snapped = snapshot["candidate"]
    candidate = candidates.get(snapped["id"])
    if candidate is None or (candidate.version, candidate.settings) != (
        snapped["version"],
        snapped["settings"],
    ):
        raise RunMismatch(f"Run {rid}: Candidate {snapped['id']} is missing or has changed")
    check_gate(candidate)
    by_id = {t.id: t for t in workspace_tasks}
    suite = []
    for task_id, version in snapshot["tasks"].items():
        task = by_id.get(task_id)
        if task is None or task.version != version:
            found = "missing" if task is None else f"now version {task.version}"
            raise RunMismatch(f"Run {rid} used Task {task_id} version {version}, {found}")
        check_clearance(candidate, task)
        suite.append(task)
    return Run(
        id=rid,
        candidate=candidate,
        date=datetime.date.fromisoformat(snapshot["date"]),
        suite=snapshot["suite"],
        tasks=tuple(suite),
        folder=folder,
        workspace_tasks=workspace_tasks,
    )
