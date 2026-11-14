"""The composition root: opens a Workspace wired to the real provider SDKs.

The core never imports a provider SDK; the CLI and TUI open their Workspace here, so
an automated Candidate gets its SDK Adapter and each judge model its SDK JudgeModel,
with API keys read from the environment or the root's .env (ADR-0005). Tests open a
Workspace directly with fakes instead.
"""

from __future__ import annotations

from pathlib import Path

from bench.core.adapters import Clock, Editor
from bench.core.workspace import Workspace
from bench.providers import candidate_adapter, judge_model


def open_workspace(
    path: str | Path, *, clock: Clock | None = None, editor: Editor | None = None
) -> Workspace:
    """Workspace.open with the provider SDK Adapters and JudgeModels."""
    root = Path(path).resolve()
    return Workspace.open(
        root,
        clock=clock,
        editor=editor,
        candidate_adapters=lambda c: candidate_adapter(c, root),
        judges=lambda spec: judge_model(spec, root),
    )
