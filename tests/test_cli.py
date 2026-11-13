"""The `bench` CLI, driven in-process over golden fixture workspaces.

Each tests/fixtures/golden/<case>/ holds the Candidates and per-Task result files
for one Verdict path over the public examples/ Task; case.yaml names the
challenger, any extra `bench verdict` arguments and the expected Verdict.
"""

import shutil
from pathlib import Path

import pytest
import yaml

from bench.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"
DAY = "2026-11-13"
BASELINE = "claude@opus-5-5"
BASELINE_RUN = f"{DAY}-{BASELINE}"


def golden_workspace(case: str, tmp_path: Path) -> Path:
    """A throwaway workspace: the committed config and examples/ plus the case's Candidates."""
    root = tmp_path / "bench"
    shutil.copytree(REPO_ROOT / "config", root / "config")
    shutil.copytree(REPO_ROOT / "examples", root / "examples")
    shutil.copytree(GOLDEN / case / "candidates", root / "candidates")
    return root


def bench(ws: Path, *args: str) -> int:
    return main(["--workspace", str(ws), *args])


def record_run(ws: Path, case: str, candidate_id: str) -> str:
    results = GOLDEN / case / "results" / candidate_id
    assert bench(ws, "run", candidate_id, "--results", str(results), "--on", DAY) == 0
    return f"{DAY}-{candidate_id}"


COMPARISON_CASES = [
    "adopt-public-only",
    "reject-by-weighted",
    "reject-by-regression",
    "retest-by-margin",
    "retest-by-spot-check",
]


@pytest.mark.parametrize("case", COMPARISON_CASES)
def test_bench_verdict_on_golden_fixture_runs_gives_the_expected_verdict(
    case: str, tmp_path: Path, capsys
):
    spec = yaml.safe_load((GOLDEN / case / "case.yaml").read_text())
    ws = golden_workspace(case, tmp_path)
    baseline_run = record_run(ws, case, BASELINE)
    challenger_run = record_run(ws, case, spec["challenger"])
    capsys.readouterr()

    assert bench(ws, "verdict", challenger_run, baseline_run, *spec["verdict_args"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"Verdict: {spec['expected']['label']}"
    assert spec["expected"]["reason"] in out


def test_bench_verdict_rejects_a_candidate_whose_gate_failed_without_runs(
    tmp_path: Path, capsys
):
    spec = yaml.safe_load((GOLDEN / "reject-by-gate" / "case.yaml").read_text())
    ws = golden_workspace("reject-by-gate", tmp_path)

    assert bench(ws, "verdict", "--gate", spec["challenger"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"Verdict: {spec['expected']['label']}"
    assert spec["expected"]["reason"] in out


def test_bench_run_refuses_a_candidate_whose_gate_failed(tmp_path: Path, capsys):
    ws = golden_workspace("reject-by-gate", tmp_path)
    results = GOLDEN / "adopt-public-only" / "results" / "other@1"

    assert bench(ws, "run", "other@1", "--results", str(results), "--on", DAY) == 1

    assert "GateNotPassed" in capsys.readouterr().err
    assert not (ws / "runs").exists()
