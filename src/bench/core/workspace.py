"""The Workspace: the single entry point clients use to touch the domain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bench.core.settings import Settings, load_settings
from bench.core.prices import PriceTable, load_prices
from bench.core.results import TaskResult
from bench.core.rubric import Rubric, load_rubric
from bench.core.scoring import RunScore, score_run


@dataclass(frozen=True)
class Workspace:
    root: Path
    settings: Settings
    rubric: Rubric
    prices: PriceTable

    @classmethod
    def open(cls, path: str | Path) -> Workspace:
        root = Path(path).resolve()
        return cls(
            root=root,
            settings=load_settings(root),
            rubric=load_rubric(root),
            prices=load_prices(root),
        )

    def score(self, results: Iterable[TaskResult]) -> RunScore:
        """Score a Run's per-Task result records with this Workspace's weights and bands."""
        return score_run(self.settings, results)
