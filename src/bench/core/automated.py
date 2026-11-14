"""One Task of an automated Run: send, retry once on a tool error, keep the Returned Output.

Workspace.run_automated_task sends the Canonical Prompt and input word for word through
the Candidate's Adapter, timing each try with the Clock. A tool error is retried exactly
once (story 22); a second error raises and stores nothing. The raw Output is then stored
as a Returned Output, which an Attempt (one that starts with the raw Output already
returned) edits into the Task's recorded Output:

  runs/<id>/returned/<task>.yaml  numbers only: latency, tokens, model, cost, tries
  <content folder>/raw/<task>.md  the raw Output (the private root for a Private Task)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from bench.core.adapters import CandidateAdapter, CandidateReply, Clock
from bench.core.candidates import model_for
from bench.core.errors import CandidateToolError
from bench.core.prices import PRICES_FILE, PriceTable
from bench.core.runs import ProviderCall, Run

RETURNED_FOLDER = "returned"
TRIES = 2  # the first try and exactly one automatic retry


@dataclass(frozen=True)
class ReturnedOutput:
    """The raw Output an automated Candidate sent back for a Task, waiting for an
    Attempt to edit it into the final."""

    task_id: str
    raw: str
    latency_seconds: float  # the successful call only; a failed try is not counted
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float | None  # None when the price table has no price for the model
    cost_note: str | None  # why cost_usd is None
    tries: int  # 1, or 2 after the one retry

    @property
    def call(self) -> ProviderCall:
        """What the Task Result keeps of the provider call once the Output is recorded."""
        return ProviderCall(
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            tries=self.tries,
            cost_note=self.cost_note,
        )


def cost_of(prices: PriceTable, model: str, reply: CandidateReply) -> tuple[float | None,
                                                                          str | None]:
    """(cost in USD, None), or (None, why) when the model has no price."""
    price = prices.models.get(model)
    if price is None:
        return None, f"no price for model {model} in {PRICES_FILE}"
    cost = (reply.input_tokens * price.input_per_mtok
            + reply.output_tokens * price.output_per_mtok) / 1_000_000
    return cost, None


def send_with_one_retry(
    adapter: CandidateAdapter, text: str, clock: Clock
) -> tuple[CandidateReply, float, int]:
    """The reply, the seconds the successful call took, and how many tries it took;
    the second CandidateToolError propagates."""
    for try_ in range(1, TRIES + 1):
        started = clock.now()
        try:
            reply = adapter.send(text)
        except CandidateToolError:
            if try_ == TRIES:
                raise
            continue
        return reply, clock.now() - started, try_
    raise AssertionError("unreachable")


def run_task(
    run: Run,
    task_id: str,
    prompt: str,
    adapter: CandidateAdapter,
    clock: Clock,
    prices: PriceTable,
) -> ReturnedOutput:
    """Send the prompt, then store and return the Returned Output."""
    model = model_for(run.candidate)
    try:
        reply, latency, tries = send_with_one_retry(adapter, prompt, clock)
    except CandidateToolError as exc:
        raise CandidateToolError(
            f"Run {run.id}: Task {task_id}: the Candidate's tool errored twice, "
            f"so nothing was stored: {exc}"
        ) from exc
    cost, note = cost_of(prices, model, reply)
    returned = ReturnedOutput(
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
    _save(run, returned)
    return returned


def load_returned(run: Run, task_id: str) -> ReturnedOutput | None:
    """The Task's Returned Output, or None if it has none."""
    task = run.task(task_id)
    path = _numbers_file(run, task_id)
    if not path.is_file():
        return None
    numbers = yaml.safe_load(path.read_text())
    numbers.pop("visibility")
    raw = _raw_file(run, task_id).read_text()
    return ReturnedOutput(task_id=task.id, raw=raw, **numbers)


def discard_returned(run: Run, task_id: str) -> None:
    """Remove a Task's Returned Output numbers once an Attempt records the Output.
    The raw file stays: it is where the recorded raw Output lives too."""
    _numbers_file(run, task_id).unlink(missing_ok=True)


def _save(run: Run, returned: ReturnedOutput) -> None:
    task = run.task(returned.task_id)
    raw = _raw_file(run, task.id)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(returned.raw)
    numbers = {k: v for k, v in asdict(returned).items() if k not in ("task_id", "raw")}
    numbers["visibility"] = str(task.visibility)
    path = _numbers_file(run, task.id)
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(numbers, sort_keys=False))


def _numbers_file(run: Run, task_id: str) -> Path:
    return run.folder / RETURNED_FOLDER / f"{task_id}.yaml"


def _raw_file(run: Run, task_id: str) -> Path:
    return run.content_folder(run.task(task_id)) / "raw" / f"{task_id}.md"
