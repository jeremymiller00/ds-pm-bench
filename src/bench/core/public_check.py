"""Spotting internal identifiers in Public Tasks before they are committed (story 69).

A Public Task's input and Gold Answer are published, so an email address, or anything
matching config/local.yaml's public_denylist, suggests the Task should be private or
sanitized. This only warns: what is internal is a judgement the PM makes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

# A Public Task's input or Gold Answer, current or archived, relative to the repo root.
PUBLIC_CONTENT = re.compile(
    r"^(tasks|examples)/[^/]+/(versions/\d+/)?(input\.[^/]+|gold\.md)$"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def public_task_warnings(
    root: Path, files: Iterable[str], denylist: Iterable[str]
) -> list[str]:
    """One line per suspect match, as file:line: what was found; empty when clean.
    Files that are not a Public Task's input or Gold Answer are skipped."""
    patterns = [("email address", EMAIL)] + [
        (f"public_denylist pattern {p!r}", re.compile(p, re.IGNORECASE)) for p in denylist
    ]
    found = []
    for name in files:
        relative = Path(name).as_posix()
        path = root / relative
        if not PUBLIC_CONTENT.match(relative) or not path.is_file():
            continue
        lines = path.read_text(errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            for what, pattern in patterns:
                for match in pattern.finditer(line):
                    found.append(f"{relative}:{number}: {what}: {match.group(0)!r}")
    return found
