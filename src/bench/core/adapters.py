"""Adapter ports: the outward boundaries Workspace receives by injection (spec #1).

Tests inject fakes; the real implementations below read the system clock and run the
person's $EDITOR.
"""

from __future__ import annotations

import datetime
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol


class Clock(Protocol):
    """Time for the harness: now() is seconds on a monotonic scale, where only
    differences between readings mean anything; today() is the date that dates a Run,
    a Gate review or a Decision Log entry when none is given."""

    def now(self) -> float: ...

    def today(self) -> datetime.date: ...


class Editor(Protocol):
    """Opens text for a person to edit and returns it once they close the editor."""

    def edit(self, text: str) -> str: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    def today(self) -> datetime.date:
        return datetime.date.today()


class ExternalEditor:
    """Runs $VISUAL or $EDITOR (vi if neither is set) on a temporary Markdown file.

    It blocks until the editor exits, so a TUI must suspend itself around edit().
    The temporary file is removed afterwards.
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
            subprocess.run([*shlex.split(command), str(path)], check=True)
            return path.read_text()
        finally:
            path.unlink(missing_ok=True)
