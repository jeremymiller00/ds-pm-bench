"""The shared Rubric: Criteria every Task's Output is scored against."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.core.fields import FieldReader
from bench.core.lookup import find_by_id

RUBRIC_FILE = "config/rubric.yaml"

_CRITERION_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    description: str


def criterion_dict(criterion: Criterion) -> dict[str, str]:
    """A Criterion as written in a rubric file: {id, name, description}."""
    return {"id": criterion.id, "name": criterion.name, "description": criterion.description}


@dataclass(frozen=True)
class Rubric:
    criteria: tuple[Criterion, ...]

    def criterion(self, criterion_id: str) -> Criterion:
        criterion = find_by_id(self.criteria, criterion_id)
        if criterion is None:
            raise KeyError(criterion_id)
        return criterion


def check_criterion_score(value: Any, where: str) -> int | float:
    """The one check of a Criterion score, from a Judge or a human: a number from 1 to 5."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: must be a 1-5 number, got {value!r}")
    if not 1 <= value <= 5:
        raise ValueError(f"{where}: must be 1-5, got {value}")
    return value


def load_rubric(root: Path) -> Rubric:
    f = FieldReader(RUBRIC_FILE)
    return Rubric(criteria=parse_criteria(f, f.read(root).get("criteria"), "criteria"))


def parse_rubric_edit(criteria: Any) -> Rubric:
    """The shared Rubric criteria would make, validated as on load (ConfigError naming
    the field); nothing is written."""
    f = FieldReader(RUBRIC_FILE)
    entries = [dict(c) for c in criteria] if isinstance(criteria, (list, tuple)) else criteria
    return Rubric(criteria=parse_criteria(f, entries, "criteria"))


def write_rubric(root: Path, rubric: Rubric) -> None:
    """Write the shared Rubric back, keeping the file's comments (ADR-0001)."""
    FieldReader(RUBRIC_FILE).write(
        root, {"criteria": [criterion_dict(c) for c in rubric.criteria]}
    )


def parse_criteria(f: FieldReader, value: Any, field: str) -> tuple[Criterion, ...]:
    """Parse a non-empty list of Criteria with unique ids. Reused by task rubrics."""
    if not isinstance(value, list) or not value:
        raise f.fail(field, "must be a non-empty list of Criteria")
    criteria: list[Criterion] = []
    for i, entry in enumerate(value):
        at = f"{field}[{i}]"
        raw = f.mapping(entry, at)
        cid = f.text(raw.get("id"), f"{at}.id")
        if not _CRITERION_ID.match(cid):
            raise f.fail(f"{at}.id", f"must be snake_case, got {cid!r}")
        if any(c.id == cid for c in criteria):
            raise f.fail(f"{at}.id", f"duplicate Criterion id {cid!r}")
        criteria.append(
            Criterion(
                id=cid,
                name=f.text(raw.get("name"), f"{at}.name"),
                description=f.text(raw.get("description"), f"{at}.description"),
            )
        )
    return tuple(criteria)
