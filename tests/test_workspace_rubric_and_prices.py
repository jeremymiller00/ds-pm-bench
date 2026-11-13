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
