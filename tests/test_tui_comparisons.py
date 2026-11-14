"""Textual pilot tests for the Comparisons and Decision Log tabs, over golden fixture Runs.

Runs are recorded from tests/fixtures/golden through `bench run --results`; a fake Clock
dates the Decision Log entries. No model is called and nothing waits on wall time.
"""

import shutil
from pathlib import Path

import yaml
from textual.widgets import OptionList, Select, Static

from bench import Workspace
from bench.cli import main
from bench.tui.app import BenchApp
from bench.tui.comparisons import ComparisonScreen
from test_cli import BASELINE, GOLDEN, golden_workspace
from test_workspace_attempts import FakeClock
from test_workspace_judging import FakeJudges

CHALLENGER = "other@1"
DAY = "2026-11-13"


def record_run(ws: Path, results: Path, candidate_id: str, on: str = DAY) -> str:
    args = ["--workspace", str(ws), "run", candidate_id, "--results", str(results), "--on", on]
    assert main(args) == 0
    return f"{on}-{candidate_id}"


def golden_runs(case: str, tmp_path: Path) -> tuple[Path, str, str]:
    """A golden case's workspace with both Runs recorded; returns it, challenger, Baseline."""
    ws = golden_workspace(case, tmp_path)
    baseline = record_run(ws, GOLDEN / case / "results" / BASELINE, BASELINE)
    challenger = record_run(ws, GOLDEN / case / "results" / CHALLENGER, CHALLENGER)
    return ws, challenger, baseline


def text(app: BenchApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).content)


async def open_tab(pilot, pane_id: str) -> None:
    await pilot.click(f"#--content-tab-{pane_id}")
    await pilot.pause()


async def compare(app: BenchApp, pilot, challenger: str, baseline: str) -> None:
    await open_tab(pilot, "comparisons")
    app.query_one("#cmp-challenger", Select).value = challenger
    app.query_one("#cmp-baseline", Select).value = baseline
    await pilot.pause()
    await pilot.click("#cmp-compare")
    await pilot.pause()


async def test_two_fixture_runs_reach_a_logged_verdict_and_browse_back_to_it(tmp_path: Path):
    ws, challenger, baseline = golden_runs("adopt-public-only", tmp_path)
    app = BenchApp(Workspace.open(ws, clock=FakeClock()))
    async with app.run_test() as pilot:
        await compare(app, pilot, challenger, baseline)
        card = text(app, "#cmp-scorecard")
        assert card.splitlines()[0] == "Verdict: adopt (public-only Suite, not a full adopt)"
        assert "at least the Baseline's" in card
        assert "Suite: public_only" in card
        # Hand-computed in the fixtures: Quality 5 vs 4, weighted 4.30 vs 3.80.
        assert "quality" in card and "5.00" in card and "+1.00" in card
        assert "Weighted" in card and "4.30" in card and "3.80" in card and "+0.50" in card
        assert "Framing" in card

        await pilot.press("l")
        await pilot.pause()
        assert f"decisions/0001-{DAY}-{CHALLENGER}.yaml" in text(app, "#cmp-status")

        await open_tab(pilot, "decision-log")
        entries = app.query_one("#log-entries", OptionList)
        assert entries.option_count == 1
        assert "adopt" in str(entries.get_option_at_index(0).prompt)
        entries.focus()
        entries.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ComparisonScreen)
        assert "Verdict: adopt" in text(app, "#comparison-scorecard")
        assert "4.30" in text(app, "#comparison-scorecard")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ComparisonScreen)

    (entry,) = Workspace.open(ws).decision_log()
    assert entry["verdict"] == "adopt"
    assert entry["runs"] == {"challenger": challenger, "baseline": baseline}


async def test_logging_twice_needs_a_fresh_comparison(tmp_path: Path):
    ws, challenger, baseline = golden_runs("adopt-public-only", tmp_path)
    app = BenchApp(Workspace.open(ws, clock=FakeClock()))
    async with app.run_test() as pilot:
        await compare(app, pilot, challenger, baseline)
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert "already logged" in text(app, "#cmp-status")

    assert len(Workspace.open(ws).decision_log()) == 1


