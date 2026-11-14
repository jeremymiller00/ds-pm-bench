"""Comparisons and Verdicts: a challenger Run against a Baseline Run over one Suite."""

from __future__ import annotations

from dataclasses import dataclass

from bench.core.candidates import Candidate
from bench.core.errors import SuiteMismatch
from bench.core.kinds import Suite, Visibility
from bench.core.runs import Run, SpotCheck
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
    suite: Suite | None  # None without Runs (Gate failed)
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
        return self.outcome == "adopt" and self.suite == Suite.PUBLIC_AND_PRIVATE

    @property
    def label(self) -> str:
        if self.suite != Suite.PUBLIC_ONLY:
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
    def judge_setup_hash(self) -> str | None:
        """The Judge Setup both Runs were scored by; None if they differ or are unknown."""
        a, b = self.challenger.judge_setup_hash, self.baseline.judge_setup_hash
        return a if a == b else None


def compare(
    settings: Settings,
    challenger: Run,
    challenger_score: RunScore,
    baseline: Run,
    baseline_score: RunScore,
) -> Comparison:
    if not baseline.candidate.baseline:
        raise ValueError(
            f"Run {baseline.id} is of {baseline.candidate_id}, not the Baseline"
        )
    check_same_suite(challenger, baseline)
    worst = max(
        challenger.spot_checks + baseline.spot_checks, key=lambda c: c.gap, default=None
    )
    outcome, rationale, regressions = apply_adopt_rule(
        settings, challenger_score, baseline_score, worst
    )
    warnings: tuple[str, ...] = ()
    days = abs((challenger.date - baseline.date).days)
    if days > SAME_WEEK_DAYS:
        warnings += (f"the Runs are {days} days apart, more than a week",)
    a, b = challenger.judge_setup_hash, baseline.judge_setup_hash
    if a != b and a is not None and b is not None:
        warnings += (
            f"the Runs were scored by different Judge Setups ({a} vs {b}), so their "
            "scores may not be comparable",
        )
    verdict = Verdict(
        outcome=outcome,
        suite=challenger.suite,
        rationale=rationale,
        candidate_id=challenger.candidate_id,
        candidate_version=challenger.candidate.version,
        baseline_id=baseline.candidate_id,
        baseline_version=baseline.candidate.version,
        private_tasks=sum(t.visibility == Visibility.PRIVATE for t in challenger.tasks),
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


def gate_verdict(candidate: Candidate, baseline: Candidate) -> Verdict:
    """Reject a Candidate whose Gate has not passed. It can never have a Run to compare."""
    if candidate.gate_passed:
        raise ValueError(
            f"Candidate {candidate.id}'s Gate passed; compare its Run with a Baseline Run"
        )
    reason = "no Gate review is recorded" if candidate.gate is None else "its Gate failed"
    return Verdict(
        outcome="reject",
        suite=None,
        rationale=(f"Gate: {reason}, so {candidate.id} cannot be Run or adopted",),
        candidate_id=candidate.id,
        candidate_version=candidate.version,
        baseline_id=baseline.id,
        baseline_version=baseline.version,
        private_tasks=0,
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


def apply_adopt_rule(
    settings: Settings,
    challenger: RunScore,
    baseline: RunScore,
    worst_spot_check: SpotCheck | None = None,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """The adopt rule for a challenger with a passing Gate (it has a Run).

    Returns the outcome, its rationale and any Regressions. The rules apply in a fixed
    precedence, each only when every earlier one has passed:

      1. Gate: a Candidate whose Gate has not passed is rejected without Runs
         (gate_verdict); by the time this runs, the Gate has passed.
      2. Spot-check: the worst recorded Spot-check, on either Run, more than
         SPOT_CHECK_DISAGREEMENT_POINTS from the Judge forces a re-test. The scores
         are then untrustworthy, so they cannot establish anything, not even a
         Regression: rejecting on suspect scores would be as unfounded as adopting.
      3. Regression: any Track whose Quality falls more than the regression margin
         below the Baseline's rejects, however good the weighted total.
      4. Adopt: a weighted total at least the Baseline's.
      5. Re-test: a weighted total below, but within the re-test margin.
      6. Reject: anything further below.
    """
    gap = round(challenger.weighted - baseline.weighted, 9)  # margins compare exactly
    regressions = tuple(
        track
        for track, quality in baseline.track_quality.items()
        if round(quality - challenger.track_quality.get(track, quality), 9)
        > settings.regression_margin
    )
    scores = f"weighted {challenger.weighted:.2f} vs Baseline {baseline.weighted:.2f}"
    if worst_spot_check is not None and worst_spot_check.gap > SPOT_CHECK_DISAGREEMENT_POINTS:
        c = worst_spot_check
        return (
            "re-test",
            (
                f"Spot-check: Judge and human differ by {c.gap:g} points on "
                f"{c.task_id} {c.criterion} in Run {c.run_id} (Judge {c.judge_score:g}, "
                f"human {c.human_score:g}), more than {SPOT_CHECK_DISAGREEMENT_POINTS}",
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
