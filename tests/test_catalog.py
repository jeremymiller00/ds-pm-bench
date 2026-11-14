"""`bench scaffold-catalog`: the 12 catalog Tasks as Private Tasks with placeholders."""

from pathlib import Path

import pytest

from bench import Workspace
from bench.cli import main
from test_workspace_tasks import configure_private_root, write_task

CATALOG_IDS = [f"T{track}.{n}" for track in range(1, 5) for n in range(1, 4)]
TRACKS = {"T1": "Framing", "T2": "Specs", "T3": "Prioritize", "T4": "Story"}


@pytest.fixture
def private_root(workspace_dir: Path, tmp_path: Path) -> Path:
    private = tmp_path / "bench-private"
    private.mkdir()
    configure_private_root(workspace_dir, private)
    return private


def scaffold(workspace_dir: Path, private_root: Path) -> int:
    return main(
        ["--workspace", str(workspace_dir), "scaffold-catalog", "--root", str(private_root)]
    )


def test_scaffolds_the_12_catalog_tasks_as_private_tasks_with_placeholders(
    workspace_dir: Path, private_root: Path, capsys: pytest.CaptureFixture[str]
):
    assert scaffold(workspace_dir, private_root) == 0

    tasks = Workspace.open(workspace_dir).tasks
    assert [t.id for t in tasks] == CATALOG_IDS
    for task in tasks:
        assert task.visibility == "private"
        assert task.folder == private_root / "tasks" / task.id
        assert task.track == TRACKS[task.id.split(".")[0]]
        assert task.version == 1
        assert task.placeholders == ("input", "Gold Answer", "Manual Time", "Canonical Prompt")
        assert task.trap
        assert len(task.rubric.criteria) in (6, 7)  # shared five plus one or two
    assert not (workspace_dir / "tasks").exists()
    assert "created 12 Private Tasks" in capsys.readouterr().out


def test_catalog_tasks_carry_their_trap_and_task_specific_criteria(
    workspace_dir: Path, private_root: Path
):
    scaffold(workspace_dir, private_root)
    ws = Workspace.open(workspace_dir)

    framing = ws.task("T1.1")
    assert "metric" in framing.trap
    ranking = ws.task("T3.1")
    assert "owner" in ranking.trap
    assert any(
        "likelihood of success" in c.name.lower() for c in ranking.rubric.criteria[5:]
    )
    pr_faq = ws.task("T4.1")
    assert "fabricate" in pr_faq.trap


def test_refuses_to_overwrite_existing_tasks_and_writes_nothing(
    workspace_dir: Path, private_root: Path, capsys: pytest.CaptureFixture[str]
):
    existing = write_task(private_root, "T2.2", track="Specs", trap="Mine.")

    assert scaffold(workspace_dir, private_root) == 1

    assert "T2.2" in capsys.readouterr().err
    assert sorted(p.name for p in (private_root / "tasks").iterdir()) == ["T2.2"]
    assert Workspace.open(workspace_dir).task("T2.2").trap == "Mine."
    assert existing.is_dir()


def test_running_it_twice_refuses_the_second_time(workspace_dir: Path, private_root: Path):
    assert scaffold(workspace_dir, private_root) == 0

    assert scaffold(workspace_dir, private_root) == 1
    assert len(Workspace.open(workspace_dir).tasks) == 12


def test_refuses_a_root_that_is_not_configured(
    workspace_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert scaffold(workspace_dir, elsewhere) == 1

    assert "not a configured private root" in capsys.readouterr().err
    assert not (elsewhere / "tasks").exists()


def test_the_catalog_never_scaffolds_public_tasks(workspace_dir: Path):
    with pytest.raises(SystemExit):  # --root is required
        main(["--workspace", str(workspace_dir), "scaffold-catalog"])
    assert not (workspace_dir / "tasks").exists()
