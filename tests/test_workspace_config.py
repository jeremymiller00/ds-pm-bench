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


@pytest.mark.parametrize(
    "key, value, field",
    [
        ("regression_margin", "-0.5", "regression_margin"),
        ("regression_margin", "null", "regression_margin"),
        ("retest_margin", "'small'", "retest_margin"),
        ("spot_check_rate", "1.5", "spot_check_rate"),
        (
            "calibration",
            "{within_one_point: 90, weighted_kappa: 0.6}",
            "calibration.within_one_point",
        ),
        ("calibration", "{within_one_point: 0.9}", "calibration.weighted_kappa"),
        ("calibration", "0.9", "calibration"),
    ],
    ids=[
        "negative-regression-margin",
        "missing-regression-margin",
        "non-numeric-retest-margin",
        "spot-check-rate-above-1",
        "within-one-point-as-percent",
        "missing-kappa",
        "calibration-not-a-mapping",
    ],
)
def test_invalid_margins_and_thresholds_fail_naming_the_field(
    workspace_dir: Path, key: str, value: str, field: str
):
    set_setting(workspace_dir, key, value)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)


def test_missing_settings_file_fails_naming_the_file(workspace_dir: Path):
    (workspace_dir / "config" / "settings.yaml").unlink()

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.file == "config/settings.yaml"
    assert "missing" in str(err.value)


def test_malformed_yaml_fails_naming_the_file(workspace_dir: Path):
    (workspace_dir / "config" / "settings.yaml").write_text("weights: [unclosed\n")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.file == "config/settings.yaml"


def test_default_config_has_normalization_bands_for_each_measure(workspace_dir: Path):
    bands = Workspace.open(workspace_dir).settings.normalization

    assert set(bands) == {"edit_distance", "time_saved", "cost", "latency"}
    assert bands["edit_distance"].better == "lower"
    assert bands["time_saved"].better == "higher"
    for band in bands.values():
        assert len(band.thresholds) == 4
        assert list(band.thresholds) == sorted(band.thresholds)


ED = "normalization.edit_distance"


@pytest.mark.parametrize(
    "value, field",
    [
        ("{better: lower, thresholds: [0.05, 0.3, 0.15, 0.5]}", f"{ED}.thresholds"),
        ("{better: lower, thresholds: [0.05, 0.15, 0.3]}", f"{ED}.thresholds"),
        ("{better: lower, thresholds: [0.05, 0.15, x, 0.5]}", f"{ED}.thresholds[2]"),
        ("{better: sideways, thresholds: [0.05, 0.15, 0.3, 0.5]}", f"{ED}.better"),
    ],
    ids=["not-ascending", "wrong-count", "non-numeric", "bad-direction"],
)
def test_invalid_normalization_band_fails_naming_the_field(
    workspace_dir: Path, value: str, field: str
):
    path = workspace_dir / "config" / "settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["normalization"]["edit_distance"] = yaml.safe_load(value)
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)


def test_missing_normalization_band_fails_naming_the_measure(workspace_dir: Path):
    path = workspace_dir / "config" / "settings.yaml"
    data = yaml.safe_load(path.read_text())
    del data["normalization"]["latency"]
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert err.value.field == "normalization.latency"


def test_default_config_has_no_private_roots(workspace_dir: Path):
    assert Workspace.open(workspace_dir).settings.private_roots == ()


def test_private_roots_resolve_relative_to_the_workspace(workspace_dir: Path, tmp_path: Path):
    private = tmp_path / "bench-private"
    private.mkdir()
    absolute = tmp_path / "elsewhere"
    absolute.mkdir()
    set_setting(workspace_dir, "private_roots", f"['../bench-private', '{absolute}']")

    ws = Workspace.open(workspace_dir)

    assert ws.settings.private_roots == (private.resolve(), absolute.resolve())


@pytest.mark.parametrize(
    "value, field",
    [
        ("'../bench-private'", "private_roots"),
        ("['../does-not-exist']", "private_roots[0]"),
        ("[42]", "private_roots[0]"),
    ],
    ids=["not-a-list", "missing-directory", "not-a-path"],
)
def test_invalid_private_roots_fail_naming_the_field(
    workspace_dir: Path, value: str, field: str
):
    set_setting(workspace_dir, "private_roots", value)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)
