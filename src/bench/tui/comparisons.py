"""The Comparisons and Decision Log tabs.

Comparisons: choose a challenger Run and a Baseline Run; the Workspace compares them
and this module shows the scorecard and its Verdict, which one key logs to the Decision
Log with that scorecard. Decision Log: browse every entry, newest first, and open a
Verdict's scorecard as it was logged; recomputing it from the Runs as they are now is a
separate, labelled action. Every rule lives in the Workspace; this module only shows
what it reports.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import yaml
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from bench.core.decisions import entry_kind, logged_scorecard
from bench.core.errors import COMPARISON_REFUSALS
from bench.core.kinds import EntryKind
from bench.core.verdicts import Comparison, Scorecard, ScoreRow, Verdict, verdict_from_entry
from bench.core.workspace import Workspace


class ComparisonsPane(Vertical):
    DEFAULT_CSS = """
    ComparisonsPane #cmp-controls { height: auto; }
    ComparisonsPane Select { width: 1fr; }
    ComparisonsPane #cmp-scorecard, ComparisonsPane #cmp-status { height: auto; padding: 0 1; }
    """
    BINDINGS = [Binding("l", "log", "Log Verdict")]

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.comparison: Comparison | None = None
        self.logged = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="cmp-controls"):
            yield Select([], prompt="Challenger Run", id="cmp-challenger")
            yield Select([], prompt="Baseline Run", id="cmp-baseline")
            yield Button("Compare", id="cmp-compare", variant="primary")
            yield Button("Log Verdict (l)", id="cmp-log", disabled=True)
        with VerticalScroll():
            yield Static("", id="cmp-scorecard", markup=False)
            yield Static("", id="cmp-status", markup=False)

    def on_mount(self) -> None:
        self._offer_runs()

    def on_show(self) -> None:
        self._offer_runs()  # Runs started in the Runs tab since

    def _offer_runs(self) -> None:
        options = [(run_id, run_id) for run_id in self.workspace.run_ids()]
        for select in self.query(Select):
            value = select.value
            select.set_options(options)
            if value in self.workspace.run_ids():
                select.value = value

    @on(Button.Pressed, "#cmp-compare")
    def _compare(self) -> None:
        challenger = self.query_one("#cmp-challenger", Select).value
        baseline = self.query_one("#cmp-baseline", Select).value
        if challenger is Select.BLANK or baseline is Select.BLANK:
            self._status("Choose a challenger Run and a Baseline Run to compare.")
            return
        self.comparison, self.logged = None, False
        self.query_one("#cmp-log", Button).disabled = True
        self.query_one("#cmp-scorecard", Static).update("")
        try:
            comparison = self.workspace.compare(str(challenger), str(baseline))
        except COMPARISON_REFUSALS as exc:
            self._status(f"Not compared: {exc}")
            return
        self.comparison = comparison
        self.query_one("#cmp-scorecard", Static).update(comparison_text(comparison))
        self.query_one("#cmp-log", Button).disabled = False
        self._status("l logs this Verdict to the Decision Log.")

    @on(Button.Pressed, "#cmp-log")
    def action_log(self) -> None:
        comparison = self.comparison
        if comparison is None:
            self._status("Compare two Runs first; only a Comparison's Verdict can be logged.")
            return
        if self.logged:
            self._status("This Verdict is already logged; compare again to log another.")
            return
        path = self.workspace.log_comparison(comparison)
        self.logged = True
        self.query_one("#cmp-log", Button).disabled = True
        self._status(f"Logged: {path.relative_to(self.workspace.root)}")

    def _status(self, message: str) -> None:
        self.query_one("#cmp-status", Static).update(message)


class DecisionLogPane(Vertical):
    DEFAULT_CSS = """
    DecisionLogPane OptionList { height: auto; max-height: 16; }
    DecisionLogPane #log-detail, DecisionLogPane #log-status { height: auto; padding: 0 1; }
    """

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.entries: tuple[dict[str, Any], ...] = ()

    def compose(self) -> ComposeResult:
        yield Label("Decision Log, newest first (enter on a Verdict opens its scorecard)")
        yield OptionList(id="log-entries")
        yield Static("", id="log-status", markup=False)
        with VerticalScroll():
            yield Static("", id="log-detail", markup=False)

    def on_mount(self) -> None:
        self._show_entries()

    def on_show(self) -> None:
        self._show_entries()  # Verdicts logged in the Comparisons tab since

    def _show_entries(self) -> None:
        self.entries = tuple(reversed(self.workspace.decision_log()))
        entries = self.query_one("#log-entries", OptionList)
        entries.clear_options()
        entries.add_options(
            Option(entry_label(entry), id=str(n)) for n, entry in enumerate(self.entries)
        )

    def _entry(self, option: Option) -> dict[str, Any] | None:
        return None if option.id is None else self.entries[int(option.id)]

    @on(OptionList.OptionHighlighted, "#log-entries")
    def _detail(self, event: OptionList.OptionHighlighted) -> None:
        entry = self._entry(event.option)
        detail = "" if entry is None else yaml.safe_dump(entry, sort_keys=False)
        self.query_one("#log-detail", Static).update(detail)
        self._status("")

    @on(OptionList.OptionSelected, "#log-entries")
    def _open(self, event: OptionList.OptionSelected) -> None:
        entry = self._entry(event.option)
        if entry is None:
            return
        if entry_kind(entry) != EntryKind.VERDICT:
            self._status("Only a Verdict has a Comparison behind it.")
            return
        runs = entry.get("runs") or {}
        if not (runs.get("challenger") and runs.get("baseline")):
            self._status("No Comparison: this Verdict needed no Runs.")
            return
        self._status("")
        self.app.push_screen(ComparisonScreen(self.workspace, entry))

    def _status(self, message: str) -> None:
        self.query_one("#log-status", Static).update(message)


class ComparisonScreen(Screen[None]):
    """A logged Verdict's scorecard exactly as it was logged. r recomputes the Comparison
    from its Runs as they are now, shown apart and labelled; escape goes back."""

    DEFAULT_CSS = "ComparisonScreen Static { height: auto; padding: 0 1; }"
    BINDINGS = [
        Binding("r", "recompute", "Recompute from the Runs as they are now"),
        Binding("escape", "leave", "Back to the Decision Log"),
    ]

    def __init__(self, workspace: Workspace, entry: dict[str, Any]) -> None:
        super().__init__()
        self.workspace = workspace
        self.entry = entry

    def compose(self) -> ComposeResult:
        verdict = verdict_from_entry(self.entry)
        card = logged_scorecard(self.entry)
        date = self.entry.get("date", "?")
        if card is None:
            note = (f"Logged {date}: Verdict {verdict.label}. This entry was logged "
                    "without a scorecard; r recomputes it from the Runs.")
            shown = verdict_text(verdict)
        else:
            note = f"As logged {date}. r recomputes it from the Runs as they are now."
            shown = scorecard_text(verdict, card, self.entry.get("judge_setup_hash"))
        with VerticalScroll():
            yield Static(note, id="comparison-note", markup=False)
            yield Static(shown, id="comparison-scorecard", markup=False)
            yield Static("", id="comparison-recomputed", markup=False)
        yield Footer()

    def action_recompute(self) -> None:
        runs = self.entry["runs"]
        try:
            comparison = self.workspace.compare(runs["challenger"], runs["baseline"])
        except COMPARISON_REFUSALS as exc:
            shown = f"Recomputed from the Runs as they are now: not comparable: {exc}"
        else:
            shown = "Recomputed from the Runs as they are now:\n\n" + comparison_text(comparison)
        self.query_one("#comparison-recomputed", Static).update(shown)

    def action_leave(self) -> None:
        self.dismiss(None)


def _verdict_label(entry: dict[str, Any]) -> str:
    candidate = (entry.get("candidate") or {}).get("id", "?")
    baseline = (entry.get("baseline") or {}).get("id", "?")
    return f"Verdict {entry.get('label')}: {candidate} vs Baseline {baseline}"


def _override_label(entry: dict[str, Any]) -> str:
    return f"uncertified Judge override: Run {entry.get('run')}"


def _certification_label(entry: dict[str, Any]) -> str:
    agreement = agreement_text(entry.get("within_one_point"), entry.get("weighted_kappa"))
    return f"Judge Setup {entry.get('judge_setup_hash')} certified: {agreement}"


# How each kind of Decision Log entry is labelled in the list.
ENTRY_LABELS: dict[EntryKind, Callable[[dict[str, Any]], str]] = {
    EntryKind.VERDICT: _verdict_label,
    EntryKind.UNCERTIFIED_JUDGE_OVERRIDE: _override_label,
    EntryKind.JUDGE_CERTIFICATION: _certification_label,
}


def entry_label(entry: dict[str, Any]) -> str:
    date, kind = entry.get("date", "?"), entry_kind(entry)
    if kind is None:
        return f"{date}  {entry.get('kind', '?')}"
    return f"{date}  {ENTRY_LABELS[kind](entry)}"


def agreement_text(within_one_point: float | None, weighted_kappa: float | None) -> str:
    within = "?" if within_one_point is None else f"{within_one_point:.0%}"
    kappa = "n/a" if weighted_kappa is None else f"{weighted_kappa:.2f}"
    return f"{within} within 1 point, weighted kappa {kappa}"


def comparison_text(comparison: Comparison) -> str:
    return scorecard_text(comparison.verdict, comparison.scorecard, comparison.judge_setup_hash)


def verdict_text(verdict: Verdict) -> str:
    lines = [f"Verdict: {verdict.label}"]
    lines += [f"  - {reason}" for reason in verdict.rationale]
    return "\n".join(lines)


def scorecard_text(verdict: Verdict, card: Scorecard, judge_setup_hash: str | None) -> str:
    judge = judge_setup_hash or "differs between the Runs, or unknown"
    lines = [
        verdict_text(verdict),
        "",
        f"{verdict.candidate_id} (Run {verdict.challenger_run_id}) vs Baseline "
        f"{verdict.baseline_id} (Run {verdict.baseline_run_id})",
        f"Suite: {verdict.suite}, {verdict.private_tasks} Private Tasks; "
        f"full adopt: {'yes' if verdict.full_adopt else 'no'}",
        f"Judge Setup: {judge}",
        f"Margins: regression {card.regression_margin:g}, re-test {card.retest_margin:g}",
        "",
        f"{'Dimension':<20}{'weight':>8}{'challenger':>12}{'Baseline':>10}{'delta':>8}",
    ]
    lines += [row(d, f"{card.weights[d]:g}%", r) for d, r in card.dimensions.items()]
    lines += [row("Weighted", "", card.weighted), "", "Track quality"]
    for track, r in card.track_quality.items():
        flag = "  Regression" if track in verdict.regressions else ""
        lines.append(row(track, "", r) + flag)
    notes = ([f"challenger: {n}" for n in card.challenger_notes]
             + [f"Baseline: {n}" for n in card.baseline_notes])
    if notes:
        lines += ["", "Notes"] + [f"  {n}" for n in notes]
    if verdict.warnings:
        lines += ["", "Warnings"] + [f"  {w}" for w in verdict.warnings]
    return "\n".join(lines)


def row(name: str, weight: str, r: ScoreRow) -> str:
    return f"{name:<20}{weight:>8}{r.challenger:>12.2f}{r.baseline:>10.2f}{r.delta:>+8.2f}"
