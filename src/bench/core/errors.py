"""Errors raised by the core."""

from __future__ import annotations


class ConfigError(ValueError):
    """Invalid config. Names the file (relative to the Workspace root) and field."""

    def __init__(self, file: str, field: str, message: str) -> None:
        self.file = file
        self.field = field
        self.message = message
        super().__init__(f"{file}: {field}: {message}")


class UnknownTaskError(LookupError):
    """No Task with the requested id in any loaded root."""


class TaskNotRunnable(Exception):
    """The harness refuses to run a Task that still holds a placeholder."""
