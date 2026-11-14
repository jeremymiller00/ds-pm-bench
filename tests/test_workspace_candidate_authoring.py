import datetime
from pathlib import Path

import pytest

from bench import CandidateAuthoringError, ConfigError, Workspace

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
    assert (c.baseline, c.gate, c.revision) == (True, None, 1)
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
