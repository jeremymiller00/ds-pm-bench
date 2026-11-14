import shutil
from pathlib import Path

import pytest
import yaml

from bench import ConfigError, TaskNotRunnable, Workspace

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    (workspace_dir / "config" / "local.yaml").write_text(f"private_roots: ['{private}']\n")


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


@pytest.mark.parametrize(
    "task_args, file, field",
    [
        (dict(track="Roadmaps"), "task.md", "track"),
        (dict(version="0"), "task.md", "version"),
        (dict(version="'2'"), "task.md", "version"),
        (dict(manual_time="-5"), "task.md", "manual_time_minutes"),
        (dict(manual_time="0"), "task.md", "manual_time_minutes"),
        (dict(trap="''"), "task.md", "trap"),
        (dict(prompt=""), "task.md", "(canonical prompt)"),
        (dict(extra_frontmatter="owner: me\n"), "task.md", "owner"),
        (dict(input_name="notes.md"), "input.*", "(file)"),
        (dict(criteria="criteria: []\n"), "rubric.yaml", "criteria"),
        (
            dict(
                criteria="criteria:\n"
                "  - id: clarity\n    name: Clarity\n    description: Again.\n"
            ),
            "rubric.yaml",
            "criteria[0].id",
        ),
    ],
    ids=[
        "unknown-track",
        "version-below-1",
        "version-not-integer",
        "negative-manual-time",
        "zero-manual-time",
        "blank-trap",
        "empty-prompt",
        "unknown-field",
        "no-input-file",
        "no-task-criteria",
        "criterion-shadows-shared",
    ],
)
def test_invalid_task_fails_naming_file_and_field(
    workspace_dir: Path, task_args: dict, file: str, field: str
):
    write_task(workspace_dir, "T1.2", **task_args)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == (f"tasks/T1.2/{file}", field)


def test_task_id_must_follow_the_track_numbering(workspace_dir: Path):
    write_task(workspace_dir, "Framing-1")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("tasks/Framing-1/task.md", "id")


def test_a_tasks_track_is_any_track_defined_in_settings(workspace_dir: Path):
    settings = workspace_dir / "config" / "settings.yaml"
    data = yaml.safe_load(settings.read_text())
    data["tracks"] = [*data["tracks"], "Roadmaps"]
    settings.write_text(yaml.safe_dump(data))
    write_task(workspace_dir, "T5.1", track="Roadmaps")
    write_task(workspace_dir, "T1.2", track="Specs")  # the id's number implies nothing

    ws = Workspace.open(workspace_dir)

    assert (ws.task("T5.1").track, ws.task("T1.2").track) == ("Roadmaps", "Specs")


def test_a_track_not_defined_in_settings_fails_naming_the_settings(workspace_dir: Path):
    write_task(workspace_dir, "T5.1", track="Roadmaps")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("tasks/T5.1/task.md", "track")
    assert "config/settings.yaml" in err.value.message


def test_task_without_frontmatter_fails_naming_the_file(workspace_dir: Path):
    folder = write_task(workspace_dir, "T1.2")
    (folder / "task.md").write_text("Just a prompt, no frontmatter.\n")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("tasks/T1.2/task.md", "(frontmatter)")


def test_complete_task_is_runnable(workspace_dir: Path):
    write_task(workspace_dir, "T1.1")

    assert Workspace.open(workspace_dir).runnable_task("T1.1").id == "T1.1"


@pytest.mark.parametrize(
    "task_args, missing",
    [
        (dict(input_text="TODO"), "input"),
        (dict(gold="TODO: PM to write after sanitizing.\n"), "Gold Answer"),
        (dict(gold="   \n"), "Gold Answer"),
        (dict(manual_time="null"), "Manual Time"),
        (dict(prompt="TODO: draft the Canonical Prompt."), "Canonical Prompt"),
    ],
    ids=["todo-input", "todo-gold-with-note", "blank-gold", "untimed", "todo-prompt"],
)
def test_task_with_a_placeholder_loads_but_is_refused_for_running(
    workspace_dir: Path, tmp_path: Path, task_args: dict, missing: str
):
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize", **task_args)
    configure_private_root(workspace_dir, private)
    ws = Workspace.open(workspace_dir)

    assert ws.task("T3.1").placeholders == (missing,)
    with pytest.raises(TaskNotRunnable, match=f"T3.1.*{missing}"):
        ws.runnable_task("T3.1")


def test_the_committed_example_task_is_public_complete_and_runnable(workspace_dir: Path):
    shutil.copytree(REPO_ROOT / "examples", workspace_dir / "examples")

    ws = Workspace.open(workspace_dir)
    task = ws.runnable_task("T1.1-example")

    assert task.visibility == "public"
    assert task.placeholders == ()
    assert len(task.rubric.criteria) in (6, 7)  # shared five plus one or two
