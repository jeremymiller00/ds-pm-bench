"""The Calibration tab: add Outputs to the Calibration Set, score them blind, run the
Judge on the set, see its agreement per Criterion and certify the Judge Setup.

Everything goes through the Workspace; this module only shows what it reports. The
scoring screen is blind (ADR-0002): it shows an item's content and nothing of the Judge's
scores, the Run or the Candidate. A judge model is called in a Textual worker, off the UI.
"""

from __future__ import annotations

import asyncio

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from bench.core.calibration import Agreement, CriterionAgreement
from bench.core.errors import CalibrationError, JudgingError, RunMismatch, UnknownRunError
from bench.core.judging import JUDGE_FILE, BlindOutput
from bench.core.rubric import Criterion
from bench.core.workspace import Workspace

# What adding an item can be refused with, shown inline.
ADD_ERRORS = (CalibrationError, RunMismatch, UnknownRunError, LookupError)


class CalibrationPane(Vertical):
    DEFAULT_CSS = """
    CalibrationPane #cal-add-controls, CalibrationPane #cal-actions { height: auto; }
    CalibrationPane Select { width: 1fr; }
    CalibrationPane OptionList { height: auto; max-height: 10; }
    CalibrationPane #cal-setup, CalibrationPane #cal-agreement, CalibrationPane #cal-status {
        height: auto; padding: 0 1;
    }
    """

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        yield Static("", id="cal-setup", markup=False)
        with Horizontal(id="cal-add-controls"):
            yield Select([], prompt="Run", id="cal-run")
            yield Select([], prompt="Task", id="cal-task")
            yield Button("Add to Calibration Set", id="cal-add")
        yield Label("Items awaiting your blind score (enter to score one)")
        yield OptionList(id="cal-unscored")
        with Horizontal(id="cal-actions"):
            yield Button("Judge the Calibration Set", id="cal-judge")
            yield Button("Certify Judge Setup", id="cal-certify", variant="primary")
        yield Static("", id="cal-agreement", markup=False)
        yield Static("", id="cal-status", markup=False)

    def on_mount(self) -> None:
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        """Re-read everything the tab shows: the Judge Setup, Runs, items, agreement."""
        ws = self.workspace
        self.query_one("#cal-setup", Static).update(
            setup_status(ws.judge_setup.hash if ws.judge_setup else None, ws.judge_certified)
        )
        runs = self.query_one("#cal-run", Select)
        chosen = runs.value
        run_ids = ws.run_ids()
        runs.set_options([(r, r) for r in run_ids])
        if chosen in run_ids:
            runs.value = chosen
        unscored = self.query_one("#cal-unscored", OptionList)
        unscored.clear_options()
        unscored.add_options(Option(i.id, id=i.id) for i in ws.unscored_calibration_items())
        self.query_one("#cal-agreement", Static).update(
            agreement_summary(ws.calibration_agreement()) if ws.judge_setup else ""
        )

    @on(Select.Changed, "#cal-run")
    def _offer_tasks(self, event: Select.Changed) -> None:
        tasks = self.query_one("#cal-task", Select)
        if event.value is Select.BLANK:
            tasks.set_options([])
            return
        try:
            run = self.workspace.resume_run(str(event.value))
        except ADD_ERRORS as exc:
            self._status(str(exc))
            return
        tasks.set_options([(t.id, t.id) for t in run.tasks])

    @on(Button.Pressed, "#cal-add")
    def _add(self) -> None:
        run_id = self.query_one("#cal-run", Select).value
        task_id = self.query_one("#cal-task", Select).value
        if run_id is Select.BLANK or task_id is Select.BLANK:
            self._status("Choose a Run and one of its Tasks to add.")
            return
        try:
            item = self.workspace.add_calibration_item(str(run_id), str(task_id))
        except ADD_ERRORS as exc:
            self._status(str(exc))
            return
        self._refresh()
        self._status(f"Added {item.id} to the Calibration Set; score it blind.")

    @on(OptionList.OptionSelected, "#cal-unscored")
    def _score(self, event: OptionList.OptionSelected) -> None:
        item = next(
            (i for i in self.workspace.unscored_calibration_items() if i.id == event.option.id),
            None,
        )
        if item is None:
            return

        def closed(scored: bool | None) -> None:
            self._refresh()
            if scored:
                self._status(f"{item.id} scored.")

        self.app.push_screen(CalibrationScoreScreen(self.workspace, item), closed)

    @on(Button.Pressed, "#cal-judge")
    def _judge(self) -> None:
        self.query_one("#cal-judge", Button).disabled = True
        self._status("Judging the Calibration Set...")
        self.run_worker(self._judge_set(), group="calibration-judge", exclusive=True)

    async def _judge_set(self) -> None:
        try:
            judged = await asyncio.to_thread(self.workspace.judge_calibration_set)
        except JudgingError as exc:
            self._status(f"Not judged: {exc}")
        else:
            self._refresh()
            self._status(
                f"The Judge scored {len(judged)} item(s)." if judged
                else "Nothing to judge: the Judge Setup has scored every item."
            )
        self.query_one("#cal-judge", Button).disabled = False

    @on(Button.Pressed, "#cal-certify")
    def _certify(self) -> None:
        try:
            self.workspace.certify_judge()
        except (CalibrationError, JudgingError) as exc:
            self._status(str(exc))
            return
        self._refresh()
        self._status(
            f"Judge Setup {self.workspace.judge_setup.hash} certified and logged to the "
            "Decision Log."
        )

    def _status(self, message: str) -> None:
        self.query_one("#cal-status", Static).update(message)


