"""Benchmark settings: config/settings.yaml, plus the machine-specific config/local.yaml.

settings.yaml is public: Tracks, weights, margins, bands. local.yaml is git-ignored and
optional: the private root paths (ADR-0006), which are home paths not worth publishing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.fields import FieldReader

SETTINGS_FILE = "config/settings.yaml"
LOCAL_FILE = "config/local.yaml"
LOCAL_FIELDS = ("private_roots",)

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
    tracks: tuple[str, ...]  # in catalog order
    weights: dict[str, int | float]
    regression_margin: float
    retest_margin: float
    spot_check_rate: float
    calibration: CalibrationThresholds
    normalization: dict[str, Band]
    private_roots: tuple[Path, ...]


def load_settings(root: Path) -> Settings:
    f = FieldReader(SETTINGS_FILE)
    data = f.read(root)
    if "private_roots" in data:
        raise f.fail(
            "private_roots",
            f"belongs in the git-ignored {LOCAL_FILE}, not the public settings",
        )
    calibration = f.mapping(data.get("calibration"), "calibration")
    f.exact_keys(calibration, "calibration", ("within_one_point", "weighted_kappa"))
    return Settings(
        tracks=_tracks(f, data.get("tracks")),
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
        private_roots=_load_private_roots(root),
    )


def _load_private_roots(root: Path) -> tuple[Path, ...]:
    """The private roots in config/local.yaml; none when the file is absent."""
    if not (root / LOCAL_FILE).is_file():
        return ()
    f = FieldReader(LOCAL_FILE)
    try:
        empty = yaml.safe_load((root / LOCAL_FILE).read_text()) is None
    except yaml.YAMLError:
        empty = False  # f.read reports it, naming the file
    if empty:
        return ()  # blank or comments only, such as a fresh copy of the example
    data = f.read(root)
    for key in data:
        if key not in LOCAL_FIELDS:
            raise f.fail(str(key), f"unknown, expected one of {', '.join(LOCAL_FIELDS)}")
    return _private_roots(f, root, data.get("private_roots", []))


def _tracks(f: FieldReader, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise f.fail("tracks", "must be a non-empty list of Track names")
    tracks: list[str] = []
    for i, entry in enumerate(value):
        name = f.text(entry, f"tracks[{i}]")
        if name in tracks:
            raise f.fail(f"tracks[{i}]", f"duplicate Track {name!r}")
        tracks.append(name)
    return tuple(tracks)


def _private_roots(f: FieldReader, root: Path, value: Any) -> tuple[Path, ...]:
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
        if path.is_relative_to(root.resolve()):
            raise f.fail(
                field,
                f"{path} is inside the public Workspace root {root.resolve()}; a private "
                "root must be a separate repo outside it (ADR-0006)",
            )
        roots.append(path)
    return tuple(roots)


def _normalization(f: FieldReader, value: Any) -> dict[str, Band]:
    raw = f.mapping(value, "normalization")
    f.exact_keys(raw, "normalization", BANDED_MEASURES)
    return {m: _band(f, raw[m], f"normalization.{m}") for m in BANDED_MEASURES}


def _band(f: FieldReader, value: Any, field: str) -> Band:
    raw = f.mapping(value, field)
    f.exact_keys(raw, field, ("better", "thresholds"))
    better = raw["better"]
    if better not in ("lower", "higher"):
        raise f.fail(f"{field}.better", f"must be 'lower' or 'higher', got {better!r}")
    thresholds = raw["thresholds"]
    if not isinstance(thresholds, list) or len(thresholds) != 4:
        raise f.fail(f"{field}.thresholds", "must be a list of 4 numbers")
    numbers = [f.number(t, f"{field}.thresholds[{i}]") for i, t in enumerate(thresholds)]
    if any(a >= b for a, b in zip(numbers, numbers[1:], strict=False)):
        raise f.fail(f"{field}.thresholds", f"must be strictly ascending, got {numbers}")
    return Band(better=better, thresholds=tuple(numbers))


def _weights(f: FieldReader, value: Any) -> dict[str, int | float]:
    raw = f.mapping(value, "weights")
    f.exact_keys(raw, "weights", DIMENSIONS)
    weights = {d: f.number(raw[d], f"weights.{d}", minimum=0) for d in DIMENSIONS}
    total = sum(weights.values())
    if total != 100:
        raise f.fail("weights", f"must sum to 100, got {total}")
    return weights
