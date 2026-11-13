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
