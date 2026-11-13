"""Tasks: one folder each, read from the public repo and from private roots (ADR-0006).

A Task folder holds:
  task.md      YAML frontmatter (id, track, trap, version, manual_time_minutes),
               then the Canonical Prompt as the body
  input.*      the input, any one extension
  gold.md      the Gold Answer
  rubric.yaml  the task-specific Criteria, added to the shared Rubric
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.fields import FieldReader
from bench.core.kinds import Visibility
from bench.core.rubric import Rubric, parse_criteria
from bench.core.settings import SETTINGS_FILE

TASK_FILE = "task.md"
GOLD_FILE = "gold.md"
RUBRIC_FILE = "rubric.yaml"

TASK_FIELDS = ("id", "track", "trap", "version", "manual_time_minutes")

# T<track number>.<n>, optionally suffixed, such as T1.1-example for a synthetic twin.
# The numbers are catalog naming only: a Task's Track is what its task.md declares.
_TASK_ID = re.compile(r"^T[1-9][0-9]*\.[1-9][0-9]*(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Task:
    id: str
    track: str
    trap: str
    version: int
    manual_time_minutes: int | float | None  # None until timed once
    canonical_prompt: str
    rubric: Rubric
    visibility: Visibility  # from the root the Task lives in
    root: Path  # the Workspace root for a Public Task, its private root for a Private one
    folder: Path  # <root>/<tasks or examples>/<id>
    input_path: Path
    gold_answer_path: Path
    # What is still a placeholder ("input", "Gold Answer", "Manual Time").
    # A Task with any placeholder loads, so it can be edited, but cannot be Run.
    placeholders: tuple[str, ...]


# Public Tasks: the repo's tasks/ and the synthetic examples/ (ADR-0006).
PUBLIC_TASK_FOLDERS = ("tasks", "examples")
PRIVATE_TASK_FOLDER = "tasks"


def load_tasks(
    workspace_root: Path,
    private_roots: tuple[Path, ...],
    shared: Rubric,
    tracks: tuple[str, ...],
) -> tuple[Task, ...]:
    """Public Tasks from the repo, then Private Tasks from each private root."""
    sources = [(workspace_root, name, Visibility.PUBLIC) for name in PUBLIC_TASK_FOLDERS]
    sources += [(root, PRIVATE_TASK_FOLDER, Visibility.PRIVATE) for root in private_roots]
    tasks: list[Task] = []
    for root, name, visibility in sources:
        for folder in sorted(p for p in (root / name).glob("*") if p.is_dir()):
            task = load_task(workspace_root, root, folder, visibility, shared, tracks)
            for other in tasks:
                if other.id == task.id:
                    raise FieldReader(_display(workspace_root, folder / TASK_FILE)).fail(
                        "id", f"Task {task.id} is also defined in {other.folder}"
                    )
            tasks.append(task)
    return tuple(sorted(tasks, key=lambda t: t.id))


def load_task(
    workspace_root: Path,
    root: Path,
    folder: Path,
    visibility: Visibility,
    shared: Rubric,
    tracks: tuple[str, ...],
) -> Task:
    display = _display(workspace_root, folder)
    f = FieldReader(f"{display}/{TASK_FILE}")
    meta, prompt = _frontmatter(f, folder / TASK_FILE)
    for key in meta:
        if key not in TASK_FIELDS:
            raise f.fail(str(key), f"unknown, expected one of {', '.join(TASK_FIELDS)}")
    task_id = f.text(meta.get("id"), "id")
    if not _TASK_ID.match(task_id):
        raise f.fail("id", f"must look like T<track>.<n>, such as T1.2, got {task_id!r}")
    track = f.text(meta.get("track"), "track")
    if track not in tracks:
        raise f.fail(
            "track",
            f"must be a Track in {SETTINGS_FILE} ({', '.join(tracks)}), got {track!r}",
        )
    version = meta.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise f.fail("version", f"must be a whole number of at least 1, got {version!r}")
    manual_time = meta.get("manual_time_minutes")
    if manual_time is not None:
        manual_time = f.number(manual_time, "manual_time_minutes")
        if manual_time <= 0:
            raise f.fail("manual_time_minutes", f"must be positive, got {manual_time}")
    input_path = _input_path(f"{display}/input.*", folder)
    gold_path = folder / GOLD_FILE
    if not gold_path.is_file():
        raise FieldReader(f"{display}/{GOLD_FILE}").fail("(file)", "is missing")
    placeholders = tuple(
        name
        for name, missing in (
            ("input", _is_placeholder(input_path)),
            ("Gold Answer", _is_placeholder(gold_path)),
            ("Manual Time", manual_time is None),
        )
        if missing
    )
    return Task(
        id=task_id,
        track=track,
        trap=f.text(meta.get("trap"), "trap"),
        version=version,
        manual_time_minutes=manual_time,
        canonical_prompt=prompt,
        rubric=_task_rubric(workspace_root, f"{display}/{RUBRIC_FILE}", shared),
        visibility=visibility,
        root=root,
        folder=folder,
        input_path=input_path,
        gold_answer_path=gold_path,
        placeholders=placeholders,
    )


def _is_placeholder(path: Path) -> bool:
    """Empty, or starting with TODO (the scaffold's marker)."""
    text = path.read_text(errors="replace").strip()
    return not text or text.upper().startswith("TODO")


def _task_rubric(workspace_root: Path, file: str, shared: Rubric) -> Rubric:
    f = FieldReader(file)
    criteria = parse_criteria(f, f.read(workspace_root).get("criteria"), "criteria")
    shared_ids = {c.id for c in shared.criteria}
    for i, criterion in enumerate(criteria):
        if criterion.id in shared_ids:
            raise f.fail(f"criteria[{i}].id", f"{criterion.id!r} is already a shared Criterion")
    return Rubric(criteria=shared.criteria + criteria)


def _input_path(file: str, folder: Path) -> Path:
    inputs = sorted(folder.glob("input.*"))
    if len(inputs) != 1:
        found = ", ".join(p.name for p in inputs) or "none"
        raise FieldReader(file).fail("(file)", f"need exactly one input file, found {found}")
    return inputs[0]


def _frontmatter(f: FieldReader, path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise f.fail("(file)", "is missing")
    parts = path.read_text().split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise f.fail("(frontmatter)", "must start with a --- fenced YAML block")
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise f.fail("(frontmatter)", f"is not valid YAML: {exc}") from exc
    prompt = parts[2].strip()
    if not prompt:
        raise f.fail("(canonical prompt)", "the body after the frontmatter must hold the prompt")
    return f.mapping(meta, "(frontmatter)"), prompt


def _display(workspace_root: Path, path: Path) -> str:
    """A file name for errors: relative to the Workspace root when inside it."""
    try:
        return str(path.relative_to(workspace_root))
    except ValueError:
        return str(path)
