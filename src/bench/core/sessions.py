"""A manual-assisted session: one person working one Task of a Run by hand.

begin (Workspace.begin_manual_task; Clearance is checked first and the clock starts)
-> submit_raw (latency = start to paste) -> edit, in the TUI's built-in editor with
pause/resume or in $EDITOR via edit_externally (all its open time counts) -> finish
(records raw, final, active seconds = start to save minus pauses, latency and cost).

Abandoning records nothing, so the Task can be begun again until it is finished.
"""

from __future__ import annotations

from bench.core.adapters import Clock, Editor
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
        self.raw: str | None = None
        self.latency_seconds: float | None = None

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
        if self.raw is not None:
            raise ValueError(f"Task {self.task_id}: the raw Output is already submitted")
        self.latency_seconds = self._clock.now() - self._started
        self.raw = raw

    def pause(self) -> None:
        """Pause the edit timer, such as for an interruption."""
        self._need_raw("pause")
        if self.paused:
            raise ValueError(f"Task {self.task_id}: the edit timer is already paused")
        self._paused_at = self._clock.now()

    def resume(self) -> None:
        if self._paused_at is None:
            raise ValueError(f"Task {self.task_id}: the edit timer is not paused")
        self._paused_seconds += self._clock.now() - self._paused_at
        self._paused_at = None

    def finish(self, final: str) -> OutputRecord:
        """Save raw and final with timings and cost; the session is then over.

        A pause still open at save time does not count as active time.
        """
        raw = self._need_raw("finish")
        return self.run.record_output(
            self.task_id,
            raw=raw,
            final=final,
            active_seconds=self.active_seconds,
            latency_seconds=self.latency_seconds,
            cost_usd=None,
        )

    def _need_raw(self, action: str) -> str:
        if self.raw is None:
            raise ValueError(f"Task {self.task_id}: submit the raw Output before you {action}")
        return self.raw
