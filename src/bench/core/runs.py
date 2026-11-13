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
from bench.core.errors import ClearanceError, GateNotPassed, UnknownTaskError
from bench.core.tasks import Task

RUNS_FOLDER = "runs"
RUN_FILE = "run.yaml"

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
