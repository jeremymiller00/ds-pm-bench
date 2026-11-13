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


class UnknownCandidateError(LookupError):
    """No Candidate with the requested id."""


class GateNotPassed(Exception):
    """A Candidate without a passing Gate cannot be Run (or adopted)."""


class ClearanceError(Exception):
    """Private content refused to a Candidate or Judge without private Clearance (ADR-0006)."""


class UnknownRunError(LookupError):
    """No Run with the requested id under runs/."""


class RunExists(Exception):
    """A Run for this Candidate and date already exists; resume it instead."""


class RunMismatch(Exception):
    """A Run cannot resume: what it snapshotted (Candidate, Task versions) has changed."""


class RunIncomplete(Exception):
    """A Run cannot produce TaskResults until every Task has an Output and Criterion scores."""
