"""The Comparisons and Decision Log tabs.

Comparisons: choose a challenger Run and a Baseline Run; the Workspace compares them
and this module shows the scorecard and its Verdict, which one key logs to the Decision
Log. Decision Log: browse every entry, newest first, and reopen the Comparison behind a
Verdict. Every rule lives in the Workspace; this module only shows what it reports.
"""

from __future__ import annotations

from typing import Any

import yaml
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from bench.core.settings import DIMENSIONS
from bench.core.verdicts import Comparison
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
        except Exception as exc:  # any refusal, whatever its kind, is shown inline
            self._status(f"Not compared: {exc}")
            return
        self.comparison = comparison
        self.query_one("#cmp-scorecard", Static).update(
            scorecard(comparison, self.workspace.settings.weights)
        )
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
        path = self.workspace.log_verdict(
            comparison.verdict, judge_setup_hash=comparison.judge_setup_hash
        )
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
        yield Label("Decision Log, newest first (enter on a Verdict opens its Comparison)")
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
        if entry.get("kind") != "verdict":
            self._status("Only a Verdict has a Comparison behind it.")
            return
        runs = entry.get("runs") or {}
        if not (runs.get("challenger") and runs.get("baseline")):
            self._status("No Comparison: this Verdict needed no Runs.")
            return
        try:
            comparison = self.workspace.compare(runs["challenger"], runs["baseline"])
        except Exception as exc:  # any refusal, whatever its kind, is shown inline
            self._status(f"The Comparison cannot be reopened: {exc}")
            return
        self._status("")
        weights = self.workspace.settings.weights
        self.app.push_screen(ComparisonScreen(comparison, weights, logged=entry.get("label")))

    def _status(self, message: str) -> None:
        self.query_one("#log-status", Static).update(message)


class ComparisonScreen(Screen[None]):
    """The Comparison behind a logged Verdict, recomputed from its Runs; escape goes back."""

    DEFAULT_CSS = "ComparisonScreen Static { height: auto; padding: 0 1; }"
    BINDINGS = [Binding("escape", "leave", "Back to the Decision Log")]

    def __init__(
        self, comparison: Comparison, weights: dict[str, float], *, logged: str | None
    ) -> None:
        super().__init__()
        self.comparison = comparison
        self.weights = weights
        self.logged = logged

    def compose(self) -> ComposeResult:
        verdict = self.comparison.verdict
        note = f"Logged Verdict: {self.logged}. Recomputed from the Runs as they are now."
        if self.logged != verdict.label:
            note += f" It now reads {verdict.label}, as the Runs changed since it was logged."
        with VerticalScroll():
            yield Static(note, id="comparison-note", markup=False)
            yield Static(
                scorecard(self.comparison, self.weights), id="comparison-scorecard",
                markup=False,
            )
        yield Footer()

    def action_leave(self) -> None:
        self.dismiss(None)


def entry_label(entry: dict[str, Any]) -> str:
    date, kind = entry.get("date", "?"), entry.get("kind", "?")
    candidate = (entry.get("candidate") or {}).get("id", "?")
    if kind == "verdict":
        baseline = (entry.get("baseline") or {}).get("id", "?")
        return f"{date}  Verdict {entry.get('label')}: {candidate} vs Baseline {baseline}"
    if kind == "uncertified_judge_override":
        return f"{date}  uncertified Judge override: Run {entry.get('run')}"
    return f"{date}  {kind.replace('_', ' ')}: {candidate}"


def scorecard(comparison: Comparison, weights: dict[str, float]) -> str:
    verdict = comparison.verdict
    c, b = comparison.challenger_score, comparison.baseline_score
    judge = comparison.judge_setup_hash or "differs between the Runs, or unknown"
    lines = [f"Verdict: {verdict.label}"]
    lines += [f"  - {reason}" for reason in verdict.rationale]
    lines += [
        "",
        f"{verdict.candidate_id} (Run {comparison.challenger.id}) vs Baseline "
        f"{verdict.baseline_id} (Run {comparison.baseline.id})",
        f"Suite: {verdict.suite}, {verdict.private_tasks} Private Tasks; "
        f"full adopt: {'yes' if verdict.full_adopt else 'no'}",
        f"Judge Setup: {judge}",
        "",
        f"{'Dimension':<20}{'weight':>8}{'challenger':>12}{'Baseline':>10}{'delta':>8}",
    ]
    lines += [
        row(d, f"{weights[d]:g}%", c.dimensions[d], b.dimensions[d]) for d in DIMENSIONS
    ]
    lines += [row("Weighted", "", c.weighted, b.weighted), "", "Track quality"]
    for track, baseline_quality in b.track_quality.items():
        flag = "  Regression" if track in verdict.regressions else ""
        challenger_quality = c.track_quality.get(track, baseline_quality)
        lines.append(row(track, "", challenger_quality, baseline_quality) + flag)
    notes = [f"challenger: {n}" for n in c.notes] + [f"Baseline: {n}" for n in b.notes]
    if notes:
        lines += ["", "Notes"] + [f"  {n}" for n in notes]
    if verdict.warnings:
        lines += ["", "Warnings"] + [f"  {w}" for w in verdict.warnings]
    return "\n".join(lines)


def row(name: str, weight: str, challenger: float, baseline: float) -> str:
    delta = challenger - baseline
    return f"{name:<20}{weight:>8}{challenger:>12.2f}{baseline:>10.2f}{delta:>+8.2f}"
