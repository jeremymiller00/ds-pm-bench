"""The Decision Log: decisions/NNNN-<date>-<candidate>.yaml, one dated entry per Verdict.

Append-only plain files (ADR-0001): each entry gets the next sequence number and is
created exclusively, so an existing entry is never rewritten. A superseding Verdict
is a new entry, not an edit.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import yaml

from bench.core.verdicts import Verdict

DECISIONS_FOLDER = "decisions"

_ENTRY = re.compile(r"^(\d{4,})-")


def log_verdict(
    root: Path, verdict: Verdict, date: datetime.date, judge_setup_hash: str | None
) -> Path:
    folder = root / DECISIONS_FOLDER
    folder.mkdir(exist_ok=True)
    numbers = [int(m.group(1)) for p in _entries(folder) if (m := _ENTRY.match(p.name))]
    number = max(numbers, default=0) + 1
    path = folder / f"{number:04d}-{date.isoformat()}-{verdict.candidate_id}.yaml"
    with path.open("x") as file:  # never overwrite an entry
        yaml.safe_dump(_entry(verdict, date, judge_setup_hash), file, sort_keys=False)
    return path


def read_log(root: Path) -> tuple[dict[str, Any], ...]:
    """Every entry, oldest first."""
    folder = root / DECISIONS_FOLDER
    if not folder.is_dir():
        return ()
    return tuple(yaml.safe_load(p.read_text()) for p in _entries(folder))


def _entries(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.glob("*.yaml") if _ENTRY.match(p.name)),
        key=lambda p: int(_ENTRY.match(p.name).group(1)),
    )


def _entry(
    verdict: Verdict, date: datetime.date, judge_setup_hash: str | None
) -> dict[str, Any]:
    return {
        "date": date.isoformat(),
        "candidate": {"id": verdict.candidate_id, "version": verdict.candidate_version},
        "baseline": {"id": verdict.baseline_id, "version": verdict.baseline_version},
        "verdict": verdict.outcome,
        "label": verdict.label,
        "full_adopt": verdict.full_adopt,
        "suite": verdict.suite,
        "private_tasks": verdict.private_tasks,
        "judge_setup_hash": judge_setup_hash,
        "rationale": list(verdict.rationale),
        "regressions": list(verdict.regressions),
        "warnings": list(verdict.warnings),
        "runs": {"challenger": verdict.challenger_run_id, "baseline": verdict.baseline_run_id},
    }
