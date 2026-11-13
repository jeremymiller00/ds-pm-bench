"""Comparisons and Verdicts: a challenger Run against a Baseline Run over one Suite."""

from __future__ import annotations

from dataclasses import dataclass

from bench.core.errors import SuiteMismatch
from bench.core.runs import PRIVATE_CLEARANCE, Run
from bench.core.scoring import RunScore
from bench.core.settings import Settings

OUTCOMES = ("adopt", "reject", "re-test")

# A Judge and human scoring more than this many points apart on a Spot-check (PLAN.md).
SPOT_CHECK_DISAGREEMENT_POINTS = 1

# A Comparison pairs Runs from the same week; further apart only warns.
SAME_WEEK_DAYS = 7


@dataclass(frozen=True)
class Verdict:
    outcome: str  # "adopt", "reject" or "re-test"
    suite: str | None  # "public_only" or "public_and_private"; None without Runs (Gate failed)
    rationale: tuple[str, ...]  # why, one reason per line, for the Decision Log
    candidate_id: str
    candidate_version: str
    baseline_id: str
    baseline_version: str
    private_tasks: int  # Private Tasks in the Suite
    regressions: tuple[str, ...] = ()  # Tracks more than the margin below the Baseline
    challenger_run_id: str | None = None
    baseline_run_id: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def full_adopt(self) -> bool:
        """Only an adopt over a public-and-private Suite counts as a full adopt."""
        return self.outcome == "adopt" and self.suite == PRIVATE_CLEARANCE

    @property
    def label(self) -> str:
        if self.suite != "public_only":
            return self.outcome
        if self.outcome == "adopt":
            return "adopt (public-only Suite, not a full adopt)"
        return f"{self.outcome} (public-only Suite)"


@dataclass(frozen=True)
class Comparison:
    challenger: Run
    baseline: Run
    challenger_score: RunScore
    baseline_score: RunScore
    verdict: Verdict

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.verdict.warnings


def compare(
    settings: Settings,
    challenger: Run,
    challenger_score: RunScore,
    baseline: Run,
    baseline_score: RunScore,
    spot_check_disagreement: float | None = None,
) -> Comparison:
    if not baseline.candidate.baseline:
        raise ValueError(
            f"Run {baseline.id} is of {baseline.candidate_id}, not the Baseline"
        )
    check_same_suite(challenger, baseline)
    outcome, rationale, regressions = decide(
        settings, challenger_score, baseline_score, spot_check_disagreement
    )
    warnings = ()
    days = abs((challenger.date - baseline.date).days)
    if days > SAME_WEEK_DAYS:
        warnings = (f"the Runs are {days} days apart, more than a week",)
    verdict = Verdict(
        outcome=outcome,
        suite=challenger.suite,
        rationale=rationale,
        candidate_id=challenger.candidate_id,
        candidate_version=challenger.candidate.version,
        baseline_id=baseline.candidate_id,
        baseline_version=baseline.candidate.version,
        private_tasks=sum(t.visibility == "private" for t in challenger.tasks),
        regressions=regressions,
        challenger_run_id=challenger.id,
        baseline_run_id=baseline.id,
        warnings=warnings,
    )
    return Comparison(
        challenger=challenger,
        baseline=baseline,
        challenger_score=challenger_score,
        baseline_score=baseline_score,
        verdict=verdict,
    )


def check_same_suite(challenger: Run, baseline: Run) -> None:
    """Both Runs must cover the same Tasks at the same versions."""
    a, b = challenger.task_versions, baseline.task_versions
    if a == b:
        return
    differences = [
        f"{task_id}: {a.get(task_id, 'absent')} vs {b.get(task_id, 'absent')}"
        for task_id in sorted(set(a) | set(b))
        if a.get(task_id) != b.get(task_id)
    ]
    raise SuiteMismatch(
        f"Runs {challenger.id} and {baseline.id} cover different Suites "
        f"(challenger vs Baseline Task version): {'; '.join(differences)}"
    )


def decide(
    settings: Settings,
    challenger: RunScore,
    baseline: RunScore,
    spot_check_disagreement: float | None = None,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """The adopt rule for a challenger with a passing Gate (it has a Run).

    Returns the outcome, its rationale and any Regressions. A recorded Spot-check
    disagreement over the limit makes the Judge's scores suspect, so it forces a
    re-test before the scores are weighed at all.
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
        return (
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
        return (
            "reject",
            (
                f"Regression beyond the margin of {settings.regression_margin} "
                f"in Track quality: {drops}",
                scores,
            ),
            regressions,
        )
    if gap >= 0:
        return "adopt", (f"{scores}: at least the Baseline's",), ()
    if -gap <= settings.retest_margin:
        return (
            "re-test",
            (f"{scores}: below, but within the re-test margin of {settings.retest_margin}",),
            (),
        )
    return (
        "reject",
        (f"{scores}: more than the re-test margin of {settings.retest_margin} below",),
        (),
    )
