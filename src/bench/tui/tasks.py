"""The Task browser: Tasks grouped by Track, one Task's details, and keys to edit or add Tasks."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, Tree

from bench.core.tasks import Task
from bench.core.workspace import Workspace
from bench.tui.task_editor import NewTaskScreen, TaskEditorScreen


class TaskBrowser(Vertical):
    DEFAULT_CSS = """
    TaskBrowser #task-tree { width: 1fr; }
    TaskBrowser #task-details-pane { width: 2fr; padding: 0 1; }
    TaskBrowser #task-status { height: auto; padding: 0 1; }
    """
    BINDINGS = [
        Binding("e", "edit_task", "Edit Task"),
        Binding("n", "new_task", "New Task"),
    ]

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        tree: Tree[str] = Tree("Tasks", id="task-tree")
        tree.show_root = False
        self._fill_tree(tree)
        with Horizontal():
            yield tree
            with VerticalScroll(id="task-details-pane"):
                yield Static(
                    "Select a Task to see its details.", id="task-details", markup=False
                )
        yield Static("", id="task-status", markup=False)

    def _fill_tree(self, tree: Tree[str]) -> None:
        tree.clear()
        for track in self.workspace.settings.tracks:
            tasks = [t for t in self.workspace.tasks if t.track == track]
            node = tree.root.add(f"{track} ({len(tasks)})", expand=True)
            for task in tasks:
                node.add_leaf(task_label(task), data=task.id)

    def refresh_tasks(self, select: str | None = None) -> None:
        """Rebuild the tree from the Workspace, with the cursor on the Task select names."""
        tree = self.query_one("#task-tree", Tree)
        self._fill_tree(tree)
        for track in tree.root.children:
            for node in track.children:
                if node.data == select:
                    tree.move_cursor(node)

    def _highlighted_task(self) -> str | None:
        node = self.query_one("#task-tree", Tree).cursor_node
        return None if node is None else node.data

    def action_edit_task(self) -> None:
        task_id = self._highlighted_task()
        if task_id is None:
            self._status("Highlight a Task to edit it.")
            return
        before = self.workspace.task(task_id).version

        def saved(task: Task | None) -> None:
            if task is None:
                return
            self.refresh_tasks(select=task.id)
            if task.version > before:
                self._status(
                    f"{task.id} saved as v{task.version} "
                    f"(a new version: a Run used v{before})."
                )
            else:
                self._status(f"{task.id} saved as v{task.version} (edited in place).")

        self.app.push_screen(TaskEditorScreen(self.workspace, task_id), saved)

    def action_new_task(self) -> None:
        def created(task: Task | None) -> None:
            if task is not None:
                self.refresh_tasks(select=task.id)
                self._status(
                    f"{task.id} created as v{task.version} {task.visibility} in {task.folder}."
                )

        self.app.push_screen(NewTaskScreen(self.workspace), created)

    def _status(self, message: str) -> None:
        self.query_one("#task-status", Static).update(message)

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
    catalog = "".join(
        f"{label}: {value}\n"
        for label, value in (("Name", task.name), ("Output shape", task.output_shape))
        if value is not None
    )
    return (
        f"{task.id}\n\n"
        f"{catalog}"
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
