"""Typed field reading for YAML config files, with errors naming file and field."""

from __future__ import annotations

import datetime
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from bench.core.errors import ConfigError

_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:\s|$)")


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _segments(text: str) -> list[tuple[str | None, str]]:
    """text split into top-level settings (key, its lines) and what lies between them
    (None, comment and blank lines)."""
    segments: list[tuple[str | None, list[str]]] = []
    for line in text.splitlines(keepends=True):
        top = _TOP_LEVEL_KEY.match(line)
        if top:
            segments.append((top.group(1), [line]))
        elif segments and segments[-1][0] is not None and (
            not line.strip() or line[0].isspace()
        ):
            segments[-1][1].append(line)  # an indented or blank line of the setting
        elif segments and segments[-1][0] is None:
            segments[-1][1].append(line)
        else:
            segments.append((None, [line]))
    result: list[tuple[str | None, str]] = []
    for key, lines in segments:
        trailing: list[str] = []
        while key is not None and lines and not lines[-1].strip():
            trailing.insert(0, lines.pop())  # blank lines after it lie between settings
        result.append((key, "".join(lines)))
        if trailing:
            result.append((None, "".join(trailing)))
    return result


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

    def write(self, root: Path, data: dict[str, Any]) -> None:
        """Write data back as plain YAML, so the git diff shows only what changed.

        A top-level setting whose value is unchanged keeps its text, comments
        included, and the comments between settings stay; a changed one is rewritten
        as block YAML in place (losing only its own inline comments), and a new one
        goes at the end.
        """
        path = root / self.file
        text = path.read_text() if path.is_file() else ""
        try:
            old = yaml.safe_load(text)
        except yaml.YAMLError:
            old = None
        if not isinstance(old, dict):
            old, text = {}, ""
        out: list[str] = []
        written: set[str] = set()
        for key, lines in _segments(text):
            if key is None:
                out.append(lines)
            elif key in data and key not in written:
                same = key in old and data[key] == old[key]
                out.append(lines if same else _dump({key: data[key]}))
                written.add(key)
        out += [_dump({key: data[key]}) for key in data if key not in written]
        body = "".join(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if body.endswith("\n") else body + "\n")

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
