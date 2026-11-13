"""Loading and validation of shared config files under config/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.errors import ConfigError

SETTINGS_FILE = "config/settings.yaml"

DIMENSIONS = ("quality", "edit_distance", "time_saved", "cost_latency")

# Raw measures that are normalized to 1-5 through a band.
BANDED_MEASURES = ("edit_distance", "time_saved", "cost", "latency")


@dataclass(frozen=True)
class Band:
    """Four ascending thresholds splitting a raw measure into five 1-5 scores."""

    better: str  # "lower" or "higher"
    thresholds: tuple[float, float, float, float]


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
    normalization: dict[str, Band]
    private_roots: tuple[Path, ...]


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
        normalization=_normalization(f, data.get("normalization")),
        private_roots=_private_roots(f, root, data.get("private_roots", [])),
    )


def _private_roots(f: _Fields, root: Path, value: Any) -> tuple[Path, ...]:
    if not isinstance(value, list):
        raise f.fail("private_roots", "must be a list of directory paths")
    roots = []
    for i, entry in enumerate(value):
        field = f"private_roots[{i}]"
        if not isinstance(entry, str) or not entry.strip():
            raise f.fail(field, f"must be a directory path, got {entry!r}")
        path = (root / Path(entry).expanduser()).resolve()
        if not path.is_dir():
            raise f.fail(field, f"directory {path} does not exist")
        roots.append(path)
    return tuple(roots)


def _normalization(f: _Fields, value: Any) -> dict[str, Band]:
    raw = f.mapping(value, "normalization")
    f.exact_keys(raw, "normalization", BANDED_MEASURES)
    return {m: _band(f, raw[m], f"normalization.{m}") for m in BANDED_MEASURES}


def _band(f: _Fields, value: Any, field: str) -> Band:
    raw = f.mapping(value, field)
    f.exact_keys(raw, field, ("better", "thresholds"))
    better = raw["better"]
    if better not in ("lower", "higher"):
        raise f.fail(f"{field}.better", f"must be 'lower' or 'higher', got {better!r}")
    thresholds = raw["thresholds"]
    if not isinstance(thresholds, list) or len(thresholds) != 4:
        raise f.fail(f"{field}.thresholds", "must be a list of 4 numbers")
    numbers = [f.number(t, f"{field}.thresholds[{i}]") for i, t in enumerate(thresholds)]
    if any(a >= b for a, b in zip(numbers, numbers[1:])):
        raise f.fail(f"{field}.thresholds", f"must be strictly ascending, got {numbers}")
    return Band(better=better, thresholds=tuple(numbers))


def _weights(f: _Fields, value: Any) -> dict[str, int | float]:
    raw = f.mapping(value, "weights")
    f.exact_keys(raw, "weights", DIMENSIONS)
    weights = {d: f.number(raw[d], f"weights.{d}", minimum=0) for d in DIMENSIONS}
    total = sum(weights.values())
    if total != 100:
        raise f.fail("weights", f"must sum to 100, got {total}")
    return weights