class CalibrationScoreScreen(Screen[bool | None]):
    """Score one Calibration Set item blind: its input, Gold Answer, Criteria and Output,
    and a 1-5 box per Criterion. Nothing of the Judge's scores, Run or Candidate.

    Dismisses with True once scored, None when left without scoring.
    """

    DEFAULT_CSS = """
    CalibrationScoreScreen Static { height: auto; padding: 0 1; }
    CalibrationScoreScreen #cal-score-error { color: $error; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Save scores", priority=True),
        Binding("escape", "leave", "Back without saving"),
    ]

    def __init__(self, workspace: Workspace, item: BlindOutput) -> None:
        super().__init__()
        self.workspace = workspace
        self.item = item

    def compose(self) -> ComposeResult:
        item = self.item
        with VerticalScroll():
            yield Static(f"Calibration item {item.id}: score it blind, 1-5 per Criterion.",
                         markup=False)
            yield from blind_content(item.input, item.gold_answer, item.output)
            yield from score_fields(item.criteria)
            yield Static("", id="cal-score-error", markup=False)
        yield Footer()

    def action_save(self) -> None:
        try:
            scores = read_scores(self, [c.id for c in self.item.criteria])
            self.workspace.score_calibration_item(self.item.id, scores)
        except (ValueError, CalibrationError) as exc:
            self.query_one("#cal-score-error", Static).update(str(exc))
            return
        self.dismiss(True)

    def action_leave(self) -> None:
        self.dismiss(None)


def blind_content(task_input: str, gold_answer: str, output: str) -> ComposeResult:
    """What a human scoring an Output blind reads: nothing that names who wrote it."""
    yield Label("Canonical Prompt and input")
    yield Static(task_input, markup=False)
    yield Label("Gold Answer")
    yield Static(gold_answer, markup=False)
    yield Label("Output")
    yield Static(output, markup=False)


def score_fields(criteria: tuple[Criterion, ...]) -> ComposeResult:
    for criterion in criteria:
        yield Static(f"{criterion.name} ({criterion.id}): {criterion.description}",
                     markup=False)
        yield Input(placeholder="1-5", id=f"score-{criterion.id}")


def read_scores(screen: Screen, criteria: list[str]) -> dict[str, int]:
    """The whole-number score typed for each Criterion; ValueError names a bad one."""
    scores = {}
    for cid in criteria:
        value = screen.query_one(f"#score-{cid}", Input).value.strip()
        try:
            scores[cid] = int(value)
        except ValueError:
            raise ValueError(f"{cid}: type a whole number 1-5, not {value!r}") from None
    return scores


def setup_status(judge_setup_hash: str | None, certified: bool) -> str:
    if judge_setup_hash is None:
        return f"No Judge Setup: {JUDGE_FILE} is missing."
    state = "certified" if certified else "not certified"
    return f"Judge Setup {judge_setup_hash}: {state}"


def agreement_summary(agreement: Agreement) -> str:
    lines = [f"Agreement of Judge Setup {agreement.judge_setup_hash} with your scores "
             f"(thresholds {agreement.within_one_point_threshold:.0%} within 1 point, "
             f"weighted kappa {agreement.weighted_kappa_threshold:.2f})"]
    lines += [f"  {cid}: {agreement_line(a)}" for cid, a in agreement.criteria.items()]
    lines.append(f"  overall: {agreement_line(agreement.overall)}")
    if agreement.shortfalls:
        lines += ["Not certifiable yet:"] + [f"  {s}" for s in agreement.shortfalls]
    else:
        lines.append("Meets every threshold: ready to certify.")
    return "\n".join(lines)


def agreement_line(a: CriterionAgreement) -> str:
    if a.items == 0:
        return "no scores to compare yet"
    kappa = "n/a" if a.weighted_kappa is None else f"{a.weighted_kappa:.2f}"
    return f"{a.within_one_point:.0%} within 1 point, weighted kappa {kappa} ({a.items} scores)"
