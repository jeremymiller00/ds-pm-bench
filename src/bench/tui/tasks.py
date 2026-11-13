"""The Task browser: Tasks grouped by Track, with one Task's details read-only."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Tree

from bench.core.tasks import Task
from bench.core.workspace import Workspace


class TaskBrowser(Horizontal):
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


def task_label(task: Task) -> str:
    """One line: id, visibility, version and whether the Task is still a placeholder."""
    return f"{task.id}  {task.visibility}  v{task.version}  {placeholder_status(task)}"


def placeholder_status(task: Task) -> str:
    if task.placeholders:
        return f"placeholder: {', '.join(task.placeholders)}"
    return "ready"
