"""Opt-in live smoke tests: one real call per provider Adapter, through the Workspace.

Skipped unless BENCH_LIVE=1, the provider's key is set and the model to call is named:

  BENCH_LIVE=1 ANTHROPIC_API_KEY=... BENCH_LIVE_ANTHROPIC_MODEL=<model id> uv run pytest -m live
  BENCH_LIVE=1 OPENAI_API_KEY=...    BENCH_LIVE_OPENAI_MODEL=<model id>    uv run pytest -m live

Each sends the example-sized Task once and costs a fraction of a cent.
"""

import os
from pathlib import Path

import pytest

from bench import Workspace
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
    ws = Workspace.open(workspace_dir)
    run = ws.start_run("live@1")

    pending = ws.run_automated_task(run, "T1.1")

    assert pending.raw.strip()
    assert pending.input_tokens > 0 and pending.output_tokens > 0
    assert pending.latency_seconds > 0
