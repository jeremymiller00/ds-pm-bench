from pathlib import Path

import pytest
import yaml

from bench import ConfigError, Workspace


def test_default_config_has_spec_dimension_weights(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    assert ws.settings.weights == {
        "quality": 50,
        "edit_distance": 20,
        "time_saved": 20,
        "cost_latency": 10,
    }


def set_setting(root: Path, key: str, value: str) -> None:
    """Replace one top-level key of the default settings.yaml with a YAML value."""
    path = root / "config" / "settings.yaml"
    data = yaml.safe_load(path.read_text())
    data[key] = yaml.safe_load(value)
    path.write_text(yaml.safe_dump(data))


def test_weights_not_summing_to_100_fail_naming_file_and_field(workspace_dir: Path):
    set_setting(
        workspace_dir,
        "weights",
        "{quality: 50, edit_distance: 20, time_saved: 20, cost_latency: 5}",
    )

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.file == "config/settings.yaml"
    assert err.value.field == "weights"
    assert "100" in str(err.value) and "95" in str(err.value)
    assert "config/settings.yaml" in str(err.value)


@pytest.mark.parametrize(
    "weights, field",
    [
        ("{quality: 60, edit_distance: 20, time_saved: 20}", "weights.cost_latency"),
        (
            "{quality: 40, edit_distance: 20, time_saved: 20, cost_latency: 10, vibes: 10}",
            "weights.vibes",
        ),
        (
            "{quality: fifty, edit_distance: 20, time_saved: 20, cost_latency: 10}",
            "weights.quality",
        ),
        (
            "{quality: 110, edit_distance: -10, time_saved: 0, cost_latency: 0}",
            "weights.edit_distance",
        ),
    ],
    ids=["missing-dimension", "unknown-dimension", "not-a-number", "negative"],
)
def test_invalid_weight_entries_fail_naming_the_dimension(
    workspace_dir: Path, weights: str, field: str
):
    set_setting(workspace_dir, "weights", weights)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)


def test_default_config_has_spec_margins_and_thresholds(workspace_dir: Path):
    s = Workspace.open(workspace_dir).settings

    assert s.regression_margin == 0.5
    assert s.retest_margin == 0.2
    assert s.spot_check_rate == 0.25
    assert s.calibration.within_one_point == 0.9
    assert s.calibration.weighted_kappa == 0.6
