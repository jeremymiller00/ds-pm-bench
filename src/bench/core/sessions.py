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
        self.raw: str | None = None
        self.latency_seconds: float | None = None

    def submit_raw(self, raw: str) -> None:
        """The Candidate's raw Output, pasted back; stops the latency clock."""
        self.latency_seconds = self._clock.now() - self._started
        self.raw = raw

    def finish(self, final: str) -> OutputRecord:
        """Save raw and final with timings and cost; the session is then over."""
        active = self._clock.now() - self._started
        return self.run.record_output(
            self.task_id,
            raw=self.raw,
            final=final,
            active_seconds=active,
            latency_seconds=self.latency_seconds,
            cost_usd=None,
        )
