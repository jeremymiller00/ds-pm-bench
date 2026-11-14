"""A manual-assisted session: one person working one Task of a Run by hand.

begin (Workspace.begin_manual_task; Clearance is checked first and the clock starts)
-> submit_raw (latency = start to paste) -> edit, in the TUI's built-in editor with
pause/resume or in $EDITOR via edit_externally (all its open time counts) -> finish
(records raw, final, active seconds = start to save minus pauses, latency and cost).

Abandoning records nothing, so the Task can be begun again until it is finished.
"""

from __future__ import annotations

import dataclasses

from bench.core.adapters import Clock, Editor
from bench.core.candidates import Candidate
from bench.core.runs import OutputRecord, Run


class ManualSession:
    def __init__(
        self, run: Run, task_id: str, prompt: str, clock: Clock, editor: Editor
    ) -> None:
        self.run = run
        self.task_id = task_id
        self.prompt = prompt  # the Canonical Prompt then the input, to show the person
        self._clock = clock
        self._editor = editor
        self._started = clock.now()
        self._paused_seconds = 0.0
        self._paused_at: float | None = None
        self._over: str | None = None  # "finished" or "abandoned"
        self.raw: str | None = None
        self.latency_seconds: float | None = None
        self.draft: str | None = None  # the latest edit; the raw Output until edited

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
            raise ValueError(f"Task {self.task_id}: the raw Output is already submitted")
        self.latency_seconds = self._clock.now() - self._started
        self.raw = self.draft = raw

    def pause(self) -> None:
        """Pause the edit timer, such as for an interruption."""
        self._need_raw("pause")
        if self.paused:
            raise ValueError(f"Task {self.task_id}: the edit timer is already paused")
        self._paused_at = self._clock.now()

    def resume(self) -> None:
        self._check_open()
        if self._paused_at is None:
            raise ValueError(f"Task {self.task_id}: the edit timer is not paused")
        self._paused_seconds += self._clock.now() - self._paused_at
        self._paused_at = None

    def edit_externally(self) -> str:
        """Open the draft in the Editor Adapter ($EDITOR); returns the edited text,
        which becomes the draft. All the time it is open counts as active time."""
        self.check_editable()
        self.draft = self._editor.edit(self.draft or "")
        return self.draft

    def check_editable(self) -> None:
        """Raise ValueError if the draft cannot be opened in $EDITOR now: before the raw
        Output is submitted, or while paused. Lets a TUI refuse before suspending."""
        self._need_raw("edit")
        if self.paused:
            raise ValueError(f"Task {self.task_id}: resume the paused timer before editing")

    def finish(self, final: str | None = None) -> OutputRecord:
        """Save raw and final (the draft when final is omitted) with timings and cost.

        A pause still open at save time does not count as active time. The session is
        then over.
        """
        raw = self._need_raw("finish")
        record = self.run.record_output(
            self.task_id,
            raw=raw,
            final=self.draft if final is None else final,
            active_seconds=self.active_seconds,
            latency_seconds=self.latency_seconds,
            cost_usd=flat_cost_usd(self.run.candidate),
        )
        self._over = "finished"
        return record

    def abandon(self) -> None:
        """Drop the session, recording nothing; the Task can be begun again."""
        self._check_open()
        self._over = "abandoned"

    def _check_open(self) -> None:
        if self._over is not None:
            raise ValueError(f"Task {self.task_id}: this session is {self._over}")

    def _need_raw(self, action: str) -> str:
        self._check_open()
        if self.raw is None:
            raise ValueError(f"Task {self.task_id}: submit the raw Output before you {action}")
        return self.raw


def flat_cost_usd(candidate: Candidate) -> float | None:
    """The Candidate's flat per-Task cost in USD, or None when it has none.

    Read generically, as the one Candidate field whose name mentions cost, so this
    does not depend on how Candidate authoring names it.
    """
    costs = [
        getattr(candidate, f.name)
        for f in dataclasses.fields(candidate)
        if "cost" in f.name
    ]
    if len(costs) > 1:
        raise ValueError(f"Candidate {candidate.id} has more than one cost field")
    return None if not costs or costs[0] is None else float(costs[0])
