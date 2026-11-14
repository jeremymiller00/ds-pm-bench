"""Editing the shared Rubric (story 63), and rebuilding what a past Run was judged with
after the judge prompt and the Rubric change (ADR-0003)."""

import datetime
from pathlib import Path

import pytest

from bench import ConfigEditRefused, ConfigError, Workspace
from test_workspace_candidates import candidate, write_candidate
from test_workspace_judging import (
    CANDIDATE,
    DAY,
    FakeJudges,
    recorded_run,
    write_judge_setup,
)
from test_workspace_tasks import write_task

TIMELINESS = {"id": "timeliness", "name": "Timeliness", "description": "Fits the deadline."}


@pytest.fixture
def ws_dir(workspace_dir: Path) -> Path:
    write_task(workspace_dir, "T1.1")
    data = candidate(tool="chatbot", version="web-1", adapter="manual_assisted",
                     env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, CANDIDATE, data)
    write_judge_setup(workspace_dir)
    return workspace_dir


def criteria(ws: Workspace) -> list[dict]:
    return [{"id": c.id, "name": c.name, "description": c.description}
            for c in ws.rubric.criteria]


def test_shared_criteria_are_added_edited_and_removed(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    changed = criteria(ws)[1:] + [TIMELINESS]  # remove correctness, add timeliness
    changed[0]["description"] = "Covers everything asked."

    rubric = ws.update_rubric(changed)

    ids = [c.id for c in rubric.criteria]
    assert ids == ["completeness", "clarity", "pm_judgment", "format_fit", "timeliness"]
    assert rubric.criterion("completeness").description == "Covers everything asked."
    reopened = Workspace.open(ws_dir)
    assert [c.id for c in reopened.rubric.criteria] == ids
    assert [c.id for c in reopened.task("T1.1").rubric.criteria] == ids + ["names_the_decision"]


def test_an_invalid_rubric_edit_names_the_field_and_writes_nothing(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    before = (ws_dir / "config" / "rubric.yaml").read_text()

    with pytest.raises(ConfigError, match=r"criteria\[0\].id"):
        ws.update_rubric([{"id": "Bad Id", "name": "x", "description": "y"}])
    with pytest.raises(ConfigError, match="criteria"):
        ws.update_rubric([])

    assert (ws_dir / "config" / "rubric.yaml").read_text() == before


def test_a_shared_criterion_a_task_already_defines_is_refused(ws_dir: Path):
    ws = Workspace.open(ws_dir)
    clash = {"id": "names_the_decision", "name": "Decision", "description": "Names it."}

    with pytest.raises(ConfigEditRefused, match="T1.1"):
        ws.update_rubric(criteria(ws) + [clash])

    assert "names_the_decision" not in [c.id for c in Workspace.open(ws_dir).rubric.criteria]


def test_a_judged_run_rebuilds_its_judge_prompt_and_rubric_after_both_change(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    run = recorded_run(ws)
    ws.judge_run(run, allow_uncertified=True)
    old_prompt, old_criteria = ws.judge_setup.prompt, criteria(ws)

    ws.update_judge_setup(prompt="Score harshly.")
    ws.update_rubric(old_criteria[1:] + [TIMELINESS])
    (ws_dir / "config" / "judge" / "prompt.md").write_text("Edited by hand, uncommitted.")
    ws = Workspace.open(ws_dir)
    reopened = ws.resume_run(run.id)

    assert ws.run_judge_prompt(reopened) == old_prompt
    assert criteria_of(ws.run_rubric(reopened)) == old_criteria
    # The Run still loads, scores and lists its Criteria as it was judged.
    assert [c.id for c in reopened.task("T1.1").rubric.criteria][0] == "correctness"
    assert reopened.complete


def test_a_certification_records_the_rubric_and_judge_prompt_it_used(ws_dir: Path):
    ws = Workspace.open(ws_dir, judges=FakeJudges())
    ws.update_settings(calibration={"min_items": 1})
    item = ws.add_calibration_item(recorded_run(ws).id, "T1.1")
    ws.score_calibration_item(item.id, {c: 4 for c in item.criteria})
    ws.judge_calibration_set()
    ws.certify_judge(on=DAY + datetime.timedelta(days=1))
    [entry] = ws.decision_log()
    old_prompt, old_criteria = ws.judge_setup.prompt, criteria(ws)

    ws.update_judge_setup(prompt="Score harshly.")
    ws.update_rubric(old_criteria + [TIMELINESS])

    assert ws.archived_judge_prompt(entry["judge_setup"]["prompt_hash"]) == old_prompt
    assert criteria_of(ws.archived_rubric(entry["rubric_hash"])) == old_criteria


def criteria_of(rubric) -> list[dict]:
    return [{"id": c.id, "name": c.name, "description": c.description}
            for c in rubric.criteria]
