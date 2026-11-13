"""Textual pilot tests: the TUI opened on fixture Workspaces in temp dirs."""

from pathlib import Path

import pytest
from textual.widgets import Static, TabbedContent, Tabs, Tree

from bench import Workspace
from bench.cli import main
from bench.tui.app import BenchApp


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
    settings = workspace_dir / "config" / "settings.yaml"
    settings.write_text(
        settings.read_text().replace("private_roots: []", f"private_roots: ['{private}']")
    )


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
