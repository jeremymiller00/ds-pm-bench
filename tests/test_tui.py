"""Textual pilot tests: the TUI opened on fixture Workspaces in temp dirs."""

from pathlib import Path

import pytest
from textual.widgets import Static, TabbedContent, Tabs, TextArea, Tree

from bench import Workspace
from bench.cli import main
from bench.tui.app import BenchApp
from bench.tui.task_editor import TaskEditorScreen
from test_workspace_candidates import write_candidate
from test_workspace_runs import cleared


def write_task(
    root: Path,
    task_id: str,
    *,
    track: str = "Framing",
    folder: str = "tasks",
    version: int = 1,
    manual_time: str = "45",
    input_text: str = "Churn rose 4% last quarter in the SMB segment.",
    gold: str = "Problem: predict SMB churn 60 days ahead.",
) -> Path:
    path = root / folder / task_id
    path.mkdir(parents=True)
    (path / "task.md").write_text(
        "---\n"
        f"id: {task_id}\n"
        f"track: {track}\n"
        "trap: Invents a success metric the input never states.\n"
        f"version: {version}\n"
        f"manual_time_minutes: {manual_time}\n"
        "---\n"
        "Frame this business problem as a data problem.\n"
    )
    (path / "input.md").write_text(input_text)
    (path / "gold.md").write_text(gold)
    (path / "rubric.yaml").write_text(
        "criteria:\n"
        "  - id: names_the_decision\n"
        "    name: Names the decision\n"
        "    description: States which decision the model output will inform.\n"
    )
    return path


def tree_outline(app: BenchApp) -> dict[str, list[str]]:
    """Each Track node's label, mapped to the labels of its Task nodes."""
    tree = app.query_one("#task-tree", Tree)
    return {
        str(track.label): [str(task.label) for task in track.children]
        for track in tree.root.children
    }


async def test_task_browser_groups_tasks_by_track_in_catalog_order(workspace_dir: Path):
    write_task(workspace_dir, "T2.1", track="Specs")
    write_task(workspace_dir, "T1.1", track="Framing")
    write_task(workspace_dir, "T1.2", track="Framing")

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test():
        outline = tree_outline(app)

    assert list(outline) == ["Framing (2)", "Specs (1)", "Prioritize (0)", "Story (0)"]
    assert [label.split()[0] for label in outline["Framing (2)"]] == ["T1.1", "T1.2"]
    assert [label.split()[0] for label in outline["Specs (1)"]] == ["T2.1"]


def configure_private_root(workspace_dir: Path, private: Path) -> None:
    (workspace_dir / "config" / "local.yaml").write_text(f"private_roots: ['{private}']\n")


async def test_task_labels_mark_visibility_version_and_placeholders(
    workspace_dir: Path, tmp_path: Path
):
    private = tmp_path / "private"
    configure_private_root(workspace_dir, private)
    write_task(workspace_dir, "T1.1", version=3)
    write_task(private, "T1.2", gold="TODO: write the Gold Answer", manual_time="null")

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test():
        public, private_task = tree_outline(app)["Framing (2)"]

    assert public == "T1.1  public  v3  ready"
    assert private_task == "T1.2  private  v1  placeholder: Gold Answer, Manual Time"


async def test_highlighting_a_task_shows_its_details_read_only(workspace_dir: Path):
    write_task(workspace_dir, "T1.1", input_text="TODO")

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        details = app.query_one("#task-details", Static)
        assert "Select a Task" in str(details.content)
        await pilot.press("down")  # from the Framing Track to its first Task
        await pilot.pause()
        text = str(details.content)

    assert "T1.1" in text
    assert "Track: Framing" in text
    assert "Visibility: public" in text
    assert "Version: 1" in text
    assert "Manual Time: 45 minutes" in text
    assert "Placeholders: input" in text
    assert "Invents a success metric the input never states." in text
    assert "Frame this business problem as a data problem." in text
    assert "names_the_decision" in text
    assert not app.query("Input, TextArea")  # nothing to edit here


async def test_shell_opens_on_tasks_with_the_later_sections_as_placeholders(
    workspace_dir: Path,
):
    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        tabs = app.query_one(Tabs)
        labels = [str(tab.label) for tab in tabs.query("Tab")]
        assert labels == [
            "Tasks", "Runs", "Calibration", "Comparisons", "Decision Log", "Settings"
        ]
        sections = app.query_one(TabbedContent)
        assert sections.active == "tasks"

        await pilot.click("#--content-tab-runs")
        await pilot.pause()
        assert sections.active == "runs"
        placeholder = sections.query_one("#runs Static", Static)
        assert "not built yet" in str(placeholder.content)


