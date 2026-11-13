"""The price table: per-model token prices for computing cost."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bench.core.fields import FieldReader

PRICES_FILE = "config/prices.yaml"


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class PriceTable:
    models: dict[str, ModelPrice]


def load_prices(root: Path) -> PriceTable:
    f = FieldReader(PRICES_FILE)
    raw = f.mapping(f.read(root).get("models"), "models")
    models = {}
    for model, entry in raw.items():
        at = f"models.{model}"
        price = f.mapping(entry, at)
        f.exact_keys(price, at, ("input_per_mtok", "output_per_mtok"))
        models[str(model)] = ModelPrice(
            input_per_mtok=f.number(price["input_per_mtok"], f"{at}.input_per_mtok", minimum=0),
            output_per_mtok=f.number(price["output_per_mtok"], f"{at}.output_per_mtok", minimum=0),
        )
    return PriceTable(models=models)
