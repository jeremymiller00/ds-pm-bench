"""Loading and validation of shared config files under config/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Settings:
    weights: dict[str, int]


def load_settings(root: Path) -> Settings:
    data = yaml.safe_load((root / "config" / "settings.yaml").read_text())
    return Settings(weights=dict(data["weights"]))
