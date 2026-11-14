"""An Attempt: one pass at a Task within a Run, ending when the final is saved.

Workspace.begin_attempt begins one after checking the Gate and Clearance, and the clock
starts. For a manual-assisted Candidate it starts by showing the Canonical Prompt:
submit_raw (latency = start to paste) -> edit, in the TUI's built-in editor with
pause/resume or in $EDITOR via edit_externally (all its open time counts) -> finish
(records raw, final, active seconds = start to save minus pauses, latency and cost).

For an automated Candidate it starts with the raw Output already returned (the Returned
Output): active seconds are the editing time, and latency, cost, tokens, model and tries
are the provider call's. Finishing removes the Returned Output.

An Attempt can be abandoned, which records nothing, so the Task can be begun again until
it is finished.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from bench.core.adapters import Clock, Editor
from bench.core.automated import ReturnedOutput, discard_returned
from bench.core.errors import AttemptError
from bench.core.runs import OutputRecord, Run


class AttemptState(StrEnum):
    OPEN = "open"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class Attempt:
    def __init__(
        self,
        run: Run,
        task_id: str,
        prompt: str,
        clock: Clock,
        editor: Editor,
        check_gate_and_clearance: Callable[[], None],
        returned: ReturnedOutput | None = None,
    ) -> None:
        self.run = run
        self.task_id = task_id
        self.prompt = prompt  # the Canonical Prompt then the input, to show the person
        self._clock = clock
        self._editor = editor
        # Re-checks the Candidate's latest Gate and Clearance (ADR-0003): raises if a
        # review since begin has failed or narrowed it.
        self._check_gate_and_clearance = check_gate_and_clearance
        self._started = clock.now()
        self._paused_seconds = 0.0
        self._paused_at: float | None = None
        self.state = AttemptState.OPEN
        self.raw: str | None = None
        self.latency_seconds: float | None = None
        self.draft: str | None = None  # the latest edit; the raw Output until edited
        # An automated Task's Returned Output: the Attempt starts with its raw Output in.
        self._returned = returned
        if returned is not None:
            self.raw = self.draft = returned.raw
            self.latency_seconds = returned.latency_seconds

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    @property
    def active_seconds(self) -> float:
        """Active time so far: since begin, minus pauses (a running timer for the TUI)."""
        now = self._paused_at if self._paused_at is not None else self._clock.now()
        return now - self._started - self._paused_seconds

    def submit_raw(self, raw: str) -> None:
        """The Candidate's raw Output, pasted back; stops the latency clock."""
        self._check_open()
        if self.raw is not None:
            raise AttemptError(f"Task {self.task_id}: the raw Output is already submitted")
        self.latency_seconds = self._clock.now() - self._started
        self.raw = self.draft = raw

    def pause(self) -> None:
        """Pause the edit timer, such as for an interruption."""
        self._need_raw("pause")
        if self.paused:
            raise AttemptError(f"Task {self.task_id}: the edit timer is already paused")
        self._paused_at = self._clock.now()

    def resume(self) -> None:
        self._check_open()
        if self._paused_at is None:
            raise AttemptError(f"Task {self.task_id}: the edit timer is not paused")
        self._paused_seconds += self._clock.now() - self._paused_at
        self._paused_at = None

    def edit_externally(self) -> str:
        """Open the draft in the Editor Adapter ($EDITOR); returns the edited text,
        which becomes the draft. All the time it is open counts as active time."""
        self.check_editable()
        self.draft = self._editor.edit(self.draft or "")
        return self.draft

    def check_editable(self) -> None:
        """Raise AttemptError if the draft cannot be opened in $EDITOR now: before the raw
        Output is submitted, or while paused. Lets a TUI refuse before suspending."""
        self._need_raw("edit")
        if self.paused:
            raise AttemptError(f"Task {self.task_id}: resume the paused timer before editing")

    def finish(self, final: str | None = None) -> OutputRecord:
        """Save raw and final (the draft when final is omitted) with timings and cost,
        and for an automated Candidate the provider call's model, tokens and tries.

        A pause still open at save time does not count as active time. The Attempt is
        then over. A Gate failed or narrowed since begin refuses the save
        (GateNotPassed, ClearanceError) and records nothing.
        """
        raw = self._need_raw("finish")
        self._check_gate_and_clearance()
        if self.task_id not in self.run.pending:
            raise AttemptError(f"Task {self.task_id}: its Output is already recorded")
        record = self.run.record_output(
            self.task_id,
            raw=raw,
            final=self.draft if final is None else final,
            active_seconds=self.active_seconds,
            latency_seconds=self.latency_seconds,
            cost_usd=self._cost_usd,
            call=None if self._returned is None else self._returned.call,
        )
        if self._returned is not None:
            discard_returned(self.run, self.task_id)
        self.state = AttemptState.FINISHED
        return record

    @property
    def _cost_usd(self) -> float | None:
        if self._returned is not None:
            return self._returned.cost_usd
        return self.run.candidate.task_cost_usd

    def abandon(self) -> None:
        """Abandon the Attempt, recording nothing; the Task can be begun again."""
        self._check_open()
        self.state = AttemptState.ABANDONED

    def _check_open(self) -> None:
        if self.state != AttemptState.OPEN:
            raise AttemptError(f"Task {self.task_id}: this Attempt is {self.state}")

    def _need_raw(self, action: str) -> str:
        self._check_open()
        if self.raw is None:
            raise AttemptError(f"Task {self.task_id}: submit the raw Output before you {action}")
        return self.raw
