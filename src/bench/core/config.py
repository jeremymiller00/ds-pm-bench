"""Loading and validation of shared config files under config/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from bench.core.errors import ConfigError

SETTINGS_FILE = "config/settings.yaml"


@dataclass(frozen=True)
class Settings:
    weights: dict[str, int]


def load_settings(root: Path) -> Settings:
    data = yaml.safe_load((root / SETTINGS_FILE).read_text())
    weights = dict(data["weights"])
    total = sum(weights.values())
    if total != 100:
        raise ConfigError(SETTINGS_FILE, "weights", f"must sum to 100, got {total}")
    return Settings(weights=weights)
