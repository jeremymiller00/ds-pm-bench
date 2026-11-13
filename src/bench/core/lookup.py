"""Finding a domain object by its id."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


def find_by_id[T: _HasId](items: Iterable[T], item_id: str) -> T | None:
    """The first item whose id is item_id, or None; callers raise their own lookup error."""
    return next((item for item in items if item.id == item_id), None)
