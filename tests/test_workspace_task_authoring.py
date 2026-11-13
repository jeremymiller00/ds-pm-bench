from pathlib import Path

import pytest

from bench import ConfigError, TaskNotRunnable, Workspace

CRITERIA = [
    {
        "id": "names_the_decision",
        "name": "Names the decision",
        "description": "States which decision the model output will inform.",
    }
]


def new_task(ws: Workspace, task_id: str = "T1.1", **overrides):
    args = dict(
        track="Framing",
        trap="Invents a success metric the input never states.",
        canonical_prompt="Frame this business problem as a data problem.",
        criteria=CRITERIA,
        manual_time_minutes=45,
    )
    args.update(overrides)
    return ws.create_task(task_id, **args)


def test_a_new_task_is_scaffolded_in_the_public_tasks_folder(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    task = new_task(ws)

    assert task.folder == workspace_dir / "tasks" / "T1.1"
    assert (task.id, task.track, task.version, task.visibility) == (
        "T1.1",
        "Framing",
        1,
        "public",
    )
    assert task.trap == "Invents a success metric the input never states."
    assert task.manual_time_minutes == 45
    assert task.canonical_prompt == "Frame this business problem as a data problem."
    assert task.rubric.criteria[-1].id == "names_the_decision"
    assert task.placeholders == ("input", "Gold Answer")
    assert ws.task("T1.1") == task
    assert Workspace.open(workspace_dir).task("T1.1") == task
    with pytest.raises(TaskNotRunnable):
        ws.runnable_task("T1.1")


@pytest.fixture
def private_root(workspace_dir: Path, tmp_path: Path) -> Path:
    private = tmp_path / "bench-private"
    private.mkdir()
    (workspace_dir / "config" / "local.yaml").write_text(f"private_roots: ['{private}']\n")
    return private


def test_a_new_task_in_a_configured_private_root_is_private(
    workspace_dir: Path, private_root: Path
):
    ws = Workspace.open(workspace_dir)

    task = new_task(ws, "T2.1", track="Specs", private_root=private_root)

    assert task.folder == private_root / "tasks" / "T2.1"
    assert task.visibility == "private"
    assert not (workspace_dir / "tasks").exists()
    assert Workspace.open(workspace_dir).task("T2.1").visibility == "private"


def test_a_new_task_in_an_unconfigured_root_is_refused(
    workspace_dir: Path, tmp_path: Path
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with pytest.raises(ValueError, match="not a configured private root"):
        new_task(Workspace.open(workspace_dir), private_root=elsewhere)

    assert not (elsewhere / "tasks").exists()


def test_a_new_task_without_manual_time_has_it_as_a_placeholder(workspace_dir: Path):
    task = new_task(Workspace.open(workspace_dir), manual_time_minutes=None)

    assert task.placeholders == ("input", "Gold Answer", "Manual Time")


@pytest.mark.parametrize(
    "task_id, overrides, file, field",
    [
        ("T1.1", dict(track="Roadmaps"), "task.md", "track"),
        ("T1.1", dict(trap=" "), "task.md", "trap"),
        ("T1.1", dict(manual_time_minutes=0), "task.md", "manual_time_minutes"),
        ("T1.1", dict(canonical_prompt=""), "task.md", "(canonical prompt)"),
        ("T1.1", dict(criteria=[]), "rubric.yaml", "criteria"),
        (
            "T1.1",
            dict(criteria=[{"id": "clarity", "name": "Clarity", "description": "Again."}]),
            "rubric.yaml",
            "criteria[0].id",
        ),
        ("../T1.1", {}, "task.md", "id"),
    ],
    ids=[
        "unknown-track",
        "blank-trap",
        "zero-manual-time",
        "empty-prompt",
        "no-criteria",
        "shadows-shared",
        "bad-id",
    ],
)
def test_an_invalid_new_task_fails_naming_the_field_and_writes_nothing(
    workspace_dir: Path, task_id: str, overrides: dict, file: str, field: str
):
    ws = Workspace.open(workspace_dir)

    with pytest.raises(ConfigError) as err:
        new_task(ws, task_id, **overrides)

    assert err.value.file.endswith(file) and err.value.field == field
    assert list((workspace_dir / "tasks").glob("*")) == []
    assert ws.tasks == ()


def test_a_new_task_cannot_reuse_an_id_from_any_root(
    workspace_dir: Path, private_root: Path
):
    ws = Workspace.open(workspace_dir)
    new_task(ws, "T2.1", track="Specs", private_root=private_root)

    with pytest.raises(ValueError, match="T2.1 already exists"):
        new_task(ws, "T2.1", track="Specs")
