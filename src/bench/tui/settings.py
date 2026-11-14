"""The Settings tab: edit settings, prices and the Judge Setup, saving through the Workspace.

The Workspace validates every edit; a refused one is shown next to the field it names
and nothing is written. The form only reads its fields and sends what changed.
"""

from __future__ import annotations

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Label, Select, Static, TextArea

from bench.core.errors import ConfigError
from bench.core.judging import JUDGE_FILE, JudgeSetup, family_of
from bench.core.kinds import Clearance, JudgeMode
from bench.core.settings import BANDED_MEASURES, DIMENSIONS, Settings
from bench.core.workspace import Workspace
from bench.tui.task_editor import FormError

SETTINGS_CSS = """
SettingsPane { height: 1fr; }
SettingsPane .settings-section { height: auto; border: round $accent; padding: 0 1; }
SettingsPane TextArea { height: 6; }
SettingsPane .form-error { color: $error; }
SettingsPane .warning { color: $warning; }
"""

MARGINS = ("regression_margin", "retest_margin", "spot_check_rate")
THRESHOLDS = ("within_one_point", "weighted_kappa", "min_items")


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


def models_yaml(setup: JudgeSetup | None) -> str:
    """The judge models as the Judge Setup file lists them; family only when not the default."""
    if setup is None:
        return ""
    models = []
    for spec in setup.models:
        entry = {"provider": str(spec.provider), "model": spec.model}
        if spec.family != family_of(spec.provider):
            entry["family"] = list(spec.family)
        if spec.env_var:
            entry["env_var"] = spec.env_var
        models.append(entry)
    return yaml.safe_dump(models, sort_keys=False)


