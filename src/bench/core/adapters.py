"""Adapter ports: the outward boundaries Workspace receives by injection (spec #1).

Tests inject fakes; the real implementations below read the system clock and run the
person's $EDITOR.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol


class Clock(Protocol):
    """Seconds on a monotonic scale; only differences between readings mean anything."""

    def now(self) -> float: ...


class Editor(Protocol):
    """Opens text for a person to edit and returns it once they close the editor."""

    def edit(self, text: str) -> str: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()


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
