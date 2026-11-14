"""The Runs tab: start or resume a Run, then work its Tasks by hand.

Everything goes through the Workspace and its ManualSession; this module only shows
what they report and passes on what the person types.
"""

from __future__ import annotations

import subprocess

from textual import on
from textual.app import ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from bench.core.errors import (
    ClearanceError,
    GateNotPassed,
    RunExists,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
)
from bench.core.runs import OutputRecord, Run
from bench.core.sessions import ManualSession
from bench.core.workspace import Workspace

# What the Workspace refuses when starting or resuming a Run, shown inline.
RUN_ERRORS = (
    ClearanceError,
    GateNotPassed,
    RunExists,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
    ValueError,
)


class RunsPane(Vertical):
    DEFAULT_CSS = """
    RunsPane #run-controls { height: auto; }
    RunsPane #run-controls Select { width: 1fr; }
    RunsPane #run-tasks { height: auto; max-height: 12; }
    RunsPane #run-status { height: auto; padding: 0 1; }
    """

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.run: Run | None = None

    def compose(self) -> ComposeResult:
        candidates = [(c.id, c.id) for c in self.workspace.candidates]
        with Horizontal(id="run-controls"):
            yield Select(candidates, prompt="Candidate", id="run-candidate")
            yield Button("Start Run", id="run-start", variant="primary")
        yield Static("No Run open.", id="run-progress", markup=False)
        yield Label("Pending Tasks (enter to work one)")
        yield OptionList(id="run-tasks")
        yield Static("", id="run-status", markup=False)

    @on(Button.Pressed, "#run-start")
    def _start(self) -> None:
        candidate_id = self.query_one("#run-candidate", Select).value
        if candidate_id is Select.BLANK:
            self._status("Choose a Candidate to start a Run.")
            return
        try:
            run = self.workspace.start_run(str(candidate_id))
        except RUN_ERRORS as exc:
            self._status(str(exc))
            return
        self._open(run)
        self._status(f"Started Run {run.id}.")

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
            session = self.workspace.begin_manual_task(run, event.option.id)
        except RUN_ERRORS as exc:
            self._status(str(exc))
            return

        def saved(record: OutputRecord | None) -> None:
            self._show_progress()
            if record is None:
                self._status(f"{session.task_id} abandoned; nothing recorded.")
            else:
                self._status(output_summary(record))

        self.app.push_screen(ManualTaskScreen(session), saved)

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

    def __init__(self, session: ManualSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(f"Task {self.session.task_id}", markup=False)
            yield Static("", id="task-timer", markup=False)
            yield Static(PASTE_HELP, id="task-help", markup=False)
            yield Label("Canonical Prompt and input")
            yield TextArea(self.session.prompt, id="task-prompt", read_only=True)
            yield Label("Raw Output (paste it here, then ctrl+s)")
            yield TextArea("", id="task-raw")
            yield Static("", id="task-error", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#task-raw", TextArea).focus()
        self.refresh_timer()
        self.set_interval(1, self.refresh_timer)

    def refresh_timer(self) -> None:
        """Show the session's running timer; called each second and after every action."""
        s = self.session
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
        self.app.copy_to_clipboard(self.session.prompt)
        self._error("Prompt and input copied to the clipboard.")

    def action_save(self) -> None:
        if self.session.raw is None:
            self._submit_raw()
        else:
            self._finish()

    def _submit_raw(self) -> None:
        raw = self.query_one("#task-raw", TextArea)
        try:
            self.session.submit_raw(raw.text)
        except ValueError as exc:
            self._error(str(exc))
            return
        raw.read_only = True
        draft = TextArea(self.session.draft or "", id="task-draft")
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
            record = self.session.finish(final)
        except ValueError as exc:
            self._error(str(exc))
            return
        self.dismiss(record)

    def action_pause_resume(self) -> None:
        s = self.session
        try:
            s.resume() if s.paused else s.pause()
        except ValueError as exc:
            self._error(str(exc))
            return
        self.query_one("#task-draft", TextArea).read_only = s.paused
        self._error("Paused: the timer is stopped; p resumes." if s.paused else "")
        self.refresh_timer()

    def action_external_editor(self) -> None:
        try:
            self.session.check_editable()  # refuse before giving up the terminal
            with self.app.suspend():
                draft = self.session.edit_externally()
        except (ValueError, SuspendNotSupported, subprocess.CalledProcessError) as exc:
            self._error(str(exc))
            return
        self.query_one("#task-draft", TextArea).text = draft
        self._error("")
        self.refresh_timer()

    def action_abandon(self) -> None:
        self.session.abandon()
        self.dismiss(None)

    def _error(self, message: str) -> None:
        self.query_one("#task-error", Static).update(message)


EDITOR_NOTE = (
    "e opens the draft in $EDITOR; the app suspends and all the time it is open counts "
    "as active time."
)
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
