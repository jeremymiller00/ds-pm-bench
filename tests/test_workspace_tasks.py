from pathlib import Path

import pytest

from bench import ConfigError, Workspace


def write_task(
    root: Path,
    task_id: str = "T1.1",
    *,
    folder: str = "tasks",
    track: str = "Framing",
    trap: str = "Invents a success metric the input never states.",
    version: int | str = 1,
    manual_time: str = "45",
    prompt: str = "Frame this business problem as a data problem.",
    input_text: str = "Churn rose 4% last quarter in the SMB segment.",
    input_name: str = "input.md",
    gold: str = "Problem: predict SMB churn 60 days ahead.",
    criteria: str = (
        "criteria:\n"
        "  - id: names_the_decision\n"
        "    name: Names the decision\n"
        "    description: States which decision the model output will inform.\n"
    ),
    extra_frontmatter: str = "",
) -> Path:
    path = root / folder / task_id
    path.mkdir(parents=True)
    (path / "task.md").write_text(
        "---\n"
        f"id: {task_id}\n"
        f"track: {track}\n"
        f"trap: {trap}\n"
        f"version: {version}\n"
        f"manual_time_minutes: {manual_time}\n"
        f"{extra_frontmatter}"
        "---\n"
        f"{prompt}\n"
    )
    (path / input_name).write_text(input_text)
    (path / "gold.md").write_text(gold)
    (path / "rubric.yaml").write_text(criteria)
    return path


def test_public_task_loads_from_the_repo_tasks_folder(workspace_dir: Path):
    folder = write_task(workspace_dir)

    task = Workspace.open(workspace_dir).task("T1.1")

    assert task.id == "T1.1"
    assert task.track == "Framing"
    assert task.trap == "Invents a success metric the input never states."
    assert task.version == 1
    assert task.manual_time_minutes == 45
    assert task.canonical_prompt == "Frame this business problem as a data problem."
    assert task.visibility == "public"
    assert task.input_path == folder / "input.md"
    assert task.gold_answer_path == folder / "gold.md"
    assert [c.id for c in task.rubric.criteria] == [
        "correctness",
        "completeness",
        "clarity",
        "pm_judgment",
        "format_fit",
        "names_the_decision",
    ]
