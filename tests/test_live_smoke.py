"""Opt-in live smoke tests: one real call per provider Adapter, through the Workspace.

Skipped unless BENCH_LIVE=1, the provider's key is set and the model to call is named:

  BENCH_LIVE=1 ANTHROPIC_API_KEY=... BENCH_LIVE_ANTHROPIC_MODEL=<model id> uv run pytest -m live
  BENCH_LIVE=1 OPENAI_API_KEY=...    BENCH_LIVE_OPENAI_MODEL=<model id>    uv run pytest -m live

Each sends the example-sized Task once and costs a fraction of a cent.

The default Judge Setup (config/judge.yaml) judges the examples/ Task once with:

  BENCH_LIVE=1 OPENAI_API_KEY=... uv run pytest -m live
"""

import os
import shutil
from pathlib import Path

import pytest

from bench.wiring import open_workspace
from conftest import REPO_ROOT
from test_workspace_candidates import candidate, write_candidate
from test_workspace_tasks import write_task

PROVIDERS = [
    ("anthropic", "ANTHROPIC_API_KEY", "BENCH_LIVE_ANTHROPIC_MODEL"),
    ("openai", "OPENAI_API_KEY", "BENCH_LIVE_OPENAI_MODEL"),
]


@pytest.mark.live
@pytest.mark.parametrize(("provider", "key_var", "model_var"), PROVIDERS)
def test_a_live_call_returns_an_output_with_tokens(
    workspace_dir: Path, provider: str, key_var: str, model_var: str
):
    if os.environ.get("BENCH_LIVE") != "1":
        pytest.skip("live smoke tests run only with BENCH_LIVE=1")
    if not os.environ.get(key_var) or not os.environ.get(model_var):
        pytest.skip(f"set {key_var} and {model_var} to smoke-test {provider}")
    write_task(workspace_dir, "T1.1")
    data = candidate(
        tool=provider,
        version=os.environ[model_var],
        env_vars=[key_var],
        settings={"provider": provider, "max_tokens": 300},
        baseline=True,
    )
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "live@1", data)
    ws = open_workspace(workspace_dir)
    run = ws.start_run("live@1")

    pending = ws.run_automated_task(run, "T1.1")

    assert pending.raw.strip()
    assert pending.input_tokens > 0 and pending.output_tokens > 0
    assert pending.latency_seconds > 0


@pytest.mark.live
def test_the_default_judge_setup_judges_the_example_task_once(workspace_dir: Path):
    if os.environ.get("BENCH_LIVE") != "1":
        pytest.skip("live smoke tests run only with BENCH_LIVE=1")
    shutil.copytree(REPO_ROOT / "examples", workspace_dir / "examples")
    data = candidate(adapter="manual_assisted", env_vars=[], baseline=True)
    data["gate"]["clearance"] = "public_only"
    write_candidate(workspace_dir, "claude@claude-opus-5-5", data)
    ws = open_workspace(workspace_dir)  # the real provider judge models
    [spec] = ws.judge_setup.models
    if not os.environ.get(spec.env_var):
        pytest.skip(f"set {spec.env_var} to smoke-test Judge {spec.label}")
    task = ws.task("T1.1-example")
    run = ws.start_run("claude@claude-opus-5-5")
    gold = task.gold_answer_path.read_text()
    run.record_output(task.id, raw=gold, final=gold, active_seconds=60,
                      latency_seconds=1, cost_usd=None)

    [judging] = ws.judge_run(run, allow_uncertified=True)

    [scores] = judging.judges.values()
    assert set(scores) == {c.id for c in task.rubric.criteria}
    assert all(1 <= j.score <= 5 and j.reason for j in scores.values())
