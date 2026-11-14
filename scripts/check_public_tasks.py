"""Pre-commit hook: warn when a staged Public Task's input or Gold Answer contains an
email address or a pattern from public_denylist in the git-ignored config/local.yaml
(story 69). Never blocks the commit. Run from the repo root with the staged file names:

    python scripts/check_public_tasks.py tasks/T1.1/input.md ...
"""

from __future__ import annotations

import sys
from pathlib import Path

from bench.core.public_check import public_task_warnings
from bench.core.settings import load_public_denylist


def main(files: list[str]) -> int:
    root = Path.cwd()
    warnings = public_task_warnings(root, files, load_public_denylist(root))
    for warning in warnings:
        print(f"warning: {warning}")
    if warnings:
        print("A Public Task is published: make it a Private Task, or sanitize it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
