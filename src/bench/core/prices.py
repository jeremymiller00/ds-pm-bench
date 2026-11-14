"""The price table: per-model token prices for computing cost."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    return _parse(f, f.read(root))


def set_price(
    root: Path, model: str, input_per_mtok: float, output_per_mtok: float
) -> PriceTable:
    """Add or change one model's price in config/prices.yaml; a new model goes last.
    ConfigError, naming the field, writes nothing."""
    f = FieldReader(PRICES_FILE)
    if not isinstance(model, str) or not model.strip():
        raise f.fail("models", f"a priced model must be non-empty text, got {model!r}")
    data = f.read(root)
    models = dict(f.mapping(data.get("models"), "models"))
    models[model.strip()] = {
        "input_per_mtok": input_per_mtok, "output_per_mtok": output_per_mtok
    }
    return _write(f, root, {**data, "models": models})


def remove_price(root: Path, model: str) -> PriceTable:
    """Remove one model's price from config/prices.yaml; ConfigError if it has none."""
    f = FieldReader(PRICES_FILE)
    data = f.read(root)
    models = dict(f.mapping(data.get("models"), "models"))
    if model not in models:
        raise f.fail(f"models.{model}", "has no price to remove")
    del models[model]
    return _write(f, root, {**data, "models": models})


def _write(f: FieldReader, root: Path, data: dict[str, Any]) -> PriceTable:
    table = _parse(f, data)
    f.write(root, data)
    return table


def _parse(f: FieldReader, data: dict[str, Any]) -> PriceTable:
    raw = f.mapping(data.get("models"), "models")
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
