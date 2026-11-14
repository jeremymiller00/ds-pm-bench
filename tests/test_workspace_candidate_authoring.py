import datetime
from pathlib import Path

import pytest

from bench import CandidateAuthoringError, ConfigError, GateNotPassed, Workspace
from test_workspace_tasks import write_task

DAY = datetime.date(2026, 11, 13)

CHECKLIST = {
    "retention": "30 days, no training",
    "sso": True,
    "enterprise_terms": True,
    "connector_support": "None needed",
}


def new_candidate(ws: Workspace, **overrides):
    args = dict(
        tool="Claude",
        version="claude-opus-5-5",
        adapter="automated",
        settings={"temperature": 0.2},
        env_vars=["ANTHROPIC_API_KEY"],
        baseline=True,
    )
    args.update(overrides)
    return ws.create_candidate(**args)


def test_a_new_candidate_is_written_to_candidates_and_loads_back(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    c = new_candidate(ws)

    assert c.id == "claude@claude-opus-5-5"
    assert (workspace_dir / "candidates" / "claude@claude-opus-5-5.yaml").is_file()
    assert (c.tool, c.version, c.adapter) == ("Claude", "claude-opus-5-5", "automated")
    assert c.settings == {"temperature": 0.2}
    assert c.env_vars == ("ANTHROPIC_API_KEY",)
    assert (c.baseline, c.gate, c.lineage_position) == (True, None, 1)
    assert ws.candidate(c.id) == c
    assert Workspace.open(workspace_dir).candidate(c.id) == c


def test_the_first_candidate_must_be_the_baseline(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    with pytest.raises(CandidateAuthoringError, match="must be the Baseline"):
        new_candidate(ws, baseline=False)

    assert not (workspace_dir / "candidates").exists() or not any(
        (workspace_dir / "candidates").iterdir()
    )


def test_marking_a_new_baseline_unmarks_the_old_one(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    old = new_candidate(ws)
    other = new_candidate(ws, tool="Other", version="1", baseline=False)

    assert ws.baseline.id == old.id

    ws.mark_baseline(other.id)

    assert ws.baseline.id == other.id
    assert [c.id for c in ws.candidates if c.baseline] == [other.id]
    assert Workspace.open(workspace_dir).baseline.id == other.id


def test_creating_a_candidate_as_baseline_moves_the_mark(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    new_candidate(ws)

    newer = new_candidate(ws, version="claude-opus-6", baseline=True)

    assert [c.id for c in Workspace.open(workspace_dir).candidates if c.baseline] == [
        newer.id
    ]


def test_a_taken_candidate_id_is_refused(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    new_candidate(ws)

    with pytest.raises(CandidateAuthoringError, match="already exists"):
        new_candidate(ws, settings={"temperature": 0.9})


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"adapter": "browser"}, "adapter"),
        ({"env_vars": ["sk-ant-abc123"]}, "env_vars[0]"),
        ({"settings": {"api_key": "sk-live"}}, "settings.api_key"),
        ({"adapter": "manual_assisted", "cost_per_task_usd": -1}, "cost_per_task_usd"),
        ({"cost_per_task_usd": 0.5}, "cost_per_task_usd"),
    ],
    ids=["unknown-adapter", "env-var-value", "secret-setting", "negative-cost",
         "flat-cost-on-automated"],
)
def test_an_invalid_new_candidate_names_the_field_and_writes_nothing(
    workspace_dir: Path, overrides: dict, field: str
):
    ws = Workspace.open(workspace_dir)

    with pytest.raises(ConfigError) as err:
        new_candidate(ws, **overrides)

    assert err.value.field == field
    assert not list(workspace_dir.glob("candidates/*.yaml"))
    assert ws.candidates == ()


def test_a_passing_gate_is_recorded_with_its_clearance(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    c = new_candidate(ws)

    gated = ws.record_gate(
        c.id,
        reviewer="J. Reviewer",
        on=DAY,
        checklist=CHECKLIST,
        passed=True,
        clearance="public_only",
    )

    assert gated.id == c.id
    assert gated.gate_passed and gated.clearance == "public_only"
    assert (gated.gate.reviewer, gated.gate.date) == ("J. Reviewer", DAY)
    assert gated.gate.retention == "30 days, no training"
    assert (gated.gate.sso, gated.gate.enterprise_terms) == (True, True)
    assert gated.gate.connector_support == "None needed"
    assert Workspace.open(workspace_dir).candidate(c.id) == gated
    assert ws.decision_log() == ()


def test_a_failed_gate_logs_a_reject_verdict(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    baseline = new_candidate(ws)
    other = new_candidate(ws, tool="Other", version="1", baseline=False)

    failed = ws.record_gate(
        other.id,
        reviewer="J. Reviewer",
        on=DAY,
        checklist={**CHECKLIST, "sso": False},
        passed=False,
        clearance=None,
    )

    assert failed.gate.result == "fail" and failed.clearance is None
    [entry] = ws.decision_log()
    assert entry["date"] == "2026-11-13"
    assert entry["verdict"] == "reject"
    assert entry["candidate"] == {"id": "other@1", "version": "1"}
    assert entry["baseline"]["id"] == baseline.id
    assert entry["rationale"] == ["Gate: its Gate failed, so other@1 cannot be Run or adopted"]


@pytest.mark.parametrize(
    "passed, clearance, checklist, field",
    [
        (True, None, CHECKLIST, "gate.clearance"),
        (True, "everything", CHECKLIST, "gate.clearance"),
        (False, "public_only", CHECKLIST, "gate.clearance"),
        (True, "public_only", {**CHECKLIST, "sso": "yes"}, "gate.sso"),
        (True, "public_only", {k: v for k, v in CHECKLIST.items() if k != "retention"},
         "gate.retention"),
        (True, "public_only", {**CHECKLIST, "vibes": "good"}, "gate.vibes"),
    ],
    ids=["pass-without-clearance", "unknown-clearance", "fail-with-clearance",
         "sso-not-bool", "missing-retention", "unknown-checklist-item"],
)
def test_an_invalid_gate_names_the_field_and_changes_nothing(
    workspace_dir: Path, passed, clearance, checklist, field
):
    ws = Workspace.open(workspace_dir)
    c = new_candidate(ws)

    with pytest.raises(ConfigError) as err:
        ws.record_gate(c.id, reviewer="J. Reviewer", on=DAY, checklist=checklist,
                       passed=passed, clearance=clearance)

    assert err.value.field == field
    assert Workspace.open(workspace_dir).candidate(c.id).gate is None
    assert ws.decision_log() == ()


def gated(ws: Workspace, candidate_id: str, *, on=DAY, **checklist):
    return ws.record_gate(
        candidate_id,
        reviewer="J. Reviewer",
        on=on,
        checklist={**CHECKLIST, **checklist},
        passed=True,
        clearance="public_only",
    )


@pytest.fixture
def used(workspace_dir: Path):
    """A gated Baseline that a Run has used, and the Run."""
    write_task(workspace_dir, "T1.1")
    ws = Workspace.open(workspace_dir)
    c = gated(ws, new_candidate(ws).id)
    run = ws.start_run(c.id, on=DAY)
    return ws, c, run


def test_an_unused_candidate_is_changed_in_place(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    c = new_candidate(ws)

    changed = ws.update_candidate(c.id, settings={"temperature": 0.7})

    assert (changed.id, changed.lineage_position) == (c.id, 1)
    assert changed.settings == {"temperature": 0.7}
    assert [x.id for x in Workspace.open(workspace_dir).candidates] == [c.id]


def test_changing_a_used_candidate_adds_a_candidate_to_its_lineage(workspace_dir: Path, used):
    ws, c, run = used
    before = (workspace_dir / "candidates" / f"{c.id}.yaml").read_text()

    changed = ws.update_candidate(c.id, settings={"temperature": 0.7})

    assert (changed.id, changed.lineage_position, changed.lineage) == (f"{c.id}~2", 2, c.id)
    assert changed.settings == {"temperature": 0.7}
    assert (changed.tool, changed.version) == (c.tool, c.version)
    assert changed.gate == c.gate
    assert (workspace_dir / "candidates" / f"{c.id}.yaml").read_text().replace(
        "baseline: true", "baseline: false"
    ) == before.replace("baseline: true", "baseline: false")
    assert ws.candidate(c.id).settings == {"temperature": 0.2}
    assert ws.resume_run(run.id).candidate.id == c.id


def test_the_next_candidate_in_the_baselines_lineage_takes_the_baseline_mark(used):
    ws, c, _ = used

    changed = ws.update_candidate(c.id, env_vars=["OTHER_KEY"])

    assert ws.baseline.id == changed.id
    assert not ws.candidate(c.id).baseline


def test_regating_a_used_candidate_updates_its_gate_in_place(workspace_dir: Path, used):
    ws, c, run = used
    later = DAY + datetime.timedelta(days=1)

    regated = ws.record_gate(
        c.id, reviewer="A. Nother", on=later, checklist=CHECKLIST, passed=False,
        clearance=None,
    )

    assert regated.id == c.id and regated.lineage_position == 1
    assert [x.id for x in Workspace.open(workspace_dir).candidates] == [c.id]
    assert regated.gate.result == "fail" and regated.gate.date == later
    assert [(r.reviewer, r.result) for r in regated.gate_reviews] == [
        ("J. Reviewer", "pass"), ("A. Nother", "fail"),
    ]
    assert Workspace.open(workspace_dir).candidate(c.id).gate_reviews == regated.gate_reviews


def test_a_failed_gate_stops_a_run_in_progress(workspace_dir: Path, used):
    ws, c, run = used
    ws.record_gate(c.id, reviewer="A. Nother", on=DAY, checklist=CHECKLIST,
                   passed=False, clearance=None)

    with pytest.raises(GateNotPassed):
        ws.resume_run(run.id)
    with pytest.raises(GateNotPassed):
        ws.task_input(run, "T1.1")


def test_lineage_positions_number_on_from_the_highest(used):
    ws, c, _ = used
    second = ws.update_candidate(c.id, settings={"temperature": 0.7})
    ws.start_run(second.id, on=DAY + datetime.timedelta(days=1))

    third = ws.update_candidate(second.id, settings={"temperature": 0.9})

    assert third.id == f"{c.id}~3"
    assert [x.id for x in ws.current_candidates] == [third.id]


def test_a_superseded_candidate_cannot_be_changed(used):
    ws, c, _ = used
    newer = ws.update_candidate(c.id, settings={"temperature": 0.7})

    with pytest.raises(CandidateAuthoringError, match=f"superseded by {newer.id}"):
        ws.update_candidate(c.id, settings={"temperature": 0.9})


def test_a_superseded_candidate_can_still_be_regated(used):
    """Its Gate is a review of it, so failing it stops a Run of it still in progress."""
    ws, c, run = used
    ws.update_candidate(c.id, settings={"temperature": 0.7})

    ws.record_gate(c.id, reviewer="A. Nother", on=DAY, checklist=CHECKLIST,
                   passed=False, clearance=None)

    with pytest.raises(GateNotPassed):
        ws.resume_run(run.id)


@pytest.mark.parametrize("field", ["tool", "version", "baseline", "gate", "id"])
def test_fields_that_name_a_different_candidate_cannot_be_changed(
    workspace_dir: Path, field: str
):
    ws = Workspace.open(workspace_dir)
    c = new_candidate(ws)

    with pytest.raises(CandidateAuthoringError, match=field):
        ws.update_candidate(c.id, **{field: "x"})


def test_a_manual_assisted_candidate_records_its_flat_cost_per_task(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    new_candidate(ws)
    manual = new_candidate(
        ws, tool="Chat UI", version="2026-11", adapter="manual_assisted",
        env_vars=[], cost_per_task_usd=0.25, baseline=False,
    )
    unpriced = new_candidate(
        ws, tool="Other", version="1", adapter="manual_assisted", baseline=False
    )

    assert manual.id == "chat-ui@2026-11"
    assert Workspace.open(workspace_dir).candidate(manual.id).task_cost_usd == 0.25
    assert unpriced.task_cost_usd is None
    assert ws.candidate("claude@claude-opus-5-5").task_cost_usd is None
