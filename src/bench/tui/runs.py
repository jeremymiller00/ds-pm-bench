"""The Runs tab: start or resume a Run, work its Tasks by hand or send them to an
automated Candidate and edit what comes back, then judge the Run.

Everything goes through the Workspace and its Attempt; this module only shows
what they report and passes on what the person types. Calls to a Candidate or a judge
model run in Textual workers, off the UI, so a slow model never freezes the screen.
"""

from __future__ import annotations

import asyncio

from textual import on
from textual.app import ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from bench.core.attempts import Attempt
from bench.core.automated import ReturnedOutput
from bench.core.errors import (
    AttemptError,
    CandidateToolError,
    CandidateUnavailable,
    ClearanceError,
    ConfigError,
    EditorError,
    GateNotPassed,
    JudgingError,
    MissingSecret,
    RunExists,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
)
from bench.core.judging import JUDGE_FILE, JudgeSetup, TaskJudging
from bench.core.kinds import Suite
from bench.core.manual_time import ManualTimeStopwatch
from bench.core.runs import OutputRecord, Run
from bench.core.tasks import Task
from bench.core.workspace import Workspace
from bench.tui.spot_checks import SpotCheckScreen

# What the Workspace refuses when starting or resuming a Run, shown inline.
RUN_ERRORS = (
    AttemptError,
    ClearanceError,
    GateNotPassed,
    RunExists,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
)
# What can stop one automated Task from being sent; shown against that Task.
SEND_ERRORS = (AttemptError, CandidateToolError, CandidateUnavailable, ClearanceError,
               GateNotPassed, MissingSecret)
# Every judging refusal or failure (a JudgingError) is shown on the Judge screen.


