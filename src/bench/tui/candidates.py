"""The Candidates tab: each current Candidate with its Gate, and keys to add one,
mark the Baseline and record a Gate review."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from bench.core.candidates import Candidate
from bench.core.errors import CandidateAuthoringError, GateNotPassed
from bench.core.workspace import Workspace
from bench.tui.candidate_editor import GateRecorded, GateScreen, NewCandidateScreen

COLUMNS = ("Candidate", "Version", "Adapter", "Baseline", "Gate", "Clearance")


class CandidateBrowser(Vertical):
    DEFAULT_CSS = """
    CandidateBrowser #candidate-table { height: 1fr; }
    CandidateBrowser #candidate-status { height: auto; padding: 0 1; }
    """
    BINDINGS = [
        Binding("n", "new_candidate", "New Candidate"),
        Binding("b", "mark_baseline", "Mark Baseline"),
        Binding("g", "gate", "Gate review"),
    ]

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        table: DataTable[str] = DataTable(id="candidate-table", cursor_type="row")
        table.add_columns(*COLUMNS)
        self._fill(table)
        yield table
        yield Static(
            "n adds a Candidate, b marks the Baseline, g records its Gate review.",
            id="candidate-status",
            markup=False,
        )

    def _fill(self, table: DataTable[str]) -> None:
        table.clear()
        for c in self.workspace.current_candidates:
            table.add_row(*candidate_row(c), key=c.id)

    def refresh_candidates(self, select: str | None = None) -> None:
        table = self.query_one("#candidate-table", DataTable)
        self._fill(table)
        if select is not None:
            table.move_cursor(row=table.get_row_index(select))

    def _highlighted(self) -> str | None:
        table = self.query_one("#candidate-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_new_candidate(self) -> None:
        def created(c: Candidate | None) -> None:
            if c is not None:
                self.refresh_candidates(select=c.id)
                baseline = ", the Baseline" if c.baseline else ""
                self._status(f"{c.id} created ({c.adapter}{baseline}); it needs a Gate review.")

        self.app.push_screen(NewCandidateScreen(self.workspace), created)

    def action_mark_baseline(self) -> None:
        candidate_id = self._highlighted()
        if candidate_id is None:
            self._status("Highlight a Candidate to mark it the Baseline.")
            return
        try:
            marked = self.workspace.mark_baseline(candidate_id)
        except (CandidateAuthoringError, GateNotPassed) as error:
            self._status(str(error))
            return
        self.refresh_candidates(select=marked.id)
        self._status(f"{marked.id} is now the Baseline.")

    def action_gate(self) -> None:
        candidate_id = self._highlighted()
        if candidate_id is None:
            self._status("Highlight a Candidate to record its Gate review.")
            return

        def recorded(result: GateRecorded | None) -> None:
            if result is None:
                return
            c, verdict = result
            self.refresh_candidates(select=c.id)
            if c.gate_passed:
                self._status(f"{c.id} passed its Gate: Clearance {c.clearance}.")
                return
            message = f"{c.id} failed its Gate: no Clearance."
            if verdict is not None and c.gate is not None:
                message += (
                    f"\nDecision Log: {verdict.outcome} for {verdict.candidate_id} "
                    f"on {c.gate.date.isoformat()}. " + " ".join(verdict.rationale)
                )
            self._status(message)

        self.app.push_screen(GateScreen(self.workspace, candidate_id), recorded)

    def _status(self, message: str) -> None:
        self.query_one("#candidate-status", Static).update(message)


def candidate_row(c: Candidate) -> tuple[str, ...]:
    gate = "not reviewed" if c.gate is None else c.gate.result
    clearance = "none" if c.clearance is None else str(c.clearance)
    return (c.id, c.version, c.adapter, "Baseline" if c.baseline else "", gate, clearance)
