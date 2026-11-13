"""Textual pilot tests: the TUI opened on fixture Workspaces in temp dirs."""

from pathlib import Path

from textual.widgets import Tree

from bench import Workspace
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
