"""DS PM Benchmark core library."""

from bench.core.adapters import Clock, Editor, ExternalEditor, SystemClock
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
from bench.core.sessions import ManualSession
from bench.core.verdicts import Comparison, Verdict
from bench.core.workspace import Workspace

__all__ = [
    "CandidateAuthoringError",
    "Clock",
    "Comparison",
    "ClearanceError",
    "ConfigError",
    "Editor",
    "ExternalEditor",
    "GateNotPassed",
    "ManualSession",
    "RunExists",
    "RunIncomplete",
    "RunMismatch",
    "RunScore",
    "SuiteMismatch",
    "SystemClock",
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
