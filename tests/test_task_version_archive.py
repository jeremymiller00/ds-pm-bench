"""ADR-0003: a bumped Task's used version is archived, so past Runs still load."""

from pathlib import Path

import pytest

from bench import RunMismatch, Workspace
from bench.cli import main
from test_workspace_candidates import write_candidate
from test_workspace_runs import cleared
from test_workspace_tasks import configure_private_root, write_task
from test_workspace_verdicts import BASELINE, CHALLENGER, complete_run


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """Two public Tasks, a Baseline and a challenger, both public only."""
    write_task(workspace_dir, "T1.1", track="Framing")
    write_task(workspace_dir, "T2.1", track="Specs")
    write_candidate(workspace_dir, BASELINE, cleared("public_only", baseline=True))
    write_candidate(workspace_dir, CHALLENGER, cleared("public_only", version="other-1"))
    return workspace_dir


def test_a_run_that_used_an_older_version_still_compares_after_a_bump(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    baseline = complete_run(ws, BASELINE)
    challenger = complete_run(ws, CHALLENGER)
    before = ws.compare(challenger, baseline)

    bumped = ws.update_task("T1.1", trap="A sharper trap.", canonical_prompt="A new prompt.")

    assert bumped.version == 2
    after = Workspace.open(ws_dir).compare(challenger, baseline)
    assert after.verdict == before.verdict
    run = Workspace.open(ws_dir).resume_run(baseline)
    assert run.task("T1.1").version == 1
    assert run.task("T1.1").canonical_prompt == "Frame this business problem as a data problem."


def test_a_run_that_used_an_older_version_still_scores_after_a_bump(
    ws_dir: Path, capsys: pytest.CaptureFixture[str]
):
    ws = Workspace.open(ws_dir)
    run_id = complete_run(ws, BASELINE)
    assert main(["--workspace", str(ws_dir), "score", run_id]) == 0
    before = capsys.readouterr().out

    ws.update_task("T1.1", manual_time_minutes=90)

    assert main(["--workspace", str(ws_dir), "score", run_id]) == 0
    assert capsys.readouterr().out == before


def test_the_used_version_is_archived_beside_the_task(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    complete_run(ws, BASELINE)

    ws.update_task("T1.1", trap="A sharper trap.")

    archive = ws_dir / "tasks" / "T1.1" / "versions" / "1"
    assert sorted(p.name for p in archive.iterdir()) == [
        "gold.md",
        "input.md",
        "rubric.yaml",
        "task.md",
    ]
    assert [t.id for t in Workspace.open(ws_dir).tasks] == ["T1.1", "T2.1"]


def test_a_private_tasks_archive_stays_in_its_private_root(ws_dir: Path, tmp_path: Path):
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize")
    configure_private_root(ws_dir, private)
    write_candidate(ws_dir, "cleared@1", cleared("public_and_private", version="c-1"))
    ws = Workspace.open(ws_dir)
    run_id = complete_run(ws, "cleared@1")

    ws.update_task("T3.1", trap="A sharper trap.")

    assert (private / "tasks" / "T3.1" / "versions" / "1" / "task.md").is_file()
    assert not (ws_dir / "tasks" / "T3.1").exists()
    run = Workspace.open(ws_dir).resume_run(run_id)
    assert run.task("T3.1").version == 1


def test_an_unfinished_run_cannot_resume_once_a_task_it_used_is_bumped(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    run = ws.start_run(BASELINE)

    ws.update_task("T1.1", trap="A sharper trap.")

    with pytest.raises(RunMismatch, match="must finish on the Task versions it started with"):
        Workspace.open(ws_dir).resume_run(run.id)
