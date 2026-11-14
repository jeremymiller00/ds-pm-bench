"""Screens that author Candidates: add one, or record its Gate review, through the Workspace."""

from __future__ import annotations

import datetime
from typing import Any, NamedTuple

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from bench.core.candidates import ADAPTER_KINDS, Candidate
from bench.core.errors import CandidateAuthoringError, ConfigError
from bench.core.kinds import Clearance
from bench.core.verdicts import Verdict
from bench.core.workspace import Workspace
from bench.tui.task_editor import EDITOR_CSS, FormError

AUTHORING_ERRORS = (FormError, ConfigError, CandidateAuthoringError)
NO_CLEARANCE = "none"


class NewCandidateScreen(ModalScreen[Candidate | None]):
    """Add a Candidate, ungated; dismisses with it, or None when cancelled."""

    DEFAULT_CSS = EDITOR_CSS.replace("#editor", "#new-candidate")
    BINDINGS = [
        Binding("ctrl+s", "save", "Create", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        first = self.workspace.baseline is None
        with VerticalScroll(id="new-candidate"):
            yield Static("New Candidate. ctrl+s creates it, escape cancels.", markup=False)
            yield Label("Tool")
            yield Input(id="cand-tool")
            yield Label("Version")
            yield Input(id="cand-version")
            yield Label("Adapter")
            yield Select([(a, a) for a in ADAPTER_KINDS], value=ADAPTER_KINDS[0],
                         allow_blank=False, id="cand-adapter")
            yield Label("Settings (YAML mapping, blank for none)")
            yield TextArea(id="cand-settings")
            yield Label("Environment variable names for secrets (comma-separated)")
            yield Input(id="cand-env-vars")
            yield Label("Cost per Task in USD (manual-assisted only, blank for none)")
            yield Input(id="cand-cost")
            yield Checkbox("Baseline" + (" (the first Candidate must be)" if first else ""),
                           value=first, id="cand-baseline")
            yield Static("", id="cand-error", markup=False)
            yield Button("Create", id="cand-save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cand-save":
            self.action_save()

    def action_save(self) -> None:
        try:
            created = self.workspace.create_candidate(
                tool=self.query_one("#cand-tool", Input).value.strip(),
                version=self.query_one("#cand-version", Input).value.strip(),
                adapter=str(self.query_one("#cand-adapter", Select).value),
                settings=parse_settings(self.query_one("#cand-settings", TextArea).text),
                env_vars=parse_names(self.query_one("#cand-env-vars", Input).value),
                cost_per_task_usd=parse_cost(self.query_one("#cand-cost", Input).value),
                baseline=self.query_one("#cand-baseline", Checkbox).value,
            )
        except AUTHORING_ERRORS as error:
            self.query_one("#cand-error", Static).update(str(error))
            return
        self.dismiss(created)

    def action_cancel(self) -> None:
        self.dismiss(None)


class GateRecorded(NamedTuple):
    """A recorded Gate review: the Candidate as it now stands and the Verdict it logged."""

    candidate: Candidate
    verdict: Verdict | None


class GateScreen(ModalScreen[GateRecorded | None]):
    """Fill one Candidate's Gate checklist; dismisses with what was recorded, or None."""

    DEFAULT_CSS = EDITOR_CSS.replace("#editor", "#gate")
    BINDINGS = [
        Binding("ctrl+s", "save", "Record", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, workspace: Workspace, candidate_id: str) -> None:
        super().__init__()
        self.workspace = workspace
        self.candidate_id = candidate_id

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="gate"):
            yield Static(
                f"Gate review of {self.candidate_id}. A review that fails the Gate logs "
                "a reject Verdict. ctrl+s records it, escape cancels.",
                markup=False,
            )
            yield Label("Reviewer")
            yield Input(id="gate-reviewer")
            yield Label("Date (YYYY-MM-DD)")
            yield Input(datetime.date.today().isoformat(), id="gate-date")
            yield Label("Retention")
            yield Input(id="gate-retention")
            yield Checkbox("SSO", id="gate-sso")
            yield Checkbox("Enterprise terms", id="gate-enterprise-terms")
            yield Label("Connector support")
            yield Input(id="gate-connector-support")
            yield Label("Result")
            yield Select([("Pass", "pass"), ("Fail", "fail")], value="pass",
                         allow_blank=False, id="gate-result")
            yield Label("Clearance (none for a failed Gate)")
            yield Select(
                [(str(c), str(c)) for c in Clearance] + [("none", NO_CLEARANCE)],
                value=NO_CLEARANCE, allow_blank=False, id="gate-clearance",
            )
            yield Static("", id="gate-error", markup=False)
            yield Button("Record", id="gate-save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "gate-save":
            self.action_save()

    def action_save(self) -> None:
        clearance = str(self.query_one("#gate-clearance", Select).value)
        try:
            verdict = self.workspace.record_gate(
                self.candidate_id,
                reviewer=self.query_one("#gate-reviewer", Input).value.strip(),
                on=parse_date(self.query_one("#gate-date", Input).value),
                checklist={
                    "retention": self.query_one("#gate-retention", Input).value.strip(),
                    "sso": self.query_one("#gate-sso", Checkbox).value,
                    "enterprise_terms": self.query_one("#gate-enterprise-terms", Checkbox).value,
                    "connector_support":
                        self.query_one("#gate-connector-support", Input).value.strip(),
                },
                passed=self.query_one("#gate-result", Select).value == "pass",
                clearance=None if clearance == NO_CLEARANCE else clearance,
            )
        except AUTHORING_ERRORS as error:
            self.query_one("#gate-error", Static).update(str(error))
            return
        self.dismiss(GateRecorded(self.workspace.candidate(self.candidate_id), verdict))

    def action_cancel(self) -> None:
        self.dismiss(None)


def parse_settings(text: str) -> dict[str, Any]:
    """The settings as written; blank is none. The Workspace validates the rest."""
    try:
        settings = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise FormError(f"Settings: not valid YAML: {error}") from None
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise FormError("Settings: must be a YAML mapping of name: value")
    return settings


def parse_names(text: str) -> list[str]:
    return [name.strip() for name in text.split(",") if name.strip()]


def parse_cost(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise FormError(f"Cost per Task: must be a number of USD, got {text!r}") from None


def parse_date(text: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError:
        raise FormError(f"Date: must be YYYY-MM-DD, got {text!r}") from None
