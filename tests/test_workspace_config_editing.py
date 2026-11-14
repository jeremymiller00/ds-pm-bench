"""Editing settings, the price table and the Judge Setup through the Workspace (#28)."""

from pathlib import Path

import pytest
import yaml

from bench import ConfigEditRefused, ConfigError, Workspace
from test_workspace_candidates import write_candidate
from test_workspace_runs import cleared
from test_workspace_task_authoring import new_task
from test_workspace_tasks import write_task
from test_workspace_verdicts import BASELINE, complete_run

NEW_WEIGHTS = {"quality": 40, "edit_distance": 30, "time_saved": 20, "cost_latency": 10}


def settings_yaml(root: Path) -> dict:
    return yaml.safe_load((root / "config" / "settings.yaml").read_text())


def test_edited_weights_are_written_back_and_reload(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    settings = ws.update_settings(weights=NEW_WEIGHTS)

    assert settings.weights == NEW_WEIGHTS
    assert ws.settings.weights == NEW_WEIGHTS
    assert settings_yaml(workspace_dir)["weights"] == NEW_WEIGHTS
    assert Workspace.open(workspace_dir).settings == settings


def test_weights_must_sum_to_100_and_a_refused_edit_writes_nothing(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    before = (workspace_dir / "config" / "settings.yaml").read_text()

    with pytest.raises(ConfigError) as err:
        ws.update_settings(weights={"quality": 40})

    assert (err.value.file, err.value.field) == ("config/settings.yaml", "weights")
    assert "90" in err.value.message
    assert (workspace_dir / "config" / "settings.yaml").read_text() == before
    assert ws.settings.weights["quality"] == 50


def test_margins_spot_check_rate_and_calibration_thresholds_are_editable(
    workspace_dir: Path,
):
    ws = Workspace.open(workspace_dir)

    s = ws.update_settings(
        regression_margin=0.4,
        retest_margin=0.1,
        spot_check_rate=0.5,
        calibration={"weighted_kappa": 0.7},
    )

    reloaded = Workspace.open(workspace_dir).settings
    assert (s.regression_margin, s.retest_margin, s.spot_check_rate) == (0.4, 0.1, 0.5)
    assert (s.calibration.within_one_point, s.calibration.weighted_kappa) == (0.9, 0.7)
    assert reloaded == s


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"regression_margin": -1}, "regression_margin"),
        ({"retest_margin": "small"}, "retest_margin"),
        ({"spot_check_rate": 1.5}, "spot_check_rate"),
        ({"calibration": {"within_one_point": 90}}, "calibration.within_one_point"),
        ({"normalization": {"latency": {"better": "lower", "thresholds": [30, 10, 60, 120]}}},
         "normalization.latency.thresholds"),
        ({"normalization": {"speed": {"better": "lower", "thresholds": [1, 2, 3, 4]}}},
         "normalization.speed"),
        ({"vibes": 3}, "vibes"),
        ({"private_roots": ["../private"]}, "private_roots"),
    ],
    ids=["negative-margin", "text-margin", "rate-above-1", "threshold-as-percent",
         "descending-band", "unknown-measure", "unknown-setting", "private-roots"],
)
def test_an_invalid_settings_edit_names_the_field(workspace_dir: Path, changes, field):
    ws = Workspace.open(workspace_dir)

    with pytest.raises(ConfigError) as err:
        ws.update_settings(**changes)

    assert (err.value.file, err.value.field) == ("config/settings.yaml", field)


