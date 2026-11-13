import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """A throwaway workspace holding a copy of the committed default config."""
    root = tmp_path / "bench"
    shutil.copytree(REPO_ROOT / "config", root / "config")
    return root
