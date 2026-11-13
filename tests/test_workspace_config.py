from pathlib import Path

import pytest

from bench import ConfigError, Workspace


def test_default_config_has_spec_dimension_weights(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    assert ws.settings.weights == {
        "quality": 50,
        "edit_distance": 20,
        "time_saved": 20,
        "cost_latency": 10,
    }


def write_settings(root: Path, text: str) -> None:
    (root / "config" / "settings.yaml").write_text(text)


def test_weights_not_summing_to_100_fail_naming_file_and_field(workspace_dir: Path):
    write_settings(
        workspace_dir,
        "weights: {quality: 50, edit_distance: 20, time_saved: 20, cost_latency: 5}\n",
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
    write_settings(workspace_dir, f"weights: {weights}\n")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)
