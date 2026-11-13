"""Typed field reading for YAML config files, with errors naming file and field."""

from __future__ import annotations

import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from bench.core.errors import ConfigError


class FieldReader:
    """Reads typed fields from one config file, raising ConfigError with a dotted path."""

    def __init__(self, file: str) -> None:
        self.file = file

    def read(self, root: Path) -> dict[str, Any]:
        """Parse the file as a YAML mapping."""
        path = root / self.file
        if not path.is_file():
            raise self.fail("(file)", "is missing")
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise self.fail("(file)", f"is not valid YAML: {exc}") from exc
        return self.mapping(data, "(file)")

    def fail(self, field: str, message: str) -> ConfigError:
        return ConfigError(self.file, field, message)

    def mapping(self, value: Any, field: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise self.fail(field, "must be a mapping")
        return value

    def number(self, value: Any, field: str, *, minimum: float | None = None) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self.fail(field, f"must be a number, got {value!r}")
        if minimum is not None and value < minimum:
            raise self.fail(field, f"must be at least {minimum}, got {value}")
        return value

    def text(self, value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise self.fail(field, "must be non-empty text")
        return value.strip()

    def fraction(self, value: Any, field: str) -> float:
        number = self.number(value, field)
        if not 0 <= number <= 1:
            raise self.fail(field, f"must be between 0 and 1, got {number}")
        return number

    def exact_keys(self, data: dict[str, Any], field: str, keys: tuple[str, ...]) -> None:
        for key in data:
            if key not in keys:
                raise self.fail(f"{field}.{key}", f"unknown, expected one of {', '.join(keys)}")
        for key in keys:
            if key not in data:
                raise self.fail(f"{field}.{key}", "is missing")

    def boolean(self, value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise self.fail(field, f"must be true or false, got {value!r}")
        return value

    def choice(self, value: Any, field: str, options: tuple[str, ...]) -> str:
        if value not in options:
            raise self.fail(field, f"must be one of {', '.join(options)}, got {value!r}")
        return value

    def enum[E: StrEnum](self, value: Any, field: str, kind: type[E]) -> E:
        options = tuple(str(member) for member in kind)
        return kind(self.choice(value, field, options))

    def date(self, value: Any, field: str) -> datetime.date:
        if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
            return value
        try:
            return datetime.date.fromisoformat(str(value))
        except ValueError:
            raise self.fail(field, f"must be a date like 2026-11-13, got {value!r}") from None