@pytest.mark.parametrize("args", [[], ["tui"]])
def test_bench_with_no_command_or_tui_opens_the_app_on_the_workspace(
    workspace_dir: Path, monkeypatch, args: list[str]
):
    write_task(workspace_dir, "T1.1")
    opened: list[Workspace] = []
    # The terminal is the boundary: record which Workspace the app would run on.
    monkeypatch.setattr(BenchApp, "run", lambda app: opened.append(app.workspace))

    assert main(["--workspace", str(workspace_dir), *args]) == 0

    assert [ws.root for ws in opened] == [workspace_dir.resolve()]
    assert [t.id for t in opened[0].tasks] == ["T1.1"]


def run_over(workspace_dir: Path) -> None:
    """A fixture Run that uses every public Task's current version."""
    write_candidate(workspace_dir, "claude@opus-5-5", cleared("public_only", baseline=True))
    Workspace.open(workspace_dir).start_run("claude@opus-5-5")


async def open_editor(app: BenchApp, pilot, task_id: str = "T1.1") -> None:
    tree = app.query_one("#task-tree", Tree)
    node = next(n for track in tree.root.children for n in track.children if n.data == task_id)
    tree.move_cursor(node)
    await pilot.press("e")
    await pilot.pause()


def fill(app: BenchApp, selector: str, value: str) -> None:
    widget = app.screen.query_one(selector)
    if isinstance(widget, TextArea):
        widget.text = value
    else:
        widget.value = value


async def test_editing_a_task_a_run_used_saves_a_new_version_and_says_so(
    workspace_dir: Path,
):
    write_task(workspace_dir, "T1.1")
    run_over(workspace_dir)

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_editor(app, pilot)
        assert isinstance(app.screen, TaskEditorScreen)
        fill(app, "#edit-trap", "Recommends a model before naming the decision.")
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert not isinstance(app.screen, TaskEditorScreen)
        assert tree_outline(app)["Framing (1)"] == ["T1.1  public  v2  ready"]
        status = str(app.query_one("#task-status", Static).content)

    assert "T1.1 saved as v2" in status
    assert "new version" in status
    task = Workspace.open(workspace_dir).task("T1.1")
    assert (task.version, task.trap) == (2, "Recommends a model before naming the decision.")


async def test_editing_a_task_no_run_used_keeps_its_version(workspace_dir: Path):
    write_task(workspace_dir, "T1.1", gold="TODO")

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_editor(app, pilot)
        fill(app, "#edit-gold", "Problem: predict SMB churn 60 days ahead.")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert tree_outline(app)["Framing (1)"] == ["T1.1  public  v1  ready"]
        status = str(app.query_one("#task-status", Static).content)

    assert "T1.1 saved as v1 (edited in place)" in status


@pytest.mark.parametrize(
    ("selector", "value", "message"),
    [
        ("#edit-manual-time", "-5", "manual_time_minutes"),
        ("#edit-manual-time", "soon", "Manual Time"),
        ("#edit-criteria", "- id: Not Snake\n  name: X\n  description: Y\n", "criteria[0].id"),
        ("#edit-criteria", "[unclosed", "not valid YAML"),
    ],
)
async def test_an_invalid_edit_is_shown_inline_and_changes_nothing(
    workspace_dir: Path, selector: str, value: str, message: str
):
    folder = write_task(workspace_dir, "T1.1")
    before = (folder / "task.md").read_text()

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_editor(app, pilot)
        fill(app, selector, value)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, TaskEditorScreen)
        error = str(app.screen.query_one("#editor-error", Static).content)

    assert message in error
    assert (folder / "task.md").read_text() == before


async def test_cancelling_an_edit_leaves_the_task_as_it_was(workspace_dir: Path):
    folder = write_task(workspace_dir, "T1.1")
    before = (folder / "task.md").read_text()

    app = BenchApp(Workspace.open(workspace_dir))
    async with app.run_test() as pilot:
        await open_editor(app, pilot)
        fill(app, "#edit-trap", "Something else.")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TaskEditorScreen)

    assert (folder / "task.md").read_text() == before
