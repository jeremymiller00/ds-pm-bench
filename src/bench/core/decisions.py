"""The Decision Log: decisions/NNNN-<date>-<subject>.yaml, one dated entry per Verdict,
one per Judge Setup Certification, and one per exception, such as an uncertified-Judge
override (kind names which). The subject is the Candidate, or judge-<hash> for a
Certification.

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

from bench.core.calibration import Agreement, CriterionAgreement
from bench.core.judging import JudgeSetup
from bench.core.lookup import next_number
from bench.core.verdicts import Verdict

DECISIONS_FOLDER = "decisions"

_ENTRY = re.compile(r"^(\d{4,})-")


def log_verdict(
    root: Path, verdict: Verdict, date: datetime.date, judge_setup_hash: str | None
) -> Path:
    return _append(root, date, verdict.candidate_id, _entry(verdict, date, judge_setup_hash))


def log_override(
    root: Path, date: datetime.date, run_id: str, candidate_id: str, judge_setup_hash: str
) -> Path:
    """Log judging a Run with an uncertified Judge Setup (ADR-0002)."""
    return _append(root, date, candidate_id, {
        "kind": "uncertified_judge_override",
        "date": date.isoformat(),
        "run": run_id,
        "candidate": {"id": candidate_id},
        "judge_setup_hash": judge_setup_hash,
    })


def override_logged(root: Path, run_id: str, judge_setup_hash: str) -> bool:
    """Whether judging the Run with this Judge Setup's uncertified override is logged."""
    return any(
        e.get("kind") == "uncertified_judge_override"
        and e.get("run") == run_id
        and e.get("judge_setup_hash") == judge_setup_hash
        for e in read_log(root)
    )


def log_certification(
    root: Path,
    date: datetime.date,
    setup: JudgeSetup,
    agreement: Agreement,
    item_ids: tuple[str, ...],
) -> Path:
    """Log a Judge Setup's Certification on the Calibration Set (ADR-0002)."""
    return _append(root, date, f"judge-{setup.hash}", {
        "kind": "judge_certification",
        "date": date.isoformat(),
        "judge_setup_hash": setup.hash,
        "judge_setup": setup.snapshot(),
        "items": list(item_ids),
        "within_one_point": agreement.overall.within_one_point,
        "weighted_kappa": agreement.overall.weighted_kappa,
        "thresholds": {
            "within_one_point": agreement.within_one_point_threshold,
            "weighted_kappa": agreement.weighted_kappa_threshold,
        },
        "criteria": {cid: _agreement(a) for cid, a in agreement.criteria.items()},
    })


def certified(root: Path, judge_setup_hash: str) -> bool:
    """Whether a Certification of this Judge Setup is logged; a changed Judge Setup has a
    new hash, so it is uncertified until certified itself."""
    return any(
        e.get("kind") == "judge_certification" and e.get("judge_setup_hash") == judge_setup_hash
        for e in read_log(root)
    )


def _agreement(a: CriterionAgreement) -> dict[str, Any]:
    return {"items": a.items, "within_one_point": a.within_one_point,
            "weighted_kappa": a.weighted_kappa}


def _append(root: Path, date: datetime.date, subject: str, entry: dict[str, Any]) -> Path:
    folder = root / DECISIONS_FOLDER
    folder.mkdir(exist_ok=True)
    number = next_number(folder, _ENTRY)
    path = folder / f"{number:04d}-{date.isoformat()}-{subject}.yaml"
    with path.open("x") as file:  # never overwrite an entry
        yaml.safe_dump(entry, file, sort_keys=False)
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
        "kind": "verdict",
        "date": date.isoformat(),
        "candidate": {"id": verdict.candidate_id, "version": verdict.candidate_version},
        "baseline": {"id": verdict.baseline_id, "version": verdict.baseline_version},
        "verdict": verdict.outcome,
        "label": verdict.label,
        "full_adopt": verdict.full_adopt,
        "suite": None if verdict.suite is None else str(verdict.suite),
        "private_tasks": verdict.private_tasks,
        "judge_setup_hash": judge_setup_hash,
        "rationale": list(verdict.rationale),
        "regressions": list(verdict.regressions),
        "warnings": list(verdict.warnings),
        "runs": {"challenger": verdict.challenger_run_id, "baseline": verdict.baseline_run_id},
    }
