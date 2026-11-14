"""The Settings tab: edit settings, prices and the Judge Setup, saving through the Workspace.

The Workspace validates every edit; a refused one is shown next to the field it names
and nothing is written. The form only reads its fields and sends what changed.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Select, Static

from bench.core.errors import ConfigError
from bench.core.settings import BANDED_MEASURES, DIMENSIONS, Settings
from bench.core.workspace import Workspace
from bench.tui.task_editor import FormError

SETTINGS_CSS = """
SettingsPane { height: 1fr; }
SettingsPane .settings-section { height: auto; border: round $accent; padding: 0 1; }
SettingsPane .form-error { color: $error; }
SettingsPane .warning { color: $warning; }
"""

MARGINS = ("regression_margin", "retest_margin", "spot_check_rate")
THRESHOLDS = ("within_one_point", "weighted_kappa")


class FieldError(FormError):
    """A form field that could not be read, naming the field as a ConfigError does."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


def fmt(number: float) -> str:
    return f"{number:g}"


def parse_number(text: str, field: str) -> int | float:
    text = text.strip()
    try:
        number = float(text)
    except ValueError:
        raise FieldError(field, f"must be a number, got {text!r}") from None
    return int(number) if number.is_integer() and "." not in text else number


def settings_fields(settings: Settings) -> dict[str, str]:
    """The settings form's Input ids -> the text they start with."""
    fields = {"set-tracks": ", ".join(settings.tracks)}
    fields |= {f"set-weight-{d}": fmt(settings.weights[d]) for d in DIMENSIONS}
    fields |= {f"set-{m}": fmt(getattr(settings, m)) for m in MARGINS}
    fields |= {f"set-calibration-{t}": fmt(getattr(settings.calibration, t))
               for t in THRESHOLDS}
    fields |= {f"set-band-{m}": ", ".join(fmt(t) for t in band.thresholds)
               for m, band in settings.normalization.items()}
    return fields


def weight_sum(texts: list[str]) -> str:
    """The live sum of the weights as typed, for display only."""
    try:
        total = sum(parse_number(t, "weights") for t in texts)
    except FieldError:
        return "Sum: ? (every weight must be a number)"
    return f"Sum: {fmt(total)}" + ("" if total == 100 else " (must be 100)")


def error_slot(field: str) -> str:
    """Which inline error a field's error shows in: its top-level setting, or its band."""
    head = field.split("[")[0].split(".")
    if head[0] == "normalization" and len(head) > 1:
        return f"err-band-{head[1]}"
    return f"err-{head[0]}"


class SettingsPane(VerticalScroll):
    """Every editable setting, the price table and the Judge Setup, each saved apart."""

    DEFAULT_CSS = SETTINGS_CSS
    BINDINGS = [Binding("ctrl+s", "save", "Save section")]

    def __init__(self, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-form", classes="settings-section"):
            yield from self._compose_settings()

    def _compose_settings(self) -> ComposeResult:
        settings = self.workspace.settings
        start = settings_fields(settings)
        yield Static("Settings. ctrl+s or Save writes config/settings.yaml.", markup=False)
        yield Label("Tracks (comma-separated, in catalog order)")
        yield Input(start["set-tracks"], id="set-tracks")
        yield Static("", id="err-tracks", classes="form-error", markup=False)
        yield Label("Weights (percent, must sum to 100)")
        for d in DIMENSIONS:
            yield Label(d)
            yield Input(start[f"set-weight-{d}"], id=f"set-weight-{d}")
        yield Static(weight_sum([start[f"set-weight-{d}"] for d in DIMENSIONS]),
                     id="weight-sum", markup=False)
        yield Static("", id="err-weights", classes="form-error", markup=False)
        for m in MARGINS:
            yield Label(m)
            yield Input(start[f"set-{m}"], id=f"set-{m}")
            yield Static("", id=f"err-{m}", classes="form-error", markup=False)
        yield Label("Calibration thresholds")
        for t in THRESHOLDS:
            yield Label(t)
            yield Input(start[f"set-calibration-{t}"], id=f"set-calibration-{t}")
        yield Static("", id="err-calibration", classes="form-error", markup=False)
        yield Label("Normalization bands (four ascending thresholds each)")
        for m in BANDED_MEASURES:
            band = settings.normalization[m]
            yield Label(m)
            yield Select([("better: lower", "lower"), ("better: higher", "higher")],
                         value=band.better, allow_blank=False, id=f"set-better-{m}")
            yield Input(start[f"set-band-{m}"], id=f"set-band-{m}")
            yield Static("", id=f"err-band-{m}", classes="form-error", markup=False)
        yield Static("", id="settings-status", markup=False)
        yield Button("Save settings", id="settings-save", variant="primary")

    # Live weight sum: display only; the Workspace decides whether it is valid.

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("set-weight-"):
            texts = [self._value(f"set-weight-{d}") for d in DIMENSIONS]
            self.query_one("#weight-sum", Static).update(weight_sum(texts))

    # Saving

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-save":
            self.save_settings()

    def action_save(self) -> None:
        focused = self.app.focused
        section = next((w for w in (focused.ancestors if focused else ())
                        if isinstance(w, Widget) and w.has_class("settings-section")), None)
        if section is not None and section.id == "settings-form":
            self.save_settings()

    def _clear_errors(self, section: str) -> None:
        for error in self.query_one(f"#{section}").query(".form-error"):
            error.update("")

    def _show_error(self, error: FormError | ConfigError, fallback: str) -> None:
        field = getattr(error, "field", "")
        try:
            slot = self.query_one(f"#{error_slot(field)}", Static)
        except NoMatches:  # a field without its own slot
            slot = self.query_one(f"#{fallback}", Static)
        slot.update(str(error))

    def save_settings(self) -> None:
        self._clear_errors("settings-form")
        status = self.query_one("#settings-status", Static)
        try:
            changes = self._settings_changes()
            self.workspace.update_settings(**changes)
        except (FormError, ConfigError) as error:
            status.update("Not saved: fix the field marked below.")
            self._show_error(error, "settings-status")
            return
        status.update("Saved config/settings.yaml." if changes else "Nothing changed.")

    def _value(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value

    def _settings_changes(self) -> dict[str, object]:
        """What the form changes, read into values for update_settings."""
        settings = self.workspace.settings
        start = settings_fields(settings)
        changed = {k for k, v in start.items() if self._value(k).strip() != v}
        changes: dict[str, object] = {}
        if "set-tracks" in changed:
            changes["tracks"] = [t.strip() for t in self._value("set-tracks").split(",")
                                 if t.strip()]
        if any(f"set-weight-{d}" in changed for d in DIMENSIONS):
            changes["weights"] = {
                d: parse_number(self._value(f"set-weight-{d}"), f"weights.{d}")
                for d in DIMENSIONS
            }
        for m in MARGINS:
            if f"set-{m}" in changed:
                changes[m] = parse_number(self._value(f"set-{m}"), m)
        calibration = {t: parse_number(self._value(f"set-calibration-{t}"), f"calibration.{t}")
                       for t in THRESHOLDS if f"set-calibration-{t}" in changed}
        if calibration:
            changes["calibration"] = calibration
        bands = {}
        for m in BANDED_MEASURES:
            better = self.query_one(f"#set-better-{m}", Select).value
            if f"set-band-{m}" in changed or better != settings.normalization[m].better:
                field = f"normalization.{m}.thresholds"
                bands[m] = {"better": better, "thresholds": [
                    parse_number(t, field) for t in self._value(f"set-band-{m}").split(",")
                ]}
        if bands:
            changes["normalization"] = bands
        return changes