def test_one_normalization_band_is_editable_alone(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    s = ws.update_settings(
        normalization={"latency": {"better": "lower", "thresholds": [5, 15, 45, 90]}}
    )

    assert s.normalization["latency"].thresholds == (5, 15, 45, 90)
    assert s.normalization["cost"].thresholds == (0.01, 0.05, 0.20, 1.00)
    assert Workspace.open(workspace_dir).settings.normalization == s.normalization


def test_a_written_settings_file_stays_readable_yaml_in_its_own_key_order(
    workspace_dir: Path,
):
    path = workspace_dir / "config" / "settings.yaml"
    order = list(yaml.safe_load(path.read_text()))

    Workspace.open(workspace_dir).update_settings(spot_check_rate=0.5)

    text = path.read_text()
    assert list(yaml.safe_load(text)) == order
    assert list(yaml.safe_load(text)["weights"]) == list(NEW_WEIGHTS)
    assert text.startswith("# Benchmark settings.")
    assert "{" not in text  # block style, one setting per line
    assert "spot_check_rate: 0.5\n" in text


def test_tracks_can_be_added_and_reordered(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    s = ws.update_settings(tracks=["Specs", "Framing", "Prioritize", "Story", "Metrics"])

    assert s.tracks == ("Specs", "Framing", "Prioritize", "Story", "Metrics")
    assert Workspace.open(workspace_dir).settings.tracks == s.tracks


def test_removing_a_track_a_task_uses_is_refused_naming_the_task(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    new_task(ws, "T1.1", track="Framing")
    before = (workspace_dir / "config" / "settings.yaml").read_text()

    with pytest.raises(ConfigEditRefused) as err:
        ws.update_settings(tracks=["Specs", "Prioritize", "Story"])

    assert (err.value.file, err.value.field) == ("config/settings.yaml", "tracks")
    assert "Framing" in err.value.message and "T1.1" in err.value.message
    assert (workspace_dir / "config" / "settings.yaml").read_text() == before
    assert isinstance(err.value, ConfigError)


def test_removing_a_track_only_a_run_used_task_version_uses_is_refused(workspace_dir: Path):
    write_task(workspace_dir, "T1.1", track="Framing")
    write_candidate(workspace_dir, BASELINE, cleared("public_only", baseline=True))
    ws = Workspace.open(workspace_dir)
    complete_run(ws, BASELINE)
    ws.update_task("T1.1", track="Specs")  # version 2; the Run used version 1, in Framing

    with pytest.raises(ConfigEditRefused, match=r"T1\.1 version 1"):
        ws.update_settings(tracks=["Specs", "Prioritize", "Story"])


def prices_yaml(root: Path) -> dict:
    return yaml.safe_load((root / "config" / "prices.yaml").read_text())


def test_a_model_price_is_added_changed_and_removed(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    ws.set_price("openai/gpt-6", input_per_mtok=2.5, output_per_mtok=10)
    ws.set_price("anthropic/claude-x", input_per_mtok=3, output_per_mtok=15)
    table = ws.set_price("openai/gpt-6", input_per_mtok=2, output_per_mtok=8)

    assert table.models["openai/gpt-6"].input_per_mtok == 2
    assert list(prices_yaml(workspace_dir)["models"]) == ["openai/gpt-6", "anthropic/claude-x"]
    assert Workspace.open(workspace_dir).prices == table

    table = ws.remove_price("openai/gpt-6")

    assert list(table.models) == ["anthropic/claude-x"]
    assert ws.prices == table == Workspace.open(workspace_dir).prices
    assert (workspace_dir / "config" / "prices.yaml").read_text().startswith("# Price table")


@pytest.mark.parametrize(
    "model, prices, field",
    [
        ("openai/gpt-6", {"input_per_mtok": -1, "output_per_mtok": 1},
         "models.openai/gpt-6.input_per_mtok"),
        ("openai/gpt-6", {"input_per_mtok": 1, "output_per_mtok": "cheap"},
         "models.openai/gpt-6.output_per_mtok"),
        (" ", {"input_per_mtok": 1, "output_per_mtok": 1}, "models"),
    ],
    ids=["negative", "not-a-number", "blank-model"],
)
def test_an_invalid_price_names_the_field_and_writes_nothing(
    workspace_dir: Path, model, prices, field
):
    ws = Workspace.open(workspace_dir)
    before = (workspace_dir / "config" / "prices.yaml").read_text()

    with pytest.raises(ConfigError) as err:
        ws.set_price(model, **prices)

    assert (err.value.file, err.value.field) == ("config/prices.yaml", field)
    assert (workspace_dir / "config" / "prices.yaml").read_text() == before


def test_removing_an_unpriced_model_names_it(workspace_dir: Path):
    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir).remove_price("openai/gpt-6")

    assert (err.value.file, err.value.field) == ("config/prices.yaml", "models.openai/gpt-6")


def judge_files(root: Path) -> tuple[str, str]:
    return ((root / "config" / "judge.yaml").read_text(),
            (root / "config" / "judge" / "prompt.md").read_text())


def test_editing_the_judge_prompt_writes_it_and_makes_a_new_judge_setup(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    old = ws.judge_setup.hash

    setup = ws.update_judge_setup(prompt="Score strictly; invented facts score 1.")

    assert setup.hash != old
    assert ws.judge_setup == setup
    assert judge_files(workspace_dir)[1] == "Score strictly; invented facts score 1."
    assert Workspace.open(workspace_dir).judge_setup.hash == setup.hash


def test_a_panel_of_new_judge_models_makes_a_new_judge_setup(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    old = ws.judge_setup.hash
    panel = [
        {"provider": "openai", "model": "gpt-6"},
        {"provider": "google", "model": "gemini-4", "env_var": "GEMINI_KEY"},
    ]

    setup = ws.update_judge_setup(mode="panel", models=panel)

    assert setup.hash != old
    assert [m.label for m in setup.models] == ["openai/gpt-6", "google/gemini-4"]
    assert setup.models[1].env_var == "GEMINI_KEY"
    reloaded = Workspace.open(workspace_dir).judge_setup
    assert (reloaded.mode, reloaded.hash) == ("panel", setup.hash)


def test_clearance_is_editable_and_is_not_part_of_the_judge_setup_hash(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)
    old = ws.judge_setup.hash

    setup = ws.update_judge_setup(clearance="public_and_private")

    assert setup.clearance == "public_and_private"
    assert setup.hash == old
    assert Workspace.open(workspace_dir).judge_setup.clearance == "public_and_private"


def test_the_prompt_can_move_to_a_new_file(workspace_dir: Path):
    ws = Workspace.open(workspace_dir)

    setup = ws.update_judge_setup(prompt_file="config/judge/strict.md", prompt="Be strict.")

    assert setup.prompt_file == "config/judge/strict.md"
    assert (workspace_dir / "config" / "judge" / "strict.md").read_text() == "Be strict."
    assert yaml.safe_load(judge_files(workspace_dir)[0])["prompt"] == "config/judge/strict.md"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"mode": "panel"}, "models"),
        ({"mode": "jury"}, "mode"),
        ({"models": [{"provider": "acme", "model": "m"}]}, "models[0].provider"),
        ({"models": [{"provider": "openai"}]}, "models[0].model"),
        ({"models": []}, "models"),
        ({"clearance": "everything"}, "clearance"),
        ({"prompt": "  "}, "prompt"),
        ({"prompt_file": "config/judge/missing.md"}, "prompt"),
    ],
    ids=["panel-of-one", "unknown-mode", "unknown-provider", "no-model", "no-models",
         "unknown-clearance", "blank-prompt", "missing-prompt-file"],
)
def test_an_invalid_judge_setup_edit_names_the_field_and_writes_nothing(
    workspace_dir: Path, changes, field
):
    ws = Workspace.open(workspace_dir)
    before = judge_files(workspace_dir)

    with pytest.raises(ConfigError) as err:
        ws.update_judge_setup(**changes)

    assert (err.value.file, err.value.field) == ("config/judge.yaml", field)
    assert judge_files(workspace_dir) == before
    assert Workspace.open(workspace_dir).judge_setup == ws.judge_setup


def test_a_judge_setup_is_created_when_there_is_none(workspace_dir: Path):
    (workspace_dir / "config" / "judge.yaml").unlink()
    ws = Workspace.open(workspace_dir)
    assert ws.judge_setup is None

    setup = ws.update_judge_setup(
        mode="single", clearance="public_only", prompt_file="config/judge/prompt.md",
        models=[{"provider": "openai", "model": "gpt-6"}],
    )

    assert Workspace.open(workspace_dir).judge_setup == setup


def test_a_written_judge_setup_keeps_its_header_and_key_order(workspace_dir: Path):
    path = workspace_dir / "config" / "judge.yaml"
    order = list(yaml.safe_load(path.read_text()))

    Workspace.open(workspace_dir).update_judge_setup(clearance="public_and_private")

    text = path.read_text()
    assert text.startswith("# The Judge Setup")
    assert list(yaml.safe_load(text)) == order


def test_an_edit_changes_only_the_edited_settings_lines_keeping_every_comment(
    workspace_dir: Path,
):
    path = workspace_dir / "config" / "settings.yaml"
    before = path.read_text().splitlines()

    Workspace.open(workspace_dir).update_settings(retest_margin=0.3)

    after = path.read_text().splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [("retest_margin: 0.2", "retest_margin: 0.3")]
