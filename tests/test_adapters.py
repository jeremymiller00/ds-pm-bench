import sys
from pathlib import Path

import pytest

from bench import EditorError, ExternalEditor, SystemClock


def scripted_editor(tmp_path: Path) -> str:
    """An $EDITOR command that rewrites the file it is given, as a person would."""
    script = tmp_path / "edit.py"
    script.write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "p.write_text(p.read_text().replace('draft', 'final'))\n"
    )
    return f"{sys.executable} {script}"


def test_the_external_editor_returns_what_the_editor_saved(tmp_path: Path):
    assert ExternalEditor(scripted_editor(tmp_path)).edit("a draft") == "a final"


def test_the_external_editor_uses_editor_from_the_environment(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", scripted_editor(tmp_path))

    assert ExternalEditor().edit("draft") == "final"


def test_an_editor_that_fails_raises_and_leaves_no_temp_file(tmp_path: Path):
    seen = tmp_path / "seen.txt"
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path({str(seen)!r}).write_text(sys.argv[1])\n"
        "raise SystemExit(1)\n"
    )

    with pytest.raises(EditorError, match="exited with status 1"):
        ExternalEditor(f"{sys.executable} {script}").edit("draft")
    assert not Path(seen.read_text()).exists()


def test_an_editor_that_cannot_be_started_raises_an_editor_error():
    with pytest.raises(EditorError, match="no-such-editor-xyz"):
        ExternalEditor("no-such-editor-xyz").edit("draft")


def test_the_system_clock_never_goes_backwards():
    clock = SystemClock()
    first = clock.now()
    assert clock.now() >= first