class RunsPane(Vertical):
    DEFAULT_CSS = """
    RunsPane #run-controls, RunsPane #run-resume-controls { height: auto; }
    RunsPane Select { width: 1fr; }
    RunsPane OptionList { height: auto; max-height: 12; }
    RunsPane #run-status { height: auto; padding: 0 1; }
    """

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.run: Run | None = None
        self.send_errors: dict[str, str] = {}  # Task id -> why its last send failed

    def compose(self) -> ComposeResult:
        candidates = [(c.id, c.id) for c in self.workspace.current_candidates]
        with Horizontal(id="run-controls"):
            yield Select(candidates, prompt="Candidate", id="run-candidate")
            yield Select(
                [(str(suite), suite) for suite in Suite],
                prompt="Suite",
                id="run-suite",
                disabled=True,
            )
            yield Button("Start Run", id="run-start", variant="primary")
        with Horizontal(id="run-resume-controls"):
            yield Select(
                [(run_id, run_id) for run_id in self.workspace.run_ids()],
                prompt="Run to resume",
                id="run-existing",
            )
            yield Button("Resume Run", id="run-resume")
        yield Static("No Run open.", id="run-progress", markup=False)
        with Horizontal(id="run-actions"):
            yield Button("Send Tasks", id="run-send", disabled=True)
            yield Button("Judge Run", id="run-judge", disabled=True)
            yield Button("Spot-checks", id="run-spot-check", disabled=True)
        yield Label("Pending Tasks (enter to work or edit one)")
        yield OptionList(id="run-tasks")
        yield Label("Tasks with Manual Time still a placeholder (enter to time one by hand)")
        yield OptionList(id="manual-time-tasks")
        yield Static("", id="run-status", markup=False)

    def on_mount(self) -> None:
        self._show_untimed()

    def _show_untimed(self) -> None:
        untimed = self.query_one("#manual-time-tasks", OptionList)
        untimed.clear_options()
        untimed.add_options(
            Option(t.id, id=t.id) for t in self.workspace.tasks if t.manual_time_minutes is None
        )

    @on(OptionList.OptionSelected, "#manual-time-tasks")
    def _time_task(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        stopwatch = self.workspace.begin_manual_time(event.option.id)

        def recorded(task: Task | None) -> None:
            if task is None:
                self._status(f"{stopwatch.task_id}: timing cancelled; nothing recorded.")
                return
            self._show_untimed()
            self._status(
                f"{task.id} Manual Time recorded: {task.manual_time_minutes:g} minutes "
                f"(v{task.version}). A Run started from now on can include it."
            )

        self.app.push_screen(ManualTimeScreen(stopwatch), recorded)

    @on(Select.Changed, "#run-candidate")
    def _offer_suites(self, event: Select.Changed) -> None:
        """Offer the Suites the Candidate's Clearance allows; a choice only when there
        is more than one."""
        select = self.query_one("#run-suite", Select)
        suites = () if event.value is Select.BLANK else (
            self.workspace.suites_for(str(event.value))
        )
        select.disabled = len(suites) < 2
        if suites:
            select.set_options([(str(s), s) for s in suites])
            select.value = suites[0]

    @on(Button.Pressed, "#run-start")
    def _start(self) -> None:
        candidate_id = self.query_one("#run-candidate", Select).value
        if candidate_id is Select.BLANK:
            self._status("Choose a Candidate to start a Run.")
            return
        suite = self.query_one("#run-suite", Select)
        try:
            run = self.workspace.start_run(
                str(candidate_id),
                suite=None if suite.disabled else suite.value,
            )
        except RUN_ERRORS as exc:
            self._status(str(exc))
            return
        self._open(run)
        existing = self.query_one("#run-existing", Select)
        existing.set_options([(run_id, run_id) for run_id in self.workspace.run_ids()])
        self._status(f"Started Run {run.id}.")

    @on(Button.Pressed, "#run-resume")
    def _resume(self) -> None:
        run_id = self.query_one("#run-existing", Select).value
        if run_id is Select.BLANK:
            self._status("Choose a Run to resume.")
            return
        try:
            run = self.workspace.resume_run(str(run_id))
        except RUN_ERRORS as exc:
            self._status(str(exc))
            return
        self._open(run)
        self._status(f"Resumed Run {run.id}.")

    def _open(self, run: Run) -> None:
        self.run = run
        self.send_errors = {}
        self.query_one("#run-send", Button).disabled = run.candidate.manual_assisted
        self._show_progress()

    def _show_progress(self) -> None:
        run = self.run
        if run is None:
            return
        self.query_one("#run-progress", Static).update(run_progress(run))
        # Judging needs a finished Run: every Task's Output recorded.
        self.query_one("#run-judge", Button).disabled = bool(run.pending)
        # Judging queues the Spot-checks.
        self.query_one("#run-spot-check", Button).disabled = not run.spot_check_queue
        tasks = self.query_one("#run-tasks", OptionList)
        tasks.clear_options()
        if run.candidate.manual_assisted:
            tasks.add_options(Option(task_id, id=task_id) for task_id in run.pending)
            return
        tasks.add_options(
            Option(self._automated_label(run, task_id), id=task_id) for task_id in run.pending
        )

    @on(Button.Pressed, "#run-judge")
    def _judge(self) -> None:
        run = self.run
        if run is None:
            return

        def closed(judged: tuple[TaskJudging, ...] | None) -> None:
            self._show_progress()
            if judged:
                self._status(f"Judged {len(judged)} Task(s) of Run {run.id}.")

        self.app.push_screen(JudgeScreen(self.workspace, run), closed)

    @on(Button.Pressed, "#run-spot-check")
    def _spot_check(self) -> None:
        run = self.run
        if run is None:
            return

        def closed(_: None) -> None:
            left = run.spot_checks_left
            self._status(
                f"Spot-checks of Run {run.id}: {len(left)} queued Task(s) left."
                if left else f"Every queued Spot-check of Run {run.id} is done."
            )

        self.app.push_screen(SpotCheckScreen(self.workspace, run), closed)

    def _automated_label(self, run: Run, task_id: str) -> str:
        if self.workspace.returned_output(run, task_id) is not None:
            return f"{task_id}: raw Output ready to edit"
        if task_id in self.send_errors:
            return f"{task_id}: failed, send again to retry"
        return f"{task_id}: not sent"

    @on(Button.Pressed, "#run-send")
    def _send(self) -> None:
        if self.run is not None:
            self.run_worker(self._send_tasks(self.run), group="send", exclusive=True)

    async def _send_tasks(self, run: Run) -> None:
        """Send each Task the Workspace says is sendable, one after another, off the UI
        thread; a Task that fails is noted against it and the rest still go."""
        button = self.query_one("#run-send", Button)
        button.disabled = True
        to_send = self.workspace.sendable_tasks(run)
        lines = []
        for n, task_id in enumerate(to_send, 1):
            self._status(f"Sending {task_id} ({n} of {len(to_send)})...")
            try:
                returned = await asyncio.to_thread(
                    self.workspace.run_automated_task, run, task_id
                )
            except SEND_ERRORS as exc:
                self.send_errors[task_id] = str(exc)
                lines.append(f"{task_id} failed: {exc}")
            else:
                self.send_errors.pop(task_id, None)
                lines.append(returned_summary(returned))
            self._show_progress()
        button.disabled = False
        self._status("\n".join(lines) or "Nothing to send: every pending Task is ready to edit.")

    @on(OptionList.OptionSelected, "#run-tasks")
    def _work_task(self, event: OptionList.OptionSelected) -> None:
        run = self.run
        if run is None or event.option.id is None:
            return
        manual = run.candidate.manual_assisted
        try:
            attempt = self.workspace.begin_attempt(run, event.option.id)
        except RUN_ERRORS as exc:
            self._status(str(exc))
            return

        def saved(record: OutputRecord | None) -> None:
            self._show_progress()
            if record is not None:
                self._status(output_summary(record))
            elif manual:
                self._status(f"{attempt.task_id} abandoned; nothing recorded.")
            else:
                self._status(
                    f"{attempt.task_id} Attempt abandoned; nothing recorded, the raw "
                    "Output kept, ready to edit in another Attempt."
                )

        self.app.push_screen(AttemptScreen(attempt), saved)

    def _status(self, message: str) -> None:
        self.query_one("#run-status", Static).update(message)


class AttemptScreen(Screen[OutputRecord | None]):
    """One Task's Attempt, of either kind. A manual-assisted one: copy the prompt, paste
    the raw Output, edit, save. An automated one opens with the Returned Output already
    in, ready to edit.

    Dismisses with the saved OutputRecord, or None when abandoned.
    """

    DEFAULT_CSS = """
    AttemptScreen TextArea { height: 1fr; }
    AttemptScreen #task-timer { height: auto; text-style: bold; }
    AttemptScreen #task-help, AttemptScreen #task-error { height: auto; }
    AttemptScreen #task-error { color: $error; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Submit / Save", priority=True),
        Binding("escape", "leave_text", "Leave text box"),
        Binding("c", "copy_prompt", "Copy prompt"),
        Binding("p", "pause_resume", "Pause / resume"),
        Binding("e", "external_editor", "$EDITOR"),
        Binding("a", "abandon", "Abandon"),
    ]

    def __init__(self, attempt: Attempt) -> None:
        super().__init__()
        self.attempt = attempt

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            raw = self.attempt.raw
            yield Static(f"Task {self.attempt.task_id}", markup=False)
            yield Static("", id="task-timer", markup=False)
            yield Static(PASTE_HELP if raw is None else EDIT_HELP, id="task-help",
                         markup=False)
            yield Label("Canonical Prompt and input")
            yield TextArea(self.attempt.prompt, id="task-prompt", read_only=True)
            if raw is None:
                yield Label("Raw Output (paste it here, then ctrl+s)")
                yield TextArea("", id="task-raw")
            else:
                yield Label("Raw Output (as the Candidate returned it)")
                yield TextArea(raw, id="task-raw", read_only=True)
                yield Label(FINAL_LABEL)
                yield TextArea(self.attempt.draft or "", id="task-draft")
            yield Static("", id="task-error", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        editing = "#task-raw" if self.attempt.raw is None else "#task-draft"
        self.query_one(editing, TextArea).focus()
        self.refresh_timer()
        self.set_interval(1, self.refresh_timer)

    def refresh_timer(self) -> None:
        """Show the Attempt's running timer; called each second and after every action."""
        s = self.attempt
        state = "paused" if s.paused else "running"
        latency = "" if s.latency_seconds is None else (
            f"  latency {clock_time(s.latency_seconds)}"
        )
        self.query_one("#task-timer", Static).update(
            f"Active {clock_time(s.active_seconds)} ({state}){latency}"
        )

    def action_leave_text(self) -> None:
        self.set_focus(None)

    def action_copy_prompt(self) -> None:
        self.app.copy_to_clipboard(self.attempt.prompt)
        self._error("Prompt and input copied to the clipboard.")

    def action_save(self) -> None:
        if self.attempt.raw is None:
            self._submit_raw()
        else:
            self._finish()

    def _submit_raw(self) -> None:
        raw = self.query_one("#task-raw", TextArea)
        try:
            self.attempt.submit_raw(raw.text)
        except AttemptError as exc:
            self._error(str(exc))
            return
        raw.read_only = True
        draft = TextArea(self.attempt.draft or "", id="task-draft")
        scroll = self.query_one(VerticalScroll)
        scroll.mount(Label(FINAL_LABEL), before="#task-error")
        scroll.mount(draft, before="#task-error")
        self.query_one("#task-help", Static).update(EDIT_HELP)
        draft.focus()
        self._error("")
        self.refresh_timer()

    def _finish(self) -> None:
        final = self.query_one("#task-draft", TextArea).text
        try:
            record = self.attempt.finish(final)
        except AttemptError as exc:
            self._error(str(exc))
            return
        self.dismiss(record)

    def action_pause_resume(self) -> None:
        s = self.attempt
        try:
            s.resume() if s.paused else s.pause()
        except AttemptError as exc:
            self._error(str(exc))
            return
        self.query_one("#task-draft", TextArea).read_only = s.paused
        self._error("Paused: the timer is stopped; p resumes." if s.paused else "")
        self.refresh_timer()

    def action_external_editor(self) -> None:
        try:
            self.attempt.check_editable()  # refuse before giving up the terminal
            with self.app.suspend():
                draft = self.attempt.edit_externally()
        except (AttemptError, EditorError, SuspendNotSupported) as exc:
            self._error(str(exc))
            return
        self.query_one("#task-draft", TextArea).text = draft
        self._error("")
        self.refresh_timer()

    def action_abandon(self) -> None:
        self.attempt.abandon()
        self.dismiss(None)

    def _error(self, message: str) -> None:
        self.query_one("#task-error", Static).update(message)


FINAL_LABEL = "Final (edit here, then ctrl+s to save)"
EDITOR_NOTE = (
    "e opens the draft in $EDITOR; the app suspends and all the time it is open counts "
    "as active time."
)
class JudgeScreen(Screen[tuple[TaskJudging, ...] | None]):
    """Judge a Run: shows the Judge Setup and its hash and, unless it is certified, asks
    for an explicit override that the Workspace logs. Then shows each Task's
    Criterion scores with every judge model's reason, Panel disagreements and bias
    warnings.

    Dismisses with what was judged, or None when left without judging.
    """

    DEFAULT_CSS = """
    JudgeScreen #judge-setup, JudgeScreen #judge-certification { height: auto; padding: 0 1; }
    JudgeScreen #judge-certification { color: $warning; }
    JudgeScreen #judge-results { height: auto; padding: 0 1; }
    """
    BINDINGS = [Binding("escape", "leave", "Back to the Run")]

    def __init__(self, workspace: Workspace, run: Run) -> None:
        super().__init__()
        self.workspace = workspace
        self.run = run
        self.judged: tuple[TaskJudging, ...] | None = None
        self.certified = workspace.judge_certified

    def compose(self) -> ComposeResult:
        setup = self.workspace.judge_setup
        with VerticalScroll():
            yield Static(f"Judge Run {self.run.id}", markup=False)
            yield Static(judge_setup_summary(setup), id="judge-setup", markup=False)
            if self.certified:
                yield Static(CERTIFIED_NOTICE, id="judge-certification", markup=False)
                yield Button("Judge Run", id="judge-run", variant="primary")
            else:
                yield Static(UNCERTIFIED_NOTICE, id="judge-certification", markup=False)
                yield Button("Judge with uncertified override (logged)", id="judge-override",
                             variant="warning", disabled=setup is None)
            yield Static("", id="judge-results", markup=False)
        yield Footer()

    @on(Button.Pressed, "#judge-override, #judge-run")
    def _start_judging(self, event: Button.Pressed) -> None:
        event.button.disabled = True
        self._results("Judging...")
        self.run_worker(self._judge(event.button), group="judge", exclusive=True)

    async def _judge(self, button: Button) -> None:
        try:
            judged = await asyncio.to_thread(
                self.workspace.judge_run, self.run, allow_uncertified=not self.certified
            )
        except JudgingError as exc:
            self._results(f"Not judged: {exc}")
            button.disabled = False
            return
        self.judged = judged
        self._results(judging_summary(judged, overridden=not self.certified))

    def action_leave(self) -> None:
        self.dismiss(self.judged)

    def _results(self, message: str) -> None:
        self.query_one("#judge-results", Static).update(message)


CERTIFIED_NOTICE = (
    "This Judge Setup is certified on the human-scored Calibration Set: judging needs no "
    "override. Escape goes back without judging."
)
UNCERTIFIED_NOTICE = (
    "This Judge Setup is not certified: certify it in the Calibration tab, against the "
    "human-scored Calibration Set. Until then judging needs an explicit override, and every "
    "override is logged to the Decision Log with the Judge Setup hash. Escape goes back "
    "without judging."
)


def judge_setup_summary(setup: JudgeSetup | None) -> str:
    if setup is None:
        return f"No Judge Setup: {JUDGE_FILE} is missing, so this Run cannot be judged."
    models = ", ".join(m.label for m in setup.models)
    return (
        f"Judge Setup {setup.hash}: {setup.mode}, {models}; cleared for "
        f"{setup.clearance}; prompt {setup.prompt_file}"
    )


def judging_summary(judged: tuple[TaskJudging, ...], *, overridden: bool = True) -> str:
    if not judged:
        return "Nothing to judge: every recorded Output is already scored."
    lines = ["Judged with the uncertified override logged to the Decision Log." if overridden
             else "Judged with the certified Judge Setup."]
    for judging in judged:
        lines += ["", f"Task {judging.task_id}"]
        if judging.same_family:
            lines.append(
                "  Warning: a judge model is the same family as the Candidate, a bias "
                "risk; "
                "full human check of every Criterion."
            )
        if judging.disagreements:
            lines.append(
                f"  Judge models disagree on {', '.join(judging.disagreements)} by more "
                "than a point; full human check."
            )
        for criterion, score in judging.scores.items():
            flag = "  (disagreement)" if criterion in judging.disagreements else ""
            lines.append(f"  {criterion} {score:g}{flag}")
            lines += [
                f"    {label}: {scores[criterion].reason}"
                + (f" ({scores[criterion].score})" if len(judging.judges) > 1 else "")
                for label, scores in judging.judges.items()
            ]
    return "\n".join(lines)


class ManualTimeScreen(ModalScreen[Task | None]):
    """A stopwatch on a person doing a Task by hand; ctrl+s stops it and records the
    Manual Time, escape cancels. Dismisses with the updated Task, or None."""

    DEFAULT_CSS = """
    ManualTimeScreen #stopwatch { width: 60; height: auto; border: thick $accent;
        background: $surface; padding: 0 1; }
    ManualTimeScreen #stopwatch-error { color: $error; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Stop and save", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, stopwatch: ManualTimeStopwatch) -> None:
        super().__init__()
        self.stopwatch = stopwatch

    def compose(self) -> ComposeResult:
        with Vertical(id="stopwatch"):
            yield Static(
                f"Timing {self.stopwatch.task_id} by hand. Do the Task without the "
                "Candidate, then ctrl+s to stop and record its Manual Time; escape cancels.",
                markup=False,
            )
            yield Static("", id="stopwatch-timer", markup=False)
            yield Static("", id="stopwatch-error", markup=False)

    def on_mount(self) -> None:
        self.refresh_timer()
        self.set_interval(1, self.refresh_timer)

    def refresh_timer(self) -> None:
        self.query_one("#stopwatch-timer", Static).update(
            f"Elapsed {clock_time(self.stopwatch.elapsed_seconds)}"
        )

    def action_save(self) -> None:
        try:
            task = self.stopwatch.finish()
        except ConfigError as exc:
            self.query_one("#stopwatch-error", Static).update(str(exc))
            return
        self.dismiss(task)

    def action_cancel(self) -> None:
        self.dismiss(None)


PASTE_HELP = (
    "The timer is running. Copy the prompt (escape, then c) into the Candidate, paste "
    "its raw Output below and press ctrl+s. Then edit here or in $EDITOR: "
    + EDITOR_NOTE + " a abandons, recording nothing."
)
EDIT_HELP = (
    "Edit the final below and press ctrl+s to save. Outside a text box (escape): "
    "p pauses or resumes the timer; " + EDITOR_NOTE + " a abandons, recording nothing."
)


def run_progress(run: Run) -> str:
    done, total = run.progress
    lines = [f"Run {run.id} ({run.suite}): {done}/{total} Tasks recorded"]
    if run.excluded:
        lines.append("Excluded, still a placeholder:")
        lines += [f"  {task_id}: {', '.join(what)}" for task_id, what in run.excluded.items()]
    return "\n".join(lines)


def returned_summary(returned: ReturnedOutput) -> str:
    cost = f"cost ${returned.cost_usd:.2f}" if returned.cost_usd is not None else (
        f"cost unknown ({returned.cost_note})"
    )
    retried = ", after one retry" if returned.tries > 1 else ""
    return (
        f"{returned.task_id}: raw Output ready to edit, latency "
        f"{clock_time(returned.latency_seconds)}, {cost}{retried}."
    )


def output_summary(record: OutputRecord) -> str:
    cost = "" if record.cost_usd is None else f", cost ${record.cost_usd:.2f}"
    return (
        f"{record.task_id} saved: Edit Distance {record.edit_distance:.2f}, "
        f"active {clock_time(record.active_seconds)}, "
        f"latency {clock_time(record.latency_seconds)}{cost}."
    )


def clock_time(seconds: float) -> str:
    """m:ss, for timers."""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"
