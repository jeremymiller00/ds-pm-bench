"""The Task browser: Tasks grouped by Track, with one Task's details read-only."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static, Tree

from bench.core.tasks import Task
from bench.core.workspace import Workspace


class TaskBrowser(Horizontal):
    DEFAULT_CSS = """
    TaskBrowser #task-tree { width: 1fr; }
    TaskBrowser #task-details-pane { width: 2fr; padding: 0 1; }
    """

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        tree: Tree[str] = Tree("Tasks", id="task-tree")
        tree.show_root = False
        for track in self.workspace.settings.tracks:
            tasks = [t for t in self.workspace.tasks if t.track == track]
            node = tree.root.add(f"{track} ({len(tasks)})", expand=True)
            for task in tasks:
                node.add_leaf(task_label(task), data=task.id)
        yield tree
        with VerticalScroll(id="task-details-pane"):
            yield Static("Select a Task to see its details.", id="task-details", markup=False)

    def on_mount(self) -> None:
        tree = self.query_one("#task-tree", Tree)
        tree.cursor_line = 0
        tree.focus()

    @on(Tree.NodeHighlighted, "#task-tree")
    def _show_task(self, event: Tree.NodeHighlighted[str]) -> None:
        task_id = event.node.data
        if task_id is not None:
            details = task_details(self.workspace.task(task_id))
            self.query_one("#task-details", Static).update(details)


def task_label(task: Task) -> str:
    """One line: id, visibility, version and whether the Task is still a placeholder."""
    return f"{task.id}  {task.visibility}  v{task.version}  {placeholder_status(task)}"


def placeholder_status(task: Task) -> str:
    if task.placeholders:
        return f"placeholder: {', '.join(task.placeholders)}"
    return "ready"


def task_details(task: Task) -> str:
    manual_time = (
        "not timed yet"
        if task.manual_time_minutes is None
        else f"{task.manual_time_minutes:g} minutes"
    )
    criteria = "\n".join(f"  - {c.id}: {c.name}" for c in task.rubric.criteria)
    return (
        f"{task.id}\n\n"
        f"Track: {task.track}\n"
        f"Visibility: {task.visibility}\n"
        f"Version: {task.version}\n"
        f"Manual Time: {manual_time}\n"
        f"Placeholders: {', '.join(task.placeholders) or 'none'}\n"
        f"Folder: {task.folder}\n\n"
        f"Trap:\n{task.trap}\n\n"
        f"Canonical Prompt:\n{task.canonical_prompt}\n\n"
        f"Rubric:\n{criteria}\n"
    )
