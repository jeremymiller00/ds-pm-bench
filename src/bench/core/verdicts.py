"""Comparisons and Verdicts: a challenger Run against a Baseline Run over one Suite."""

from __future__ import annotations

from dataclasses import dataclass

from bench.core.runs import Run
from bench.core.scoring import RunScore
from bench.core.settings import Settings

# A Judge and human scoring more than this many points apart on a Spot-check (PLAN.md).
SPOT_CHECK_DISAGREEMENT_POINTS = 1


@dataclass(frozen=True)
class Verdict:
    outcome: str  # "adopt", "reject" or "re-test"
    rationale: tuple[str, ...]  # why, one reason per line, for the Decision Log
    regressions: tuple[str, ...] = ()  # Tracks more than the margin below the Baseline


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
    spot_check_disagreement: float | None = None,
) -> Comparison:
    verdict = decide(settings, challenger_score, baseline_score, spot_check_disagreement)
    return Comparison(
        challenger=challenger,
        baseline=baseline,
        challenger_score=challenger_score,
        baseline_score=baseline_score,
        verdict=verdict,
    )


def decide(
    settings: Settings,
    challenger: RunScore,
    baseline: RunScore,
    spot_check_disagreement: float | None = None,
) -> Verdict:
    """The adopt rule, given a challenger with a passing Gate (it has a Run).

    A recorded Spot-check disagreement over the limit makes the Judge's scores
    suspect, so it forces a re-test before the scores are weighed at all.
    """
    gap = round(challenger.weighted - baseline.weighted, 9)  # margins compare exactly
    regressions = tuple(
        track
        for track, quality in baseline.track_quality.items()
        if round(quality - challenger.track_quality.get(track, quality), 9)
        > settings.regression_margin
    )
    scores = f"weighted {challenger.weighted:.2f} vs Baseline {baseline.weighted:.2f}"
    if (
        spot_check_disagreement is not None
        and spot_check_disagreement > SPOT_CHECK_DISAGREEMENT_POINTS
    ):
        return Verdict(
            "re-test",
            (
                f"Spot-check: Judge and human differ by {spot_check_disagreement:g} points, "
                f"more than {SPOT_CHECK_DISAGREEMENT_POINTS}",
                scores,
            ),
            regressions,
        )
    if regressions:
        drops = ", ".join(
            f"{t} {challenger.track_quality[t]:.2f} vs {baseline.track_quality[t]:.2f}"
            for t in regressions
        )
        return Verdict(
            "reject",
            (
                f"Regression beyond the margin of {settings.regression_margin} "
                f"in Track quality: {drops}",
                scores,
            ),
            regressions,
        )
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
