"""Adapter ports: the outward boundaries Workspace receives by injection (spec #1).

Tests inject fakes; the real implementations below read the system clock and run the
person's $EDITOR. The real Candidate Adapters and judge models, which call a provider's
API, are in bench.providers, wired in by the composition root (bench.wiring), so the
core never imports a provider SDK.
"""

from __future__ import annotations

import datetime
import os
import shlex
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from bench.core.errors import EditorError

if TYPE_CHECKING:
    from bench.core.candidates import Candidate
    from bench.core.judging import JudgeModelSpec, JudgeRequest


class Clock(Protocol):
    """Time for the harness: now() is seconds on a monotonic scale, where only
    differences between readings mean anything; today() is the date that dates a Run,
    a Gate review or a Decision Log entry when none is given."""

    def now(self) -> float: ...

    def today(self) -> datetime.date: ...


class Editor(Protocol):
    """Opens text for a person to edit and returns it once they close the editor;
    EditorError if it cannot."""

    def edit(self, text: str) -> str: ...


@dataclass(frozen=True)
class CandidateReply:
    """What an automated Candidate returned for one prompt: its raw Output and the
    tokens the provider counted."""

    text: str
    input_tokens: int
    output_tokens: int


class CandidateAdapter(Protocol):
    """Sends text to an automated Candidate, word for word, and returns its reply.

    Raises CandidateToolError when the tool errors (API error, timeout, refusal to
    answer); the caller decides whether to retry, so an Adapter never retries itself.
    """

    def send(self, text: str) -> CandidateReply: ...


class JudgeModel(Protocol):
    """A judge model call: returns the model's reply text to a JudgeRequest."""

    def judge(self, request: JudgeRequest) -> str: ...


# What Workspace receives by injection: the Candidate Adapter for an automated
# Candidate, and the JudgeModel for each judge model of a Judge Setup.
type AdapterFactory = Callable[[Candidate], CandidateAdapter]
type JudgeModels = Callable[[JudgeModelSpec], JudgeModel]


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    def today(self) -> datetime.date:
        return datetime.date.today()


class ExternalEditor:
    """Runs $VISUAL or $EDITOR (vi if neither is set) on a temporary Markdown file.

    It blocks until the editor exits, so a TUI must suspend itself around edit().
    The temporary file is removed afterwards. An editor that cannot start, or exits
    with an error, raises EditorError.
    """

    def __init__(self, command: str | None = None) -> None:
        self.command = command

    def edit(self, text: str) -> str:
        command = self.command or os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        fd, name = tempfile.mkstemp(suffix=".md", prefix="bench-")
        path = Path(name)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            try:
                subprocess.run([*shlex.split(command), str(path)], check=True)
            except subprocess.CalledProcessError as exc:
                raise EditorError(
                    f"editor {command!r} exited with status {exc.returncode}"
                ) from exc
            except OSError as exc:
                raise EditorError(f"editor {command!r} could not start: {exc}") from exc
            return path.read_text()
        finally:
            path.unlink(missing_ok=True)
