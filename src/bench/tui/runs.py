"""The Runs tab: start or resume a Run, then work its Tasks by hand.

Everything goes through the Workspace and its Attempt; this module only shows
what they report and passes on what the person types.
"""

from __future__ import annotations

import subprocess

from textual import on
from textual.app import ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from bench.core.attempts import Attempt
from bench.core.errors import (
    AttemptError,
    ClearanceError,
    ConfigError,
    GateNotPassed,
    RunExists,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
)
from bench.core.kinds import Clearance, Suite
from bench.core.manual_time import ManualTimeStopwatch
from bench.core.runs import OutputRecord, Run
from bench.core.tasks import Task
from bench.core.workspace import Workspace

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

    def compose(self) -> ComposeResult:
        # A Run here is worked by hand, so only manual-assisted Candidates are offered.
        candidates = [
            (c.id, c.id) for c in self.workspace.current_candidates if c.manual_assisted
        ]
        with Horizontal(id="run-controls"):
            yield Select(candidates, prompt="Candidate", id="run-candidate")
            yield Select(
                [(str(suite), str(suite)) for suite in Suite],
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
        yield Label("Pending Tasks (enter to work one)")
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
        """A Suite can be chosen only when the Clearance allows more than public."""
        suite = self.query_one("#run-suite", Select)
        if event.value is Select.BLANK:
            suite.disabled = True
            return
        clearance = self.workspace.candidate(str(event.value)).clearance
        suite.disabled = clearance != Clearance.PUBLIC_AND_PRIVATE
        suite.value = str(clearance or Suite.PUBLIC_ONLY)

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
                suite=None if suite.disabled else str(suite.value),
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
        self._show_progress()

    def _show_progress(self) -> None:
        run = self.run
        if run is None:
            return
        self.query_one("#run-progress", Static).update(run_progress(run))
        tasks = self.query_one("#run-tasks", OptionList)
        tasks.clear_options()
        tasks.add_options(Option(task_id, id=task_id) for task_id in run.pending)

    @on(OptionList.OptionSelected, "#run-tasks")
    def _work_task(self, event: OptionList.OptionSelected) -> None:
        run = self.run
        if run is None or event.option.id is None:
            return
        try:
            attempt = self.workspace.begin_manual_task(run, event.option.id)
        except RUN_ERRORS as exc:
            self._status(str(exc))
            return

        def saved(record: OutputRecord | None) -> None:
            self._show_progress()
            if record is None:
                self._status(f"{attempt.task_id} abandoned; nothing recorded.")
            else:
                self._status(output_summary(record))

        self.app.push_screen(ManualTaskScreen(attempt), saved)

    def _status(self, message: str) -> None:
        self.query_one("#run-status", Static).update(message)


class ManualTaskScreen(Screen[OutputRecord | None]):
    """One Task worked by hand: copy the prompt, paste the raw Output, edit, save.

    Dismisses with the saved OutputRecord, or None when abandoned.
    """

    DEFAULT_CSS = """
    ManualTaskScreen TextArea { height: 1fr; }
    ManualTaskScreen #task-timer { height: auto; text-style: bold; }
    ManualTaskScreen #task-help, ManualTaskScreen #task-error { height: auto; }
    ManualTaskScreen #task-error { color: $error; }
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
            yield Static(f"Task {self.attempt.task_id}", markup=False)
            yield Static("", id="task-timer", markup=False)
            yield Static(PASTE_HELP, id="task-help", markup=False)
            yield Label("Canonical Prompt and input")
            yield TextArea(self.attempt.prompt, id="task-prompt", read_only=True)
            yield Label("Raw Output (paste it here, then ctrl+s)")
            yield TextArea("", id="task-raw")
            yield Static("", id="task-error", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#task-raw", TextArea).focus()
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
        scroll.mount(Label("Final (edit here, then ctrl+s to save)"), before="#task-error")
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
        except (AttemptError, SuspendNotSupported, subprocess.CalledProcessError) as exc:
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


EDITOR_NOTE = (
    "e opens the draft in $EDITOR; the app suspends and all the time it is open counts "
    "as active time."
)
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
    done = len(run.tasks) - len(run.pending)
    lines = [f"Run {run.id} ({run.suite}): {done}/{len(run.tasks)} Tasks recorded"]
    if run.excluded:
        lines.append("Excluded, still a placeholder:")
        lines += [f"  {task_id}: {', '.join(what)}" for task_id, what in run.excluded.items()]
    return "\n".join(lines)


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
