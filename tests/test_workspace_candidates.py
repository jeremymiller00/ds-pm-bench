import datetime
from pathlib import Path

import pytest
import yaml

from bench import ConfigError, Workspace


def candidate(**overrides) -> dict:
    data = {
        "tool": "Claude",
        "version": "claude-opus-5-5",
        "adapter": "automated",
        "settings": {"temperature": 0.2, "max_tokens": 4000},
        "env_vars": ["ANTHROPIC_API_KEY"],
        "baseline": False,
        "gate": {
            "reviewer": "J. Reviewer",
            "date": "2026-11-01",
            "retention": "30 days, no training",
            "sso": True,
            "enterprise_terms": True,
            "connector_support": "None needed",
            "result": "pass",
            "clearance": "public_and_private",
        },
    }
    data.update(overrides)
    return data


def write_candidate(root: Path, candidate_id: str, data: dict) -> None:
    folder = root / "candidates"
    folder.mkdir(exist_ok=True)
    (folder / f"{candidate_id}.yaml").write_text(yaml.safe_dump(data))


def test_candidate_loads_with_its_gate_and_clearance(workspace_dir: Path):
    write_candidate(workspace_dir, "claude@opus-5-5", candidate(baseline=True))

    c = Workspace.open(workspace_dir).candidate("claude@opus-5-5")

    assert c.id == "claude@opus-5-5"
    assert c.tool == "Claude"
    assert c.version == "claude-opus-5-5"
    assert c.adapter == "automated"
    assert c.settings == {"temperature": 0.2, "max_tokens": 4000}
    assert c.env_vars == ("ANTHROPIC_API_KEY",)
    assert c.baseline is True
    assert c.gate.reviewer == "J. Reviewer"
    assert c.gate.date == datetime.date(2026, 11, 1)
    assert c.gate.retention == "30 days, no training"
    assert c.gate.sso is True
    assert c.gate.enterprise_terms is True
    assert c.gate.connector_support == "None needed"
    assert c.gate_passed is True
    assert c.clearance == "public_and_private"


def test_failed_or_missing_gate_means_no_clearance(workspace_dir: Path):
    failed = candidate()
    failed["gate"].update(result="fail")
    del failed["gate"]["clearance"]
    write_candidate(workspace_dir, "claude@opus-5-5", candidate(baseline=True))
    write_candidate(workspace_dir, "other@1", failed)
    write_candidate(workspace_dir, "ungated@1", candidate(gate=None))

    ws = Workspace.open(workspace_dir)

    assert [c.id for c in ws.candidates] == ["claude@opus-5-5", "other@1", "ungated@1"]
    assert (ws.candidate("other@1").gate_passed, ws.candidate("other@1").clearance) == (
        False,
        None,
    )
    assert ws.candidate("ungated@1").gate is None
    assert ws.candidate("ungated@1").clearance is None


def test_public_only_clearance(workspace_dir: Path):
    data = candidate(baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "claude@opus-5-5", data)

    assert Workspace.open(workspace_dir).candidate("claude@opus-5-5").clearance == (
        "public_only"
    )


def gate(**changes):
    def change(d):
        d["gate"].update(changes)
    return change


@pytest.mark.parametrize(
    "change, field",
    [
        (lambda d: d.update(tool=""), "tool"),
        (lambda d: d.update(version=None), "version"),
        (lambda d: d.update(adapter="browser"), "adapter"),
        (lambda d: d.update(settings=["temperature"]), "settings"),
        (lambda d: d["settings"].update(api_key="sk-live-123"), "settings.api_key"),
        (lambda d: d.update(env_vars="ANTHROPIC_API_KEY"), "env_vars"),
        (lambda d: d.update(env_vars=["sk-ant-abc123"]), "env_vars[0]"),
        (lambda d: d.update(baseline="yes"), "baseline"),
        (lambda d: d.update(owner="me"), "owner"),
        (gate(result="maybe"), "gate.result"),
        (gate(clearance="everything"), "gate.clearance"),
        (lambda d: d["gate"].pop("clearance"), "gate.clearance"),
        (gate(sso="yes"), "gate.sso"),
        (gate(date="last week"), "gate.date"),
        (gate(reviewer=""), "gate.reviewer"),
        (lambda d: d["gate"].pop("retention"), "gate.retention"),
    ],
    ids=[
        "blank-tool",
        "missing-version",
        "unknown-adapter",
        "settings-not-mapping",
        "secret-in-settings",
        "env-vars-not-list",
        "env-var-value-not-name",
        "baseline-not-bool",
        "unknown-field",
        "unknown-gate-result",
        "unknown-clearance",
        "pass-without-clearance",
        "sso-not-bool",
        "bad-gate-date",
        "blank-reviewer",
        "missing-retention",
    ],
)
def test_invalid_candidate_fails_naming_file_and_field(
    workspace_dir: Path, change, field: str
):
    data = candidate(baseline=True)
    change(data)
    write_candidate(workspace_dir, "claude@opus-5-5", data)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("candidates/claude@opus-5-5.yaml", field)


def test_candidate_file_must_be_named_tool_at_version(workspace_dir: Path):
    write_candidate(workspace_dir, "claude", candidate(baseline=True))

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("candidates/claude.yaml", "(file)")


def test_no_candidates_means_no_baseline_yet(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    assert (ws.candidates, ws.baseline) == ((), None)


def test_the_one_candidate_marked_baseline_is_the_baseline(workspace_dir: Path):
    write_candidate(workspace_dir, "claude@opus-5-5", candidate(baseline=True))
    write_candidate(workspace_dir, "other@1", candidate(tool="Other", version="1"))

    assert Workspace.open(workspace_dir).baseline.id == "claude@opus-5-5"


@pytest.mark.parametrize("marked", [[], ["a@1", "b@1"]], ids=["none", "two"])
def test_candidates_need_exactly_one_baseline(workspace_dir: Path, marked: list[str]):
    for cid in ("a@1", "b@1"):
        write_candidate(workspace_dir, cid, candidate(baseline=cid in marked))

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("candidates", "baseline")
    assert "exactly one" in err.value.message
