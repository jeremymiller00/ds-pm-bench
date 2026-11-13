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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.candidates import Candidate
from bench.core.tasks import Task

RUNS_FOLDER = "runs"
RUN_FILE = "run.yaml"

SUITES = ("public_only", "public_and_private")


@dataclass
class Run:
    id: str
    candidate_id: str
    date: datetime.date
    suite: str  # "public_only" or "public_and_private"
    task_versions: dict[str, int]
    folder: Path  # runs/<id>/ in the public repo


def run_id(candidate: Candidate, date: datetime.date) -> str:
    return f"{date.isoformat()}-{candidate.id}"


def create_run(
    workspace_root: Path, candidate: Candidate, tasks: tuple[Task, ...], date: datetime.date
) -> Run:
    rid = run_id(candidate, date)
    folder = workspace_root / RUNS_FOLDER / rid
    suite = (
        "public_and_private" if any(t.visibility == "private" for t in tasks) else "public_only"
    )
    run = Run(
        id=rid,
        candidate_id=candidate.id,
        date=date,
        suite=suite,
        task_versions={t.id: t.version for t in tasks},
        folder=folder,
    )
    folder.mkdir(parents=True)
    (folder / RUN_FILE).write_text(yaml.safe_dump(_snapshot(run, candidate), sort_keys=False))
    return run


def _snapshot(run: Run, candidate: Candidate) -> dict[str, Any]:
    return {
        "id": run.id,
        "date": run.date.isoformat(),
        "suite": run.suite,
        "candidate": {
            "id": candidate.id,
            "tool": candidate.tool,
            "version": candidate.version,
            "adapter": candidate.adapter,
            "settings": candidate.settings,
        },
        "tasks": run.task_versions,
    }
