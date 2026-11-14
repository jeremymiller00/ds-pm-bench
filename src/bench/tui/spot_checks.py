"""A judged Run's Spot-check queue: score each queued Output blind, see progress, and add
a checked Output to the Calibration Set.

Everything goes through the Workspace and the Run. Both screens are blind (ADR-0002):
they show the Task id and content, never the Judge's scores, the Run or the Candidate.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Label, OptionList, Static
from textual.widgets.option_list import Option

from bench.core.errors import CalibrationError, RunMismatch, UnknownRunError
from bench.core.judging import BlindOutput
from bench.core.rubric import check_criterion_score
from bench.core.runs import Run
from bench.core.workspace import Workspace
from bench.tui.calibration import blind_content, read_scores, score_fields


class SpotCheckScreen(Screen[None]):
    """A Run's Spot-check queue: the Tasks still to check, those checked, and progress."""

    DEFAULT_CSS = """
    SpotCheckScreen Static { height: auto; padding: 0 1; }
    SpotCheckScreen OptionList { height: auto; max-height: 12; }
    """
    BINDINGS = [Binding("escape", "leave", "Back to the Run")]

    def __init__(self, workspace: Workspace, run: Run) -> None:
        super().__init__()
        self.workspace = workspace
        self.run = run

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Spot-checks: score each queued Output blind, 1-5 per Criterion.",
                         markup=False)
            yield Static("", id="spot-progress", markup=False)
            yield Label("Still to check (enter to score one)")
            yield OptionList(id="spot-queue")
            yield Label("Checked (enter to add its Output to the Calibration Set)")
            yield OptionList(id="spot-checked")
            yield Static("", id="spot-status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        queue, left = self.run.spot_check_queue, self.run.spot_checks_left
        self.query_one("#spot-progress", Static).update(spot_check_progress(queue, left))
        to_check = self.query_one("#spot-queue", OptionList)
        to_check.clear_options()
        to_check.add_options(Option(t, id=t) for t in queue if t in left)
        checked = self.query_one("#spot-checked", OptionList)
        checked.clear_options()
        checked.add_options(Option(t, id=t) for t in queue if t not in left)

    @on(OptionList.OptionSelected, "#spot-queue")
    def _check(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        item = self.workspace.spot_check_item(self.run, event.option.id)

        def closed(checked: bool | None) -> None:
            self._refresh()
            if checked:
                self._status(f"{item.id} Spot-checked.")

        self.app.push_screen(SpotCheckTaskScreen(self.run, item), closed)

    @on(OptionList.OptionSelected, "#spot-checked")
    def _add_to_calibration(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        try:
            item = self.workspace.add_calibration_item(self.run.id, event.option.id)
        except (CalibrationError, RunMismatch, UnknownRunError) as exc:
            self._status(str(exc))
            return
        self._status(f"{event.option.id} added to the Calibration Set as {item.id}.")

    def action_leave(self) -> None:
        self.dismiss(None)

    def _status(self, message: str) -> None:
        self.query_one("#spot-status", Static).update(message)


class SpotCheckTaskScreen(Screen[bool | None]):
    """Spot-check one queued Task blind: its content and a 1-5 box per Criterion left.

    Dismisses with True once saved, None when left without saving.
    """

    DEFAULT_CSS = """
    SpotCheckTaskScreen Static { height: auto; padding: 0 1; }
    SpotCheckTaskScreen #spot-score-error { color: $error; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Save scores", priority=True),
        Binding("escape", "leave", "Back without saving"),
    ]

    def __init__(self, run: Run, item: BlindOutput) -> None:
        super().__init__()
        self.run = run
        self.item = item

    def compose(self) -> ComposeResult:
        item = self.item
        with VerticalScroll():
            yield Static(f"Spot-check Task {item.id}: score it blind, 1-5 per Criterion.",
                         markup=False)
            yield from blind_content(item.input, item.gold_answer, item.output)
            yield from score_fields(item.criteria)
            yield Static("", id="spot-score-error", markup=False)
        yield Footer()

    def action_save(self) -> None:
        try:
            scores = read_scores(self, [c.id for c in self.item.criteria])
            for criterion, score in scores.items():  # all valid before any is recorded
                check_criterion_score(score, f"Spot-check {criterion}")
            for criterion, score in scores.items():
                self.run.record_spot_check(self.item.id, criterion, score)
        except ValueError as exc:
            self.query_one("#spot-score-error", Static).update(str(exc))
            return
        self.dismiss(True)

    def action_leave(self) -> None:
        self.dismiss(None)


def spot_check_progress(queue: tuple[str, ...], left: dict[str, tuple[str, ...]]) -> str:
    if not queue:
        return "No Spot-checks queued: the Run is not judged yet."
    done = len(queue) - len(left)
    state = "all done" if not left else f"{len(left)} left"
    return f"{done}/{len(queue)} queued Tasks Spot-checked ({state})."
