"""DS PM Benchmark core library."""

from bench.core.errors import (
    CandidateAuthoringError,
    ClearanceError,
    ConfigError,
    GateNotPassed,
    RunExists,
    RunIncomplete,
    RunMismatch,
    SuiteMismatch,
    SuiteUnavailable,
    TaskAuthoringError,
    TaskEditRefused,
    TaskNotRunnable,
    UnknownCandidateError,
    UnknownRunError,
    UnknownTaskError,
)
from bench.core.results import TaskResult
from bench.core.scoring import RunScore, TaskScore
from bench.core.verdicts import Comparison, Verdict
from bench.core.workspace import Workspace

__all__ = [
    "CandidateAuthoringError",
    "Comparison",
    "ClearanceError",
    "ConfigError",
    "GateNotPassed",
    "RunExists",
    "RunIncomplete",
    "RunMismatch",
    "RunScore",
    "SuiteMismatch",
    "SuiteUnavailable",
    "TaskAuthoringError",
    "TaskEditRefused",
    "TaskNotRunnable",
    "TaskResult",
    "TaskScore",
    "UnknownCandidateError",
    "UnknownRunError",
    "UnknownTaskError",
    "Verdict",
    "Workspace",
]