def parse_models(text: str) -> list[dict]:
    try:
        models = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise FieldError("models", f"not valid YAML: {error}") from None
    if not isinstance(models, list):
        raise FieldError("models", "must be a YAML list of {provider, model}")
    return models


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
        with Vertical(id="prices-form", classes="settings-section"):
            yield from self._compose_prices()
        with Vertical(id="judge-form", classes="settings-section"):
            yield from self._compose_judge()

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

    def _compose_prices(self) -> ComposeResult:
        yield Static("Prices: USD per million tokens, keyed by the exact model string a "
                     "Candidate uses. Select a row to fill the form.", markup=False)
        table = DataTable(id="price-table", cursor_type="row")
        table.add_columns("Model", "Input / Mtok", "Output / Mtok")
        yield table
        yield Label("Model")
        yield Input(id="price-model")
        yield Label("Input per Mtok (USD)")
        yield Input(id="price-input")
        yield Label("Output per Mtok (USD)")
        yield Input(id="price-output")
        yield Static("", id="err-prices", classes="form-error", markup=False)
        yield Button("Set price", id="price-save", variant="primary")
        yield Button("Remove price", id="price-remove", variant="error")

    def on_mount(self) -> None:
        self._list_prices()

    def _list_prices(self) -> None:
        table = self.query_one("#price-table", DataTable)
        table.clear()
        for model, price in self.workspace.prices.models.items():
            table.add_row(model, fmt(price.input_per_mtok), fmt(price.output_per_mtok),
                          key=model)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "price-table" or event.row_key.value is None:
            return
        price = self.workspace.prices.models[event.row_key.value]
        self.query_one("#price-model", Input).value = event.row_key.value
        self.query_one("#price-input", Input).value = fmt(price.input_per_mtok)
        self.query_one("#price-output", Input).value = fmt(price.output_per_mtok)

    def set_price(self) -> None:
        self._clear_errors("prices-form")
        try:
            self.workspace.set_price(
                self._value("price-model").strip(),
                input_per_mtok=parse_number(self._value("price-input"), "input_per_mtok"),
                output_per_mtok=parse_number(self._value("price-output"), "output_per_mtok"),
            )
        except (FormError, ConfigError) as error:
            self._show_error(error, "prices-form", "err-prices")
            return
        self._list_prices()

    def remove_price(self) -> None:
        self._clear_errors("prices-form")
        try:
            self.workspace.remove_price(self._value("price-model").strip())
        except ConfigError as error:
            self._show_error(error, "prices-form", "err-prices")
            return
        self._list_prices()

    def _compose_judge(self) -> ComposeResult:
        setup = self.workspace.judge_setup
        yield Static(f"Judge Setup ({JUDGE_FILE}).", markup=False)
        yield Static(self._judge_status(), id="judge-status", markup=False)
        yield Static(
            "Changing the mode, models or prompt makes a new Judge Setup, which drops its "
            "Certification until it is recalibrated; so does widening clearance to "
            "public_and_private.",
            id="judge-warning", classes="warning", markup=False,
        )
        yield Label("Mode")
        yield Select([(str(m), str(m)) for m in JudgeMode], allow_blank=setup is None,
                     value=str(setup.mode) if setup else Select.NULL, id="judge-mode")
        yield Label("Clearance")
        yield Select([(str(c), str(c)) for c in Clearance], allow_blank=setup is None,
                     value=str(setup.clearance) if setup else Select.NULL,
                     id="judge-clearance")
        yield Label("Models (YAML list of provider, model, optional family and env_var)")
        yield TextArea(models_yaml(setup), id="judge-models")
        yield Label("Prompt file (relative to the Workspace root)")
        yield Input(setup.prompt_file if setup else "", id="judge-prompt-file")
        yield Label("Prompt")
        yield TextArea(setup.prompt if setup else "", id="judge-prompt")
        yield Static("", id="err-judge", classes="form-error", markup=False)
        yield Button("Save Judge Setup", id="judge-save", variant="primary")

    def _judge_status(self, note: str = "") -> str:
        setup = self.workspace.judge_setup
        if setup is None:
            return "No Judge Setup yet: fill the form to create one."
        state = "certified" if self.workspace.judge_certified else "not certified"
        return f"Judge Setup {setup.hash}: {state}." + (f" {note}" if note else "")

    def save_judge_setup(self) -> None:
        self._clear_errors("judge-form")
        setup = self.workspace.judge_setup
        was_certified = self.workspace.judge_certified
        changes: dict[str, object] = {}
        for key in ("mode", "clearance"):
            value = self.query_one(f"#judge-{key}", Select).value
            current = getattr(setup, key, None) if setup else None
            if value != Select.NULL and value != current:
                changes[key] = value
        models = self.query_one("#judge-models", TextArea).text
        prompt_file = self._value("judge-prompt-file").strip()
        prompt = self.query_one("#judge-prompt", TextArea).text
        try:
            if models != models_yaml(setup):
                changes["models"] = parse_models(models)
            if prompt_file != (setup.prompt_file if setup else ""):
                changes["prompt_file"] = prompt_file
            if prompt != (setup.prompt if setup else ""):
                changes["prompt"] = prompt
            self.workspace.update_judge_setup(**changes)
        except (FormError, ConfigError) as error:
            self._show_error(error, "judge-form", "err-judge")
            return
        dropped = was_certified and not self.workspace.judge_certified
        note = ("Certification dropped: recalibrate and certify this Judge Setup."
                if dropped else "Saved.")
        self.query_one("#judge-status", Static).update(self._judge_status(note))
        self.query_one("#judge-models", TextArea).text = models_yaml(self.workspace.judge_setup)

    # Live weight sum: display only; the Workspace decides whether it is valid.

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("set-weight-"):
            texts = [self._value(f"set-weight-{d}") for d in DIMENSIONS]
            self.query_one("#weight-sum", Static).update(weight_sum(texts))

    # Saving

    def on_button_pressed(self, event: Button.Pressed) -> None:
        saves = {"settings-save": self.save_settings, "price-save": self.set_price,
                 "price-remove": self.remove_price, "judge-save": self.save_judge_setup}
        if event.button.id in saves:
            saves[event.button.id]()

    def action_save(self) -> None:
        focused = self.app.focused
        section = next((w for w in (focused.ancestors if focused else ())
                        if isinstance(w, Widget) and w.has_class("settings-section")), None)
        saves = {"settings-form": self.save_settings, "prices-form": self.set_price,
                 "judge-form": self.save_judge_setup}
        if section is not None and section.id in saves:
            saves[section.id]()

    def _clear_errors(self, section: str) -> None:
        for error in self.query_one(f"#{section}").query(".form-error"):
            error.update("")

    def _show_error(self, error: FormError | ConfigError, section: str, fallback: str) -> None:
        """Show error next to the field it names, else in the section's fallback slot."""
        form = self.query_one(f"#{section}")
        try:
            slot = form.query_one(f"#{error_slot(getattr(error, 'field', ''))}", Static)
        except NoMatches:  # a field without its own slot
            slot = form.query_one(f"#{fallback}", Static)
        slot.update(str(error))

    def save_settings(self) -> None:
        self._clear_errors("settings-form")
        status = self.query_one("#settings-status", Static)
        try:
            changes = self._settings_changes()
            self.workspace.update_settings(**changes)
        except (FormError, ConfigError) as error:
            status.update("Not saved: fix the field marked below.")
            self._show_error(error, "settings-form", "settings-status")
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
