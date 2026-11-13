"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bench.core.config import Settings, load_settings
from bench.core.rubric import Rubric, load_rubric


@dataclass(frozen=True)
class Workspace:
    root: Path
    settings: Settings
    rubric: Rubric

    @classmethod
    def open(cls, path: str | Path) -> Workspace:
        root = Path(path).resolve()
        return cls(root=root, settings=load_settings(root), rubric=load_rubric(root))
