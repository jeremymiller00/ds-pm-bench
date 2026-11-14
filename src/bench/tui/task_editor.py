"""Screens that author Tasks: edit one or scaffold one, saving through the Workspace."""

from __future__ import annotations

from typing import Any

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from bench.core.errors import ConfigError, TaskEditRefused
from bench.core.tasks import Task
from bench.core.workspace import Workspace

EDITOR_CSS = """
#editor { width: 90%; height: 90%; border: thick $accent; background: $surface; padding: 0 1; }
#editor TextArea { height: 6; }
#editor-error { color: $error; }
"""


class FormError(ValueError):
    """A field the form could not turn into a value for the Workspace."""


class TaskEditorScreen(ModalScreen[Task | None]):
    """Edit one Task's fields; dismisses with the saved Task, or None when cancelled."""

    DEFAULT_CSS = EDITOR_CSS
    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, workspace: Workspace, task_id: str) -> None:
        super().__init__()
        self.workspace = workspace
        self.task_id = task_id

    def compose(self) -> ComposeResult:
        task = self.workspace.task(self.task_id)
        manual_time = "" if task.manual_time_minutes is None else f"{task.manual_time_minutes:g}"
        with VerticalScroll(id="editor"):
            yield Static(
                f"Edit {task.id} (v{task.version}, {task.visibility}). "
                "ctrl+s saves, escape cancels.",
                markup=False,
            )
            yield Label("Trap")
            yield Input(task.trap, id="edit-trap")
            yield Label("Manual Time (minutes, blank if not timed yet)")
            yield Input(manual_time, id="edit-manual-time")
            yield Label("Canonical Prompt")
            yield TextArea(task.canonical_prompt, id="edit-prompt")
            yield Label("Input")
            yield TextArea(task.input_path.read_text(), id="edit-input")
            yield Label("Gold Answer")
            yield TextArea(task.gold_answer_path.read_text(), id="edit-gold")
            yield Label("Criteria (YAML list of id, name, description)")
            yield TextArea(criteria_yaml(self._task_criteria(task)), id="edit-criteria")
            yield Static("", id="editor-error", markup=False)
            yield Button("Save", id="editor-save", variant="primary")

    def _task_criteria(self, task: Task) -> list[dict[str, str]]:
        own = task.rubric.criteria[len(self.workspace.rubric.criteria):]
        return [{"id": c.id, "name": c.name, "description": c.description} for c in own]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "editor-save":
            self.action_save()

    def action_save(self) -> None:
        try:
            changes = {
                "trap": self.query_one("#edit-trap", Input).value,
                "manual_time_minutes": parse_minutes(
                    self.query_one("#edit-manual-time", Input).value
                ),
                "canonical_prompt": self.query_one("#edit-prompt", TextArea).text,
                "input": self.query_one("#edit-input", TextArea).text,
                "gold_answer": self.query_one("#edit-gold", TextArea).text,
                "criteria": parse_criteria(self.query_one("#edit-criteria", TextArea).text),
            }
            task = self.workspace.update_task(self.task_id, **changes)
        except (FormError, ConfigError, TaskEditRefused) as error:
            self.query_one("#editor-error", Static).update(str(error))
            return
        self.dismiss(task)

    def action_cancel(self) -> None:
        self.dismiss(None)


def criteria_yaml(criteria: list[dict[str, str]]) -> str:
    return yaml.safe_dump(criteria, sort_keys=False, allow_unicode=True)


def parse_minutes(text: str) -> int | float | None:
    """Blank is not timed yet; anything else must be a number (the Workspace checks the rest)."""
    text = text.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        raise FormError(f"Manual Time: must be a number of minutes, got {text!r}") from None
    return int(number) if number.is_integer() else number


def parse_criteria(text: str) -> Any:
    """The Criteria as written; the Workspace validates their shape."""
    try:
        criteria = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise FormError(f"Criteria: not valid YAML: {error}") from None
    if not isinstance(criteria, list) or not all(isinstance(c, dict) for c in criteria):
        raise FormError("Criteria: must be a YAML list of id, name, description entries")
    return criteria