async def test_scorecard_shows_warnings_and_re_normalization_notes(tmp_path: Path):
    case = "adopt-public-only"
    ws = golden_workspace(case, tmp_path)
    results = tmp_path / "no-cost"
    shutil.copytree(GOLDEN / case / "results" / CHALLENGER, results)
    record = results / "T1.1-example.yaml"
    data = yaml.safe_load(record.read_text())
    data["cost_usd"] = None
    record.write_text(yaml.safe_dump(data))
    baseline = record_run(ws, GOLDEN / case / "results" / BASELINE, BASELINE, on="2026-11-01")
    challenger = record_run(ws, results, CHALLENGER)

    app = BenchApp(Workspace.open(ws, clock=FakeClock()))
    async with app.run_test() as pilot:
        await compare(app, pilot, challenger, baseline)
        card = text(app, "#cmp-scorecard")

    assert "the Runs are 12 days apart, more than a week" in card
    assert "latency only" in card


async def test_a_refused_comparison_is_shown_inline_and_cannot_be_logged(tmp_path: Path):
    ws, challenger, baseline = golden_runs("adopt-public-only", tmp_path)
    app = BenchApp(Workspace.open(ws, clock=FakeClock()))
    async with app.run_test() as pilot:
        await compare(app, pilot, baseline, challenger)  # swapped: not the Baseline
        assert "not the Baseline" in text(app, "#cmp-status")
        assert text(app, "#cmp-scorecard") == ""
        await pilot.press("l")
        await pilot.pause()
        assert "Compare two Runs" in text(app, "#cmp-status")

    assert Workspace.open(ws).decision_log() == ()


async def test_a_spot_check_re_test_shows_its_reason(tmp_path: Path):
    ws, challenger, baseline = golden_runs("retest-by-spot-check", tmp_path)
    args = ["--workspace", str(ws), "spot-check", challenger, "T1.1-example", "clarity", "3.5"]
    assert main(args) == 0
    app = BenchApp(Workspace.open(ws, clock=FakeClock()))
    async with app.run_test() as pilot:
        await compare(app, pilot, challenger, baseline)
        card = text(app, "#cmp-scorecard")

    assert card.splitlines()[0] == "Verdict: re-test (public-only Suite)"
    assert "Judge and human differ by 1.5 points on T1.1-example clarity" in card


async def test_the_decision_log_shows_the_scorecard_as_logged_and_recomputes_apart(
    tmp_path: Path,
):
    ws, challenger, baseline = golden_runs("adopt-public-only", tmp_path)
    workspace = Workspace.open(ws, clock=FakeClock())
    workspace.log_comparison(workspace.compare(challenger, baseline))
    # The Runs change after logging: a Spot-check now forces a re-test.
    args = ["--workspace", str(ws), "spot-check", challenger, "T1.1-example", "clarity", "1"]
    assert main(args) == 0

    app = BenchApp(Workspace.open(ws, clock=FakeClock()))
    async with app.run_test() as pilot:
        await open_tab(pilot, "decision-log")
        entries = app.query_one("#log-entries", OptionList)
        entries.focus()
        entries.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        logged = text(app, "#comparison-scorecard")
        before = text(app, "#comparison-recomputed")
        await pilot.press("r")
        await pilot.pause()
        recomputed = text(app, "#comparison-recomputed")

    assert logged.splitlines()[0].startswith("Verdict: adopt")
    assert "4.30" in logged and "+0.50" in logged
    assert before == ""
    assert "Recomputed from the Runs as they are now" in recomputed
    assert "Verdict: re-test" in recomputed


async def test_decision_log_lists_every_kind_of_entry_newest_first(tmp_path: Path):
    ws_dir = golden_workspace("reject-by-gate", tmp_path)
    results = tmp_path / "unscored"
    results.mkdir()
    data = yaml.safe_load(
        (GOLDEN / "adopt-public-only" / "results" / BASELINE / "T1.1-example.yaml").read_text()
    )
    del data["criterion_scores"]
    (results / "T1.1-example.yaml").write_text(yaml.safe_dump(data))
    run_id = record_run(ws_dir, results, BASELINE)
    ws = Workspace.open(ws_dir, clock=FakeClock(), judges=FakeJudges())
    ws.log_verdict(ws.gate_verdict(CHALLENGER))
    ws.judge_run(ws.resume_run(run_id), allow_uncertified=True)  # logs the override

    app = BenchApp(Workspace.open(ws_dir, clock=FakeClock()))
    async with app.run_test() as pilot:
        await open_tab(pilot, "decision-log")
        entries = app.query_one("#log-entries", OptionList)
        prompts = [str(entries.get_option_at_index(i).prompt) for i in range(2)]
        assert "uncertified" in prompts[0] and "reject" in prompts[1]

        entries.focus()
        entries.highlighted = 1
        await pilot.pause()
        assert "its Gate failed, so other@1 cannot be Run" in text(app, "#log-detail")
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ComparisonScreen)
        assert "needed no Runs" in text(app, "#log-status")
