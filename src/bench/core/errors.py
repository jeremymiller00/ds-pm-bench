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


class TaskAuthoringError(ValueError):
    """Creating or editing a Task was refused: an id taken, a root not configured,
    a catalog Task already present, or a field an edit cannot change."""


class TaskEditRefused(TaskAuthoringError):
    """An edit asked to change what an edit cannot: visibility, id or version."""


class TaskNotRunnable(Exception):
    """The harness refuses to run a Task that still holds a placeholder."""


class UnknownCandidateError(LookupError):
    """No Candidate with the requested id."""


class CandidateAuthoringError(ValueError):
    """Creating or changing a Candidate was refused: an id taken, the Baseline left
    unmarked, a superseded version edited, or a field that names a different Candidate."""


class GateNotPassed(Exception):
    """A Candidate without a passing Gate cannot be Run (or adopted)."""


class ClearanceError(Exception):
    """Private content refused to a Candidate or Judge without private Clearance (ADR-0006)."""


class SuiteUnavailable(Exception):
    """The requested Suite has no Tasks to cover, such as private Clearance but no Private Tasks."""


class AttemptError(Exception):
    """An Attempt refused an action: a Run whose Candidate is the wrong kind of Adapter
    for it, a Task already recorded or already holding a Returned Output, a step out of order,
    or an Attempt already over."""


class EditorError(Exception):
    """The Editor Adapter could not edit the text: $EDITOR failed to start or exited
    with an error."""


class UnknownRunError(LookupError):
    """No Run with the requested id under runs/."""


class RunExists(Exception):
    """A Run for this Candidate and date already exists; resume it instead."""


class RunMismatch(Exception):
    """A Run cannot resume: what it snapshotted (Candidate, Task versions) has changed."""


class RunIncomplete(Exception):
    """A Run cannot produce TaskResults until every Task has an Output and Criterion scores."""


class SuiteMismatch(Exception):
    """Two Runs cannot be compared: they do not cover the same Tasks at the same versions."""


class JudgingError(Exception):
    """Judging a Run was refused or failed; every judging error is one of these."""


class RunNotFinished(JudgingError):
    """A Run with pending Tasks cannot be judged; judge it once every Output is recorded."""


class JudgeUnavailable(JudgingError):
    """Judging cannot start: no Judge Setup in config/judge.yaml, no judge models given
    to the Workspace, or a judge model that cannot be made (its API key missing, its
    provider unsupported, or its API erroring)."""


class JudgeNotCertified(JudgingError):
    """The Judge Setup is not certified; judging needs an explicit, logged override."""


class JudgeRefused(JudgingError):
    """A judge model of the Judge Setup is the model the Candidate under test calls,
    which would judge its own Output."""


class JudgeClearanceError(JudgingError, ClearanceError):
    """A Private Task refused to a Judge Setup without private Clearance (ADR-0006)."""


class JudgeReplyError(JudgingError, ValueError):
    """A judge model's reply is malformed: not JSON, a Criterion missing or unknown, a
    score outside 1-5 or a reason that is not one line. Nothing is recorded for the Task."""


class JudgeSetupMismatch(JudgingError):
    """A Run already judged by one Judge Setup cannot be judged by another."""


class CalibrationError(ValueError):
    """A Calibration Set change was refused: an unfinished Run, an Output already in the
    set, an unknown item, or human scores that do not match its Criteria."""


class CandidateToolError(Exception):
    """An automated Candidate's tool errored: an API error, a timeout or no Output."""


class CandidateUnavailable(Exception):
    """An automated Candidate cannot be sent Tasks: the Workspace was opened without
    Candidate Adapters."""


class MissingSecret(Exception):
    """A secret a Candidate needs is set neither in the environment nor in .env.
    Names the environment variable, never a value (ADR-0005)."""
