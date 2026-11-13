from pathlib import Path

import pytest
import yaml

from bench import ConfigError, Workspace


def edit_yaml(root: Path, relative: str, change) -> None:
    path = root / relative
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))


def test_default_shared_rubric_has_the_five_spec_criteria(workspace_dir: Path):
    rubric = Workspace.open(workspace_dir).rubric

    assert [c.name for c in rubric.criteria] == [
        "Correctness",
        "Completeness",
        "Clarity",
        "PM judgment",
        "Format fit",
    ]
    assert all(c.description for c in rubric.criteria)
    assert rubric.criterion("pm_judgment").name == "PM judgment"


@pytest.mark.parametrize(
    "change, field",
    [
        (lambda d: d.update(criteria=[]), "criteria"),
        (lambda d: d["criteria"].append(dict(d["criteria"][0])), "criteria[5].id"),
        (lambda d: d["criteria"][1].pop("name"), "criteria[1].name"),
        (lambda d: d["criteria"][2].update(description=""), "criteria[2].description"),
        (lambda d: d["criteria"][0].update(id="Bad Id"), "criteria[0].id"),
    ],
    ids=["empty", "duplicate-id", "missing-name", "blank-description", "invalid-id"],
)
def test_invalid_shared_rubric_fails_naming_file_and_field(
    workspace_dir: Path, change, field: str
):
    edit_yaml(workspace_dir, "config/rubric.yaml", change)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/rubric.yaml", field)


def test_default_price_table_is_empty_until_models_are_priced(workspace_dir: Path):
    assert Workspace.open(workspace_dir).prices.models == {}


def test_price_table_loads_per_model_token_prices(workspace_dir: Path):
    (workspace_dir / "config" / "prices.yaml").write_text(
        "models:\n"
        "  vendor/model-a-2026-01:\n"
        "    input_per_mtok: 3\n"
        "    output_per_mtok: 15.5\n"
    )

    price = Workspace.open(workspace_dir).prices.models["vendor/model-a-2026-01"]

    assert (price.input_per_mtok, price.output_per_mtok) == (3, 15.5)


@pytest.mark.parametrize(
    "text, field",
    [
        ("models: [a, b]\n", "models"),
        ("models:\n  m: {input_per_mtok: 3}\n", "models.m.output_per_mtok"),
        ("models:\n  m: {input_per_mtok: -1, output_per_mtok: 2}\n", "models.m.input_per_mtok"),
        ("models:\n  m: {input_per_mtok: 1, output_per_mtok: 2, cached: 1}\n", "models.m.cached"),
    ],
    ids=["not-a-mapping", "missing-output-price", "negative-price", "unknown-field"],
)
def test_invalid_price_table_fails_naming_file_and_field(
    workspace_dir: Path, text: str, field: str
):
    (workspace_dir / "config" / "prices.yaml").write_text(text)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/prices.yaml", field)


@pytest.mark.parametrize("relative", ["config/rubric.yaml", "config/prices.yaml"])
def test_missing_shared_config_file_fails_naming_it(workspace_dir: Path, relative: str):
    (workspace_dir / relative).unlink()

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.file == relative
