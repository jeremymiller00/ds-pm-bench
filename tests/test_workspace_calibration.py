from pathlib import Path

import pytest

from bench import CalibrationError, Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import (
    CANDIDATE,
    OUTPUT,
    FakeJudges,
    private_run,
    recorded_run,
    write_judge_setup,
)
from test_workspace_tasks import configure_private_root, write_task


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """One public Task, a manual-assisted Baseline cleared for it, and a Judge Setup."""
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted",
                     env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    write_judge_setup(workspace_dir)
    return workspace_dir


@pytest.fixture
def private_ws(ws_dir: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Adds a Private Task T3.1 and a Candidate cleared for it (cleared@1)."""
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize", input_text="Real Q3 roadmap for Acme.")
    configure_private_root(ws_dir, private)
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted", env_vars=[])
    write_candidate(ws_dir, "cleared@1", data)
    return ws_dir, private


def test_an_output_of_a_finished_run_joins_the_calibration_set_blind(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)

    item = ws.add_calibration_item(run.id, "T1.1")

    [blind] = ws.unscored_calibration_items()
    assert blind.id == item.id
    assert blind.output == OUTPUT
    assert "Churn rose 4% last quarter" in blind.input
    assert blind.gold_answer.startswith("Problem: predict SMB churn")
    assert [c.id for c in blind.criteria][-1] == "names_the_decision"
    seen = repr(blind)
    for identity in ("chatbot", "web-1", CANDIDATE, run.id, "draft"):
        assert identity not in seen
    assert (ws_dir / "calibration" / f"{item.id}.yaml").is_file()


def test_a_private_tasks_item_keeps_its_content_in_the_private_root(private_ws):
    ws_dir, private = private_ws
    ws = Workspace.open(ws_dir)
    run = private_run(ws)

    item = ws.add_calibration_item(run.id, "T3.1")

    public = (ws_dir / "calibration" / f"{item.id}.yaml").read_text()
    for content in ("final T3.1", "Real Q3 roadmap", "Gold", str(private)):
        assert content not in public
    assert (private / "calibration" / "content" / f"{item.id}.yaml").is_file()
    assert not (ws_dir / "calibration" / "content").exists()
    assert item.visibility == "private"
    [blind] = ws.unscored_calibration_items()
    assert blind.output == "final T3.1"
    assert "Real Q3 roadmap for Acme." in blind.input


def test_an_unfinished_run_or_an_output_already_in_the_set_is_refused(ws_dir: Path):
    write_task(ws_dir, "T1.2")
    ws = Workspace.open(ws_dir)
    run = recorded_run(ws)  # T1.2 still pending
    with pytest.raises(CalibrationError, match="not finished"):
        ws.add_calibration_item(run.id, "T1.1")
    run.record_output("T1.2", raw="d", final="f", active_seconds=1, latency_seconds=1,
                      cost_usd=None)
    ws.add_calibration_item(run.id, "T1.1")

    with pytest.raises(CalibrationError, match="already"):
        ws.add_calibration_item(run.id, "T1.1")

    assert [i.task_id for i in ws.calibration_items()] == ["T1.1"]
