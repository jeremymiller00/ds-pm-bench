"""Loading and validation of shared config files under config/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.errors import ConfigError

SETTINGS_FILE = "config/settings.yaml"

DIMENSIONS = ("quality", "edit_distance", "time_saved", "cost_latency")


@dataclass(frozen=True)
class CalibrationThresholds:
    within_one_point: float
    weighted_kappa: float


@dataclass(frozen=True)
class Settings:
    weights: dict[str, int | float]
    regression_margin: float
    retest_margin: float
    spot_check_rate: float
    calibration: CalibrationThresholds


class _Fields:
    """Reads typed fields from one config file, raising ConfigError with a dotted path."""

    def __init__(self, file: str) -> None:
        self.file = file

    def read(self, root: Path) -> dict[str, Any]:
        """Parse the file as a YAML mapping."""
        path = root / self.file
        if not path.is_file():
            raise self.fail("(file)", "is missing")
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise self.fail("(file)", f"is not valid YAML: {exc}") from exc
        return self.mapping(data, "(file)")

    def fail(self, field: str, message: str) -> ConfigError:
        return ConfigError(self.file, field, message)

    def mapping(self, value: Any, field: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise self.fail(field, "must be a mapping")
        return value

    def number(self, value: Any, field: str, *, minimum: float | None = None) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self.fail(field, f"must be a number, got {value!r}")
        if minimum is not None and value < minimum:
            raise self.fail(field, f"must be at least {minimum}, got {value}")
        return value

    def fraction(self, value: Any, field: str) -> float:
        number = self.number(value, field)
        if not 0 <= number <= 1:
            raise self.fail(field, f"must be between 0 and 1, got {number}")
        return number

    def exact_keys(self, data: dict[str, Any], field: str, keys: tuple[str, ...]) -> None:
        for key in data:
            if key not in keys:
                raise self.fail(f"{field}.{key}", f"unknown, expected one of {', '.join(keys)}")
        for key in keys:
            if key not in data:
                raise self.fail(f"{field}.{key}", "is missing")


def load_settings(root: Path) -> Settings:
    f = _Fields(SETTINGS_FILE)
    data = f.read(root)
    calibration = f.mapping(data.get("calibration"), "calibration")
    f.exact_keys(calibration, "calibration", ("within_one_point", "weighted_kappa"))
    return Settings(
        weights=_weights(f, data.get("weights")),
        regression_margin=f.number(
            data.get("regression_margin"), "regression_margin", minimum=0
        ),
        retest_margin=f.number(data.get("retest_margin"), "retest_margin", minimum=0),
        spot_check_rate=f.fraction(data.get("spot_check_rate"), "spot_check_rate"),
        calibration=CalibrationThresholds(
            within_one_point=f.fraction(
                calibration["within_one_point"], "calibration.within_one_point"
            ),
            weighted_kappa=f.fraction(
                calibration["weighted_kappa"], "calibration.weighted_kappa"
            ),
        ),
    )


def _weights(f: _Fields, value: Any) -> dict[str, int | float]:
    raw = f.mapping(value, "weights")
    f.exact_keys(raw, "weights", DIMENSIONS)
    weights = {d: f.number(raw[d], f"weights.{d}", minimum=0) for d in DIMENSIONS}
    total = sum(weights.values())
    if total != 100:
        raise f.fail("weights", f"must sum to 100, got {total}")
    return weights
