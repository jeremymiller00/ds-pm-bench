"""`bench judge` and `bench calibrate`, driven in-process with fake judge models: the CLI
is given a Workspace opener, as bench.wiring.open_workspace is in production."""

from pathlib import Path

import pytest

from bench import Workspace
from bench.cli import main
from test_workspace_judging import CANDIDATE, DAY, FakeJudge, FakeJudges, recorded_run
from test_workspace_rubric_editing import ws_dir  # noqa: F401

JUDGE = "openai/gpt-6"


def bench(ws_dir: Path, judges: FakeJudges, *args: str) -> int:  # noqa: F811
    return main(["--workspace", str(ws_dir), *args],
                open_workspace=lambda path: Workspace.open(path, judges=judges))


@pytest.fixture
def run_id(ws_dir: Path) -> str:  # noqa: F811
    return recorded_run(Workspace.open(ws_dir)).id


def test_bench_judge_refuses_an_uncertified_judge_without_the_override(
    ws_dir: Path, run_id: str, capsys  # noqa: F811
):
    judges = FakeJudges()

    assert bench(ws_dir, judges, "judge", run_id) == 1

    assert "JudgeNotCertified" in capsys.readouterr().err
    assert judges.by_label == {}  # no judge model was called


def test_bench_judge_with_the_override_scores_the_run_and_logs_it(
    ws_dir: Path, run_id: str, capsys  # noqa: F811
):
    judges = FakeJudges(**{JUDGE: FakeJudge(score=4, scores={"clarity": 2})})

    assert bench(ws_dir, judges, "judge", run_id, "--allow-uncertified") == 0

    out = capsys.readouterr().out
    assert "T1.1" in out and "clarity 2" in out
    assert "uncertified" in out
    ws = Workspace.open(ws_dir)
    assert ws.resume_run(run_id).unjudged == ()
    assert [e["kind"] for e in ws.decision_log()] == ["uncertified_judge_override"]


def test_bench_calibrate_adds_reports_and_certifies(ws_dir: Path, run_id: str, capsys):  # noqa: F811
    judges = FakeJudges(**{JUDGE: FakeJudge(score=4)})

    assert bench(ws_dir, judges, "calibrate", "add", run_id, "T1.1") == 0
    assert "c0001" in capsys.readouterr().out

    ws = Workspace.open(ws_dir, judges=judges)
    ws.update_settings(calibration={"min_items": 1})
    ws.score_calibration_item("c0001", {c: 4 for c in ws.calibration_items()[0].criteria})
    assert bench(ws_dir, judges, "calibrate", "status") == 0
    status = capsys.readouterr().out
    assert "not certified" in status and "not scored by Judge Setup" in status
    assert bench(ws_dir, judges, "calibrate", "certify") == 1
    assert "CertificationFailed" in capsys.readouterr().err

    ws.judge_calibration_set()
    assert bench(ws_dir, judges, "calibrate", "certify", "--on", DAY.isoformat()) == 0
    assert f"certified Judge Setup {ws.judge_setup.hash}" in capsys.readouterr().out
    assert bench(ws_dir, judges, "calibrate", "status") == 0
    assert "100% within 1 point" in capsys.readouterr().out
    assert Workspace.open(ws_dir).judge_certified


def test_bench_calibrate_add_refuses_an_unknown_run(ws_dir: Path, capsys):  # noqa: F811
    assert bench(ws_dir, FakeJudges(), "calibrate", "add", f"2020-01-01-{CANDIDATE}",
                 "T1.1") == 1
    assert "UnknownRunError" in capsys.readouterr().err
