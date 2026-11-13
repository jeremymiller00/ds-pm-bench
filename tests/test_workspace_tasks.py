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


def configure_private_root(workspace_dir: Path, private: Path) -> None:
    settings = workspace_dir / "config" / "settings.yaml"
    settings.write_text(
        settings.read_text().replace("private_roots: []", f"private_roots: ['{private}']")
    )


def test_tasks_from_a_private_root_are_private_and_public_ones_public(
    workspace_dir: Path, tmp_path: Path
):
    private = tmp_path / "bench-private"
    write_task(workspace_dir, "T1.1-example")
    folder = write_task(private, "T2.1", track="Specs")
    configure_private_root(workspace_dir, private)

    ws = Workspace.open(workspace_dir)

    assert [(t.id, t.visibility) for t in ws.tasks] == [
        ("T1.1-example", "public"),
        ("T2.1", "private"),
    ]
    assert ws.task("T2.1").input_path == folder / "input.md"


def test_asking_for_a_task_without_private_roots_says_none_are_configured(
    workspace_dir: Path,
):
    with pytest.raises(LookupError, match="no private roots are configured"):
        Workspace.open(workspace_dir).task("T2.1")


def test_a_configured_private_root_that_is_missing_fails_to_open(
    workspace_dir: Path, tmp_path: Path
):
    configure_private_root(workspace_dir, tmp_path / "not-cloned-yet")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.field == "private_roots[0]"
    assert "not-cloned-yet" in err.value.message


def test_the_same_task_id_in_two_roots_is_rejected(workspace_dir: Path, tmp_path: Path):
    private = tmp_path / "bench-private"
    write_task(workspace_dir, "T1.1")
    write_task(private, "T1.1")
    configure_private_root(workspace_dir, private)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.field == "id"
    assert "T1.1" in err.value.message
