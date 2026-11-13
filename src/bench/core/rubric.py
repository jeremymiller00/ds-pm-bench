"""The shared Rubric: Criteria every Task's Output is scored against."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.core.fields import FieldReader

RUBRIC_FILE = "config/rubric.yaml"

_CRITERION_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Rubric:
    criteria: tuple[Criterion, ...]

    def criterion(self, criterion_id: str) -> Criterion:
        for c in self.criteria:
            if c.id == criterion_id:
                return c
        raise KeyError(criterion_id)


def load_rubric(root: Path) -> Rubric:
    f = FieldReader(RUBRIC_FILE)
    return Rubric(criteria=parse_criteria(f, f.read(root).get("criteria"), "criteria"))


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
