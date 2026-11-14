"""One Task of an automated Run: send, retry once on a tool error, store pending the edit.

Workspace.run_automated_task sends the Canonical Prompt and input word for word through
the Candidate's Adapter, timing it with the Clock. A tool error is retried exactly once
(story 22); a second error raises and stores nothing. The raw Output is then stored as a
Pending Output, which the edit pass (an Attempt that starts with the raw Output already
returned) turns into the Task's recorded Output:

  runs/<id>/pending/<task>.yaml   numbers only: latency, tokens, model, cost, tries
  <content folder>/raw/<task>.md  the raw Output (the private root for a Private Task)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from bench.core.adapters import CandidateAdapter, CandidateReply, Clock
from bench.core.candidates import Candidate
from bench.core.errors import CandidateToolError
from bench.core.prices import PRICES_FILE, PriceTable
from bench.core.runs import Run

PENDING_FOLDER = "pending"
TRIES = 2  # the first try and exactly one automatic retry


@dataclass(frozen=True)
class PendingOutput:
    """An automated Candidate's raw Output for a Task, awaiting its edit pass."""

    task_id: str
    raw: str
    latency_seconds: float  # from the first send to the reply, a failed try included
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float | None  # None when the price table has no price for the model
    cost_note: str | None  # why cost_usd is None
    tries: int  # 1, or 2 after the one retry


def model_for(candidate: Candidate) -> str:
    """The model id an automated Candidate calls: settings.model, else its version."""
    return str(candidate.settings.get("model") or candidate.version)


def cost_of(prices: PriceTable, model: str, reply: CandidateReply) -> tuple[float | None,
                                                                          str | None]:
    """(cost in USD, None), or (None, why) when the model has no price."""
    price = prices.models.get(model)
    if price is None:
        return None, f"no price for model {model} in {PRICES_FILE}"
    cost = (reply.input_tokens * price.input_per_mtok
            + reply.output_tokens * price.output_per_mtok) / 1_000_000
    return cost, None


def send_with_one_retry(adapter: CandidateAdapter, text: str) -> tuple[CandidateReply, int]:
    """The reply and how many tries it took; the second CandidateToolError propagates."""
    for attempt in range(1, TRIES + 1):
        try:
            return adapter.send(text), attempt
        except CandidateToolError:
            if attempt == TRIES:
                raise
    raise AssertionError("unreachable")


def run_task(
    run: Run,
    task_id: str,
    prompt: str,
    adapter: CandidateAdapter,
    clock: Clock,
    prices: PriceTable,
) -> PendingOutput:
    """Send the prompt, then store and return the Pending Output."""
    model = model_for(run.candidate)
    started = clock.now()
    try:
        reply, tries = send_with_one_retry(adapter, prompt)
    except CandidateToolError as exc:
        raise CandidateToolError(
            f"Run {run.id}: Task {task_id}: the Candidate's tool errored twice, "
            f"so nothing was stored: {exc}"
        ) from exc
    latency = clock.now() - started
    cost, note = cost_of(prices, model, reply)
    pending = PendingOutput(
        task_id=task_id,
        raw=reply.text,
        latency_seconds=latency,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        model=model,
        cost_usd=cost,
        cost_note=note,
        tries=tries,
    )
    _save(run, pending)
    return pending


def load_pending(run: Run, task_id: str) -> PendingOutput | None:
    """The Task's Pending Output, or None if it has none."""
    task = run.task(task_id)
    path = _numbers_file(run, task_id)
    if not path.is_file():
        return None
    numbers = yaml.safe_load(path.read_text())
    numbers.pop("visibility")
    raw = _raw_file(run, task_id).read_text()
    return PendingOutput(task_id=task.id, raw=raw, **numbers)


def _save(run: Run, pending: PendingOutput) -> None:
    task = run.task(pending.task_id)
    raw = _raw_file(run, task.id)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(pending.raw)
    numbers = {k: v for k, v in asdict(pending).items() if k not in ("task_id", "raw")}
    numbers["visibility"] = str(task.visibility)
    path = _numbers_file(run, task.id)
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(numbers, sort_keys=False))


def _numbers_file(run: Run, task_id: str) -> Path:
    return run.folder / PENDING_FOLDER / f"{task_id}.yaml"


def _raw_file(run: Run, task_id: str) -> Path:
    return run.content_folder(run.task(task_id)) / "raw" / f"{task_id}.md"


AdapterFactory = Callable[[Candidate], CandidateAdapter]
