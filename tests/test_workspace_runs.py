import datetime
from pathlib import Path

import pytest
import yaml

from bench import ClearanceError, GateNotPassed, Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import configure_private_root, write_task

DAY = datetime.date(2026, 11, 13)


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    """A workspace with one public Task and a gated Baseline cleared for public only."""
    write_task(workspace_dir, "T1.1", version=2)
    write_task(workspace_dir, "T2.1", track="Specs", folder="examples")
    write_candidate(workspace_dir, "claude@opus-5-5", cleared("public_only", baseline=True))
    return workspace_dir


def cleared(clearance: str, **overrides) -> dict:
    data = candidate(**overrides)
    data["gate"]["clearance"] = clearance
    return data


def test_starting_a_run_snapshots_candidate_task_versions_and_date(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)

    assert run.id == "2026-11-13-claude@opus-5-5"
    assert run.candidate_id == "claude@opus-5-5"
    assert run.date == DAY
    assert run.task_versions == {"T1.1": 2, "T2.1": 1}
    assert run.suite == "public_only"
    snapshot = yaml.safe_load((ws_dir / "runs" / run.id / "run.yaml").read_text())
    assert snapshot["candidate"]["version"] == "claude-opus-5-5"
    assert snapshot["candidate"]["settings"] == {"temperature": 0.2, "max_tokens": 4000}
    assert snapshot["date"] == "2026-11-13"
    assert snapshot["tasks"] == {"T1.1": 2, "T2.1": 1}


@pytest.mark.parametrize(
    "gate", [None, {"result": "fail", "clearance": None}], ids=["no-gate", "failed-gate"]
)
def test_a_candidate_without_a_passing_gate_cannot_be_run(ws_dir: Path, gate):
    data = cleared("public_and_private")
    data["gate"] = None if gate is None else {**data["gate"], **gate}
    write_candidate(ws_dir, "other@1", data)

    with pytest.raises(GateNotPassed, match="other@1"):
        Workspace.open(ws_dir).start_run("other@1", on=DAY)

    assert not (ws_dir / "runs").exists()


@pytest.fixture
def private_root(ws_dir: Path, tmp_path: Path) -> Path:
    private = tmp_path / "bench-private"
    write_task(private, "T3.1", track="Prioritize", input_text="Real Q3 roadmap for Acme.")
    configure_private_root(ws_dir, private)
    write_candidate(ws_dir, "cleared@1", cleared("public_and_private"))
    return private


def test_a_public_only_candidate_gets_a_public_only_suite(ws_dir: Path, private_root: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)

    assert run.suite == "public_only"
    assert run.task_versions == {"T1.1": 2, "T2.1": 1}


def test_a_privately_cleared_candidate_gets_private_tasks_too(
    ws_dir: Path, private_root: Path
):
    run = Workspace.open(ws_dir).start_run("cleared@1", on=DAY)

    assert run.suite == "public_and_private"
    assert run.task_versions == {"T1.1": 2, "T2.1": 1, "T3.1": 1}
    assert "Real Q3 roadmap" in run.task_input("T3.1")


def test_a_private_tasks_input_is_refused_without_private_clearance(
    ws_dir: Path, private_root: Path
):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)
    (private_root / "tasks" / "T3.1" / "input.md").chmod(0)  # any read would raise

    try:
        with pytest.raises(ClearanceError, match="T3.1"):
            run.task_input("T3.1")
    finally:
        (private_root / "tasks" / "T3.1" / "input.md").chmod(0o644)


def test_task_input_is_the_canonical_prompt_then_the_input(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)

    assert run.task_input("T1.1") == (
        "Frame this business problem as a data problem.\n\n"
        "Churn rose 4% last quarter in the SMB segment."
    )


@pytest.mark.parametrize(
    "raw, final, expected",
    [
        ("the churn rose four percent", "the churn rose four percent", 0.0),
        ("the churn rose four percent", "the churn fell four percent", 0.2),
        ("the churn rose", "the churn rose four percent", 0.4),
        ("one two three four", "", 1.0),
        ("", "", 0.0),
        ("a  b\nc", "a b c", 0.0),
    ],
    ids=["unchanged", "one-substituted", "two-added", "all-deleted", "both-empty", "whitespace"],
)
def test_edit_distance_is_the_share_of_words_changed(ws_dir: Path, raw, final, expected):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)

    record = run.record_output(
        "T1.1", raw=raw, final=final, active_seconds=600, latency_seconds=12, cost_usd=0.03
    )

    assert record.edit_distance == pytest.approx(expected)


def test_a_public_tasks_output_and_numbers_are_kept_in_the_public_run(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)

    run.record_output(
        "T1.1",
        raw="Raw draft words",
        final="Edited draft words",
        active_seconds=600,
        latency_seconds=12,
        cost_usd=0.03,
    )

    folder = ws_dir / "runs" / run.id
    assert (folder / "raw" / "T1.1.md").read_text() == "Raw draft words"
    assert (folder / "final" / "T1.1.md").read_text() == "Edited draft words"
    numbers = yaml.safe_load((folder / "results" / "T1.1.yaml").read_text())
    assert numbers == {
        "task": "T1.1",
        "version": 2,
        "visibility": "public",
        "edit_distance": pytest.approx(1 / 3),
        "active_seconds": 600,
        "latency_seconds": 12,
        "cost_usd": 0.03,
    }


def test_a_private_tasks_content_goes_only_to_the_private_root(
    ws_dir: Path, private_root: Path
):
    run = Workspace.open(ws_dir).start_run("cleared@1", on=DAY)

    run.record_output(
        "T3.1",
        raw="Acme roadmap raw secret",
        final="Acme roadmap final secret",
        active_seconds=900,
        latency_seconds=40,
        cost_usd=None,
    )

    private_run = private_root / "runs" / run.id
    assert (private_run / "raw" / "T3.1.md").read_text() == "Acme roadmap raw secret"
    assert (private_run / "final" / "T3.1.md").read_text() == "Acme roadmap final secret"
    numbers = yaml.safe_load((ws_dir / "runs" / run.id / "results" / "T3.1.yaml").read_text())
    assert numbers["visibility"] == "private"
    assert numbers["edit_distance"] == pytest.approx(0.25)
    public_text = "".join(
        p.read_text() for p in ws_dir.rglob("*") if p.is_file() and "config" not in p.parts
    )
    assert "Acme" not in public_text
    assert "secret" not in public_text
