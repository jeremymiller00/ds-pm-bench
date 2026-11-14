"""Finding a domain object by its id, and numbering the next one."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


def find_by_id[T: _HasId](items: Iterable[T], item_id: str) -> T | None:
    """The first item whose id is item_id, or None; callers raise their own lookup error."""
    return next((item for item in items if item.id == item_id), None)


def next_number(folder: Path, pattern: re.Pattern[str]) -> int:
    """One more than the highest number pattern's first group finds among folder's file
    names (1 for none): the one next-id rule for numbered, append-only files."""
    numbers = [
        int(m.group(1)) for p in (folder.iterdir() if folder.is_dir() else ())
        if (m := pattern.match(p.name))
    ]
    return max(numbers, default=0) + 1
