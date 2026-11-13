from pathlib import Path

import pytest

from bench import ConfigError, TaskEditRefused, TaskNotRunnable, Workspace
from test_workspace_candidates import write_candidate
from test_workspace_runs import cleared

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


def test_an_edit_before_any_run_is_made_in_place(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    new_task(ws)

    task = ws.update_task(
        "T1.1",
        trap="Recommends a model before naming the decision.",
        input="Churn rose 4% last quarter in the SMB segment.",
        gold_answer="Problem: predict SMB churn 60 days ahead.",
    )

    assert task.version == 1
    assert task.trap == "Recommends a model before naming the decision."
    assert task.placeholders == ()
    reopened = Workspace.open(workspace_dir)
    assert reopened.task("T1.1") == task
    assert reopened.runnable_task("T1.1").input_path.read_text() == (
        "Churn rose 4% last quarter in the SMB segment."
    )


def test_every_field_can_be_edited(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    new_task(ws)
    criteria = [
        {"id": "sizes_the_output", "name": "Sizes the output", "description": "Fits capacity."},
        {"id": "flags_gaps", "name": "Flags gaps", "description": "Names missing data."},
    ]

    task = ws.update_task(
        "T1.1",
        track="Specs",
        canonical_prompt="Write a one-page spec.",
        criteria=criteria,
        manual_time_minutes=30.5,
    )

    assert (task.track, task.canonical_prompt, task.manual_time_minutes) == (
        "Specs",
        "Write a one-page spec.",
        30.5,
    )
    assert [c.id for c in task.rubric.criteria][-2:] == ["sizes_the_output", "flags_gaps"]


def run_over(ws: Workspace, task_id: str) -> None:
    """Start a Run that uses the Task's current version."""
    write_candidate(ws.root, "claude@opus-5-5", cleared("public_only", baseline=True))
    ws = Workspace.open(ws.root)
    run = ws.start_run("claude@opus-5-5")
    assert task_id in run.task_versions


def complete_task(ws: Workspace, task_id: str = "T1.1") -> None:
    new_task(ws, task_id)
    ws.update_task(task_id, input="Churn rose 4%.", gold_answer="Predict SMB churn.")


def test_an_edit_after_a_run_used_the_version_makes_a_new_version(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    complete_task(ws)
    run_over(ws, "T1.1")

    task = ws.update_task("T1.1", trap="Recommends a model before naming the decision.")

    assert task.version == 2
    assert Workspace.open(workspace_dir).task("T1.1").version == 2
    # Only one bump per used version: the new version has no Run yet.
    assert ws.update_task("T1.1", manual_time_minutes=50).version == 2


def test_an_edit_that_changes_nothing_keeps_the_version(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    complete_task(ws)
    run_over(ws, "T1.1")

    assert ws.update_task("T1.1", manual_time_minutes=45).version == 1


def test_visibility_cannot_be_changed_by_an_edit(workspace_dir: Path, private_root: Path):
    ws = Workspace.open(workspace_dir)
    new_task(ws, "T2.1", track="Specs", private_root=private_root)

    with pytest.raises(TaskEditRefused, match="visibility"):
        ws.update_task("T2.1", visibility="public")

    assert ws.task("T2.1").visibility == "private"


@pytest.mark.parametrize("field", ["id", "version", "owner"])
def test_fields_outside_the_editable_ones_are_refused(workspace_dir: Path, field: str):
    ws = Workspace.open(workspace_dir)
    new_task(ws)

    with pytest.raises(TaskEditRefused, match=field):
        ws.update_task("T1.1", **{field: "x"})


def test_an_invalid_edit_fails_naming_the_field_and_leaves_the_task_as_it_was(
    workspace_dir: Path,
):
    ws = Workspace.open(workspace_dir)
    before = new_task(ws)

    with pytest.raises(ConfigError) as err:
        ws.update_task("T1.1", track="Roadmaps", trap="A new trap.")

    assert (err.value.file, err.value.field) == ("tasks/T1.1/task.md", "track")
    assert Workspace.open(workspace_dir).task("T1.1") == before
