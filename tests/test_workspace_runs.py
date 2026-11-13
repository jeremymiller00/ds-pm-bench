import datetime
from pathlib import Path

import pytest
import yaml

from bench import (
    ClearanceError,
    GateNotPassed,
    RunExists,
    RunIncomplete,
    RunMismatch,
    TaskResult,
    Workspace,
)
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
    assert "Real Q3 roadmap" in Workspace.open(ws_dir).task_input(run, "T3.1")


def test_a_private_tasks_input_is_refused_without_private_clearance(
    ws_dir: Path, private_root: Path
):
    ws = Workspace.open(ws_dir)
    run = ws.start_run("claude@opus-5-5", on=DAY)
    (private_root / "tasks" / "T3.1" / "input.md").chmod(0)  # any read would raise

    try:
        with pytest.raises(ClearanceError, match="T3.1"):
            ws.task_input(run, "T3.1")
    finally:
        (private_root / "tasks" / "T3.1" / "input.md").chmod(0o644)


def test_task_input_is_the_canonical_prompt_then_the_input(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    run = ws.start_run("claude@opus-5-5", on=DAY)

    assert ws.task_input(run, "T1.1") == (
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


SCORES = {
    "correctness": 4,
    "completeness": 3,
    "clarity": 5,
    "pm_judgment": 4,
    "format_fit": 4,
    "names_the_decision": 2,
}


def record(run, task_id: str, *, cost_usd: float | None = 0.03) -> None:
    run.record_output(
        task_id,
        raw="one two three four five",
        final="one two three four six",
        active_seconds=600,
        latency_seconds=12,
        cost_usd=cost_usd,
    )
    run.record_criterion_scores(task_id, SCORES)


def test_a_completed_run_produces_task_results_the_workspace_can_score(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    run = ws.start_run("claude@opus-5-5", on=DAY)
    record(run, "T1.1")
    record(run, "T2.1", cost_usd=None)

    results = run.task_results()

    assert results[0] == TaskResult(
        task_id="T1.1",
        track="Framing",
        criterion_scores=SCORES,
        edit_distance=0.2,
        active_seconds=600,
        manual_minutes=45,
        cost_usd=0.03,
        latency_seconds=12,
    )
    assert [(r.task_id, r.track, r.cost_usd) for r in results[1:]] == [("T2.1", "Specs", None)]
    assert ws.score(results).latency_only_tasks == ("T2.1",)


def test_task_results_refuse_an_incomplete_run(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)
    run.record_output(
        "T1.1", raw="a", final="a", active_seconds=1, latency_seconds=1, cost_usd=None
    )

    with pytest.raises(RunIncomplete) as err:
        run.task_results()

    assert "T1.1 has no Criterion scores" in str(err.value)
    assert "T2.1 has no Output" in str(err.value)


def test_criterion_scores_must_cover_exactly_the_tasks_rubric(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)
    with pytest.raises(ValueError, match="no Output"):
        run.record_criterion_scores("T1.1", SCORES)
    run.record_output(
        "T1.1", raw="a", final="a", active_seconds=1, latency_seconds=1, cost_usd=None
    )

    without = {k: v for k, v in SCORES.items() if k != "names_the_decision"}
    with pytest.raises(ValueError, match="names_the_decision"):
        run.record_criterion_scores("T1.1", without)
    with pytest.raises(ValueError, match="1-5"):
        run.record_criterion_scores("T1.1", {**SCORES, "clarity": 6})


def test_a_half_finished_run_resumes_where_it_left_off(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)
    record(run, "T1.1")
    assert run.pending == ("T2.1",)

    resumed = Workspace.open(ws_dir).resume_run(run.id)

    assert resumed.pending == ("T2.1",)
    assert (resumed.date, resumed.candidate_id, resumed.task_versions) == (
        DAY,
        "claude@opus-5-5",
        {"T1.1": 2, "T2.1": 1},
    )
    record(resumed, "T2.1")
    assert [r.task_id for r in resumed.task_results()] == ["T1.1", "T2.1"]


def test_starting_a_run_that_exists_says_to_resume_it(ws_dir: Path):
    Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)

    with pytest.raises(RunExists, match="resume"):
        Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)


def test_a_run_cannot_resume_once_a_task_it_used_has_a_new_version(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)
    task_md = ws_dir / "tasks" / "T1.1" / "task.md"
    task_md.write_text(task_md.read_text().replace("version: 2", "version: 3"))

    with pytest.raises(RunMismatch, match="T1.1"):
        Workspace.open(ws_dir).resume_run(run.id)


def test_task_input_for_a_task_the_workspace_does_not_have_is_unknown(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    run = ws.start_run("claude@opus-5-5", on=DAY)

    with pytest.raises(LookupError, match="T9.9"):
        ws.task_input(run, "T9.9")


def test_resuming_an_unknown_run_fails(ws_dir: Path):
    with pytest.raises(LookupError, match="2026-01-01-nope@1"):
        Workspace.open(ws_dir).resume_run("2026-01-01-nope@1")


def test_a_tasks_output_is_recorded_once(ws_dir: Path):
    run = Workspace.open(ws_dir).start_run("claude@opus-5-5", on=DAY)
    record(run, "T1.1")

    with pytest.raises(ValueError, match="already recorded"):
        record(run, "T1.1")
