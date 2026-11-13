import datetime
from pathlib import Path

import pytest
import yaml

from bench import Workspace
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
