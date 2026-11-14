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
        ("tracks", "[]", "tracks"),
        ("tracks", "[Framing, Framing]", "tracks[1]"),
        ("tracks", "[Framing, '']", "tracks[1]"),
    ],
    ids=[
        "negative-regression-margin",
        "missing-regression-margin",
        "non-numeric-retest-margin",
        "spot-check-rate-above-1",
        "within-one-point-as-percent",
        "missing-kappa",
        "calibration-not-a-mapping",
        "no-tracks",
        "duplicate-track",
        "blank-track",
    ],
)
def test_invalid_margins_and_thresholds_fail_naming_the_field(
    workspace_dir: Path, key: str, value: str, field: str
):
    set_setting(workspace_dir, key, value)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)


def test_default_config_defines_the_four_tracks_in_catalog_order(workspace_dir: Path):
    assert Workspace.open(workspace_dir).settings.tracks == (
        "Framing",
        "Specs",
        "Prioritize",
        "Story",
    )


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


def set_local(root: Path, text: str) -> None:
    (root / "config" / "local.yaml").write_text(text)


def test_private_roots_are_read_from_the_git_ignored_local_config(
    workspace_dir: Path, tmp_path: Path
):
    private = tmp_path / "bench-private"
    private.mkdir()
    absolute = tmp_path / "elsewhere"
    absolute.mkdir()
    set_local(workspace_dir, f"private_roots: ['../bench-private', '{absolute}']\n")

    ws = Workspace.open(workspace_dir)

    assert ws.settings.private_roots == (private.resolve(), absolute.resolve())


def test_an_empty_local_config_means_no_private_roots(workspace_dir: Path):
    set_local(workspace_dir, "# nothing machine-specific yet\n")

    assert Workspace.open(workspace_dir).settings.private_roots == ()


def test_private_roots_in_the_public_settings_are_refused(
    workspace_dir: Path, tmp_path: Path
):
    (tmp_path / "bench-private").mkdir()
    set_setting(workspace_dir, "private_roots", "['../bench-private']")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", "private_roots")
    assert "config/local.yaml" in err.value.message


@pytest.mark.parametrize(
    "text, field",
    [
        ("private_roots: '../bench-private'\n", "private_roots"),
        ("private_roots: ['../does-not-exist']\n", "private_roots[0]"),
        ("private_roots: [42]\n", "private_roots[0]"),
        ("private_roots: []\nweights: {}\n", "weights"),
        ("[unclosed\n", "(file)"),
    ],
    ids=["not-a-list", "missing-directory", "not-a-path", "unknown-key", "bad-yaml"],
)
def test_invalid_local_config_fails_naming_the_field(
    workspace_dir: Path, text: str, field: str
):
    set_local(workspace_dir, text)

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/local.yaml", field)


@pytest.mark.parametrize("inside", [".", "private", "tasks/private"])
def test_a_private_root_inside_the_public_workspace_is_refused(
    workspace_dir: Path, inside: str
):
    (workspace_dir / inside).mkdir(parents=True, exist_ok=True)
    set_local(workspace_dir, f"private_roots: ['{inside}']\n")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/local.yaml", "private_roots[0]")
    assert "inside the public Workspace" in err.value.message


# Pinned: editing the default judge.yaml models or prompt makes a new Judge Setup
# (ADR-0003), so this changes only on purpose.
DEFAULT_JUDGE_SETUP_HASH = "7bae56d113f3e624"


def test_the_default_judge_setup_is_one_public_only_openai_judge(workspace_dir: Path):
    setup = Workspace.open(workspace_dir).judge_setup

    assert setup.mode == "single" and setup.clearance == "public_only"
    [spec] = setup.models
    assert (spec.provider, spec.model, spec.env_var) == (
        "openai", "gpt-6-astra", "OPENAI_API_KEY"
    )
    assert "claude" not in spec.family  # Claude as Baseline is never self-judged
    assert setup.prompt_file == "config/judge/prompt.md"
    assert "Version: 2" in setup.prompt
    assert setup.hash == DEFAULT_JUDGE_SETUP_HASH


def test_the_default_judge_prompt_is_blind_and_fails_invented_facts(workspace_dir: Path):
    prompt = Workspace.open(workspace_dir).judge_setup.prompt.lower()

    assert "gold answer" in prompt and "rubric" in prompt
    assert "1 to 5" in prompt and "one line" in prompt
    assert "invent" in prompt and "correctness" in prompt
    assert "candidate" not in prompt  # the Judge is never told who wrote the Output
