"""The pre-commit check that warns when a Public Task's input or Gold Answer looks like it
holds internal identifiers (story 69), run as pre-commit runs it: a script given the
staged file names, from the repo root."""

import subprocess
import sys
from pathlib import Path

import pytest

from bench import ConfigError, Workspace
from test_workspace_tasks import write_task

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_public_tasks.py"


def check(root: Path, *files: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *files], cwd=root, capture_output=True, text=True
    )


def test_an_email_in_a_public_tasks_input_is_warned_about(workspace_dir: Path):
    write_task(workspace_dir, "T1.1", input_text="Ask jane.doe@acme-corp.com for the data.")

    result = check(workspace_dir, "tasks/T1.1/input.md", "tasks/T1.1/gold.md")

    assert result.returncode == 0  # a warning, never a blocked commit
    assert "tasks/T1.1/input.md:1" in result.stdout
    assert "jane.doe@acme-corp.com" in result.stdout
    assert "gold.md" not in result.stdout


def test_denylisted_patterns_from_the_local_config_are_warned_about(workspace_dir: Path):
    write_task(workspace_dir, "T1.1", gold="Pull it from DATALAKE-PROD.internal first.")
    (workspace_dir / "config" / "local.yaml").write_text(
        "public_denylist: ['datalake-prod', 'project\\s+falcon']\n"
    )

    result = check(workspace_dir, "tasks/T1.1/gold.md")

    assert "tasks/T1.1/gold.md:1" in result.stdout
    assert "DATALAKE-PROD" in result.stdout


def test_clean_public_tasks_and_other_files_pass_silently(workspace_dir: Path):
    write_task(workspace_dir, "T1.1")
    (workspace_dir / "README.md").write_text("Contact me@example.com")

    result = check(workspace_dir, "tasks/T1.1/input.md", "tasks/T1.1/gold.md", "README.md")

    assert (result.returncode, result.stdout) == (0, "")


def test_an_invalid_denylist_names_the_file_and_field(workspace_dir: Path):
    (workspace_dir / "config" / "local.yaml").write_text("public_denylist: ['(unclosed']\n")

    with pytest.raises(ConfigError) as err:
        Workspace.open(workspace_dir)

    assert (err.value.file, err.value.field) == ("config/local.yaml", "public_denylist[0]")


def test_the_hook_is_wired_into_pre_commit():
    config = (SCRIPT.parent.parent / ".pre-commit-config.yaml").read_text()

    assert "repo: local" in config and "scripts/check_public_tasks.py" in config
