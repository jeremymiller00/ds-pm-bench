"""Comparisons and Verdicts: a challenger Run against a Baseline Run over one Suite."""

from __future__ import annotations

from dataclasses import dataclass

from bench.core.runs import Run
from bench.core.scoring import RunScore
from bench.core.settings import Settings


@dataclass(frozen=True)
class Verdict:
    outcome: str  # "adopt", "reject" or "re-test"
    rationale: tuple[str, ...]  # why, one reason per line, for the Decision Log


@dataclass(frozen=True)
class Comparison:
    challenger: Run
    baseline: Run
    challenger_score: RunScore
    baseline_score: RunScore
    verdict: Verdict


def compare(
    settings: Settings,
    challenger: Run,
    challenger_score: RunScore,
    baseline: Run,
    baseline_score: RunScore,
) -> Comparison:
    verdict = decide(settings, challenger_score, baseline_score)
    return Comparison(
        challenger=challenger,
        baseline=baseline,
        challenger_score=challenger_score,
        baseline_score=baseline_score,
        verdict=verdict,
    )


def decide(settings: Settings, challenger: RunScore, baseline: RunScore) -> Verdict:
    """The adopt rule, given a challenger with a passing Gate (it has a Run)."""
    gap = round(challenger.weighted - baseline.weighted, 9)  # margins compare exactly
    scores = f"weighted {challenger.weighted:.2f} vs Baseline {baseline.weighted:.2f}"
    if gap >= 0:
        return Verdict("adopt", (f"{scores}: at least the Baseline's",))
    if -gap <= settings.retest_margin:
        return Verdict(
            "re-test",
            (f"{scores}: below, but within the re-test margin of {settings.retest_margin}",),
        )
    return Verdict(
        "reject",
        (f"{scores}: more than the re-test margin of {settings.retest_margin} below",),
    )
