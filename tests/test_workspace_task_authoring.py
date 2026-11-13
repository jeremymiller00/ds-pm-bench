from pathlib import Path

import pytest

from bench import TaskNotRunnable, Workspace

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
