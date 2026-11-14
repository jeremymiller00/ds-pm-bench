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
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.errors import TaskEditRefused
from bench.core.fields import FieldReader
from bench.core.kinds import Visibility
from bench.core.rubric import Criterion, Rubric, criterion_dict, parse_criteria
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
    own_criteria: tuple[Criterion, ...]  # the task-specific Criteria
    rubric: Rubric  # the shared Rubric's Criteria, then own_criteria
    visibility: Visibility  # from the root the Task lives in
    root: Path  # the Workspace root for a Public Task, its private root for a Private one
    folder: Path  # <root>/<tasks or examples>/<id>
    input_path: Path
    gold_answer_path: Path
    # What is still a placeholder ("input", "Gold Answer", "Manual Time",
    # "Canonical Prompt").
    # A Task with any placeholder loads, so it can be edited, but cannot be Run.
    placeholders: tuple[str, ...]


# Public Tasks: the repo's tasks/ and the synthetic examples/ (ADR-0006).
PUBLIC_TASK_FOLDERS = ("tasks", "examples")
PRIVATE_TASK_FOLDER = "tasks"


@dataclass(frozen=True)
class TaskRoot:
    """A folder Tasks live under: the Workspace root (public) or a private root."""

    path: Path
    visibility: Visibility

    @property
    def tasks_folder(self) -> Path:
        """Where a new Task goes: tasks/ in the public repo or in the private root."""
        public = self.visibility == Visibility.PUBLIC
        return self.path / (PUBLIC_TASK_FOLDERS[0] if public else PRIVATE_TASK_FOLDER)


@dataclass(frozen=True)
class TaskContext:
    """What loading, scaffolding or editing a Task needs from the Workspace."""

    workspace_root: Path
    private_roots: tuple[Path, ...]
    shared: Rubric  # the shared Rubric every Task's Criteria are added to
    tracks: tuple[str, ...]  # the Tracks a Task may declare

    @property
    def public_root(self) -> TaskRoot:
        return TaskRoot(self.workspace_root, Visibility.PUBLIC)

    def private_root(self, path: Path) -> TaskRoot:
        return TaskRoot(path, Visibility.PRIVATE)


def load_tasks(ctx: TaskContext) -> tuple[Task, ...]:
    """Public Tasks from the repo, then Private Tasks from each private root."""
    sources = [(ctx.public_root, name) for name in PUBLIC_TASK_FOLDERS]
    sources += [(ctx.private_root(path), PRIVATE_TASK_FOLDER) for path in ctx.private_roots]
    tasks: list[Task] = []
    for root, name in sources:
        for folder in sorted(p for p in (root.path / name).glob("*") if p.is_dir()):
            task = load_task(ctx, root, folder)
            for other in tasks:
                if other.id == task.id:
                    raise FieldReader(_display(ctx.workspace_root, folder / TASK_FILE)).fail(
                        "id", f"Task {task.id} is also defined in {other.folder}"
                    )
            tasks.append(task)
    return tuple(sorted(tasks, key=lambda t: t.id))


def load_task(ctx: TaskContext, root: TaskRoot, folder: Path) -> Task:
    display = _display(ctx.workspace_root, folder)
    f = FieldReader(f"{display}/{TASK_FILE}")
    meta, prompt = _frontmatter(f, folder / TASK_FILE)
    for key in meta:
        if key not in TASK_FIELDS:
            raise f.fail(str(key), f"unknown, expected one of {', '.join(TASK_FIELDS)}")
    task_id = f.text(meta.get("id"), "id")
    if not _TASK_ID.match(task_id):
        raise f.fail("id", f"must look like T<track>.<n>, such as T1.2, got {task_id!r}")
    track = f.text(meta.get("track"), "track")
    if track not in ctx.tracks:
        raise f.fail(
            "track",
            f"must be a Track in {SETTINGS_FILE} ({', '.join(ctx.tracks)}), got {track!r}",
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
            ("input", _is_placeholder(input_path.read_text(errors="replace"))),
            ("Gold Answer", _is_placeholder(gold_path.read_text(errors="replace"))),
            ("Manual Time", manual_time is None),
            ("Canonical Prompt", _is_placeholder(prompt)),
        )
        if missing
    )
    own = _own_criteria(ctx, f"{display}/{RUBRIC_FILE}")
    return Task(
        id=task_id,
        track=track,
        trap=f.text(meta.get("trap"), "trap"),
        version=version,
        manual_time_minutes=manual_time,
        canonical_prompt=prompt,
        own_criteria=own,
        rubric=Rubric(criteria=ctx.shared.criteria + own),
        visibility=root.visibility,
        root=root.path,
        folder=folder,
        input_path=input_path,
        gold_answer_path=gold_path,
        placeholders=placeholders,
    )


# The scaffold's marker for content the PM has yet to write (see _is_placeholder).
PLACEHOLDER = "TODO\n"
INPUT_FILE = "input.md"  # the scaffold's input; an existing Task's input may be input.*

# A task-specific Criterion as written, {id, name, description}; validated when the
# Task is loaded back after writing.
type CriterionSpec = Mapping[str, Any]


def scaffold_task(
    ctx: TaskContext,
    root: TaskRoot,
    task_id: str,
    *,
    track: str,
    trap: str,
    canonical_prompt: str,
    criteria: Iterable[CriterionSpec],
    manual_time_minutes: int | float | None,
) -> Task:
    """Write a new Task folder at version 1 with a placeholder input and Gold Answer.

    Validated by loading it back; on a ConfigError nothing is left behind.
    """
    folder = root.tasks_folder / task_id
    f = FieldReader(f"{_display(ctx.workspace_root, folder)}/{TASK_FILE}")
    if not isinstance(task_id, str) or not _TASK_ID.match(task_id):
        raise f.fail("id", f"must look like T<track>.<n>, such as T1.2, got {task_id!r}")
    if folder.exists():
        raise f.fail("id", f"{folder} already exists")
    folder.mkdir(parents=True)
    try:
        _write_task_files(
            folder,
            _task_meta(task_id, track, trap, 1, manual_time_minutes),
            canonical_prompt,
            criteria,
        )
        (folder / INPUT_FILE).write_text(PLACEHOLDER)
        (folder / GOLD_FILE).write_text(PLACEHOLDER)
        return load_task(ctx, root, folder)
    except BaseException:
        shutil.rmtree(folder)
        raise


# What Workspace.update_task may change; version follows ADR-0003, the rest is fixed.
EDITABLE_FIELDS = (
    "track",
    "trap",
    "canonical_prompt",
    "criteria",
    "manual_time_minutes",
    "input",
    "gold_answer",
)


def edit_task(ctx: TaskContext, task: Task, changes: Mapping[str, Any], *, used: bool) -> Task:
    """Rewrite a Task's files with the changes; a new version when a Run used this one.

    An edit that changes nothing leaves the Task as it is. On a ConfigError the
    Task's files are restored.
    """
    for key in changes:
        if key == "visibility":
            raise TaskEditRefused(
                f"Task {task.id}: visibility comes from the root the Task lives in "
                "and cannot be changed by an edit"
            )
        if key not in EDITABLE_FIELDS:
            raise TaskEditRefused(
                f"Task {task.id}: {key!r} cannot be edited; "
                f"editable fields are {', '.join(EDITABLE_FIELDS)}"
            )
    current = _editable_state(task)
    edited = {**current, **changes}
    if "criteria" in changes:
        edited["criteria"] = [dict(c) for c in changes["criteria"]]
    if edited == current:
        return task
    files = [task.folder / TASK_FILE, task.folder / RUBRIC_FILE, task.input_path,
             task.gold_answer_path]
    backup = {path: path.read_bytes() for path in files}
    meta = _task_meta(
        task.id,
        edited["track"],
        edited["trap"],
        task.version + 1 if used else task.version,
        edited["manual_time_minutes"],
    )
    try:
        _write_task_files(task.folder, meta, edited["canonical_prompt"], edited["criteria"])
        task.input_path.write_text(edited["input"])
        task.gold_answer_path.write_text(edited["gold_answer"])
        return load_task(ctx, TaskRoot(task.root, task.visibility), task.folder)
    except BaseException:
        for path, content in backup.items():
            path.write_bytes(content)
        raise


def _editable_state(task: Task) -> dict[str, Any]:
    return {
        "track": task.track,
        "trap": task.trap,
        "canonical_prompt": task.canonical_prompt,
        "criteria": [criterion_dict(c) for c in task.own_criteria],
        "manual_time_minutes": task.manual_time_minutes,
        "input": task.input_path.read_text(),
        "gold_answer": task.gold_answer_path.read_text(),
    }


def _task_meta(
    task_id: str, track: str, trap: str, version: int, manual_time: int | float | None
) -> dict[str, Any]:
    """task.md's frontmatter, in TASK_FIELDS order."""
    return {
        "id": task_id,
        "track": track,
        "trap": trap,
        "version": version,
        "manual_time_minutes": manual_time,
    }


def _write_task_files(
    folder: Path, meta: dict[str, Any], prompt: Any, criteria: Iterable[CriterionSpec]
) -> None:
    """The one write path for task.md and rubric.yaml, shared by scaffold and edit."""
    frontmatter = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    body = prompt.strip() if isinstance(prompt, str) else ""
    (folder / TASK_FILE).write_text(f"---\n{frontmatter}---\n{body}\n")
    data = {"criteria": [dict(c) for c in criteria]}
    (folder / RUBRIC_FILE).write_text(
        "# Task-specific Criteria, scored alongside the shared Rubric (1-5 each).\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )


def _is_placeholder(text: str) -> bool:
    """Empty, or starting with TODO (the scaffold's marker)."""
    text = text.strip()
    return not text or text.upper().startswith("TODO")


def _own_criteria(ctx: TaskContext, file: str) -> tuple[Criterion, ...]:
    f = FieldReader(file)
    criteria = parse_criteria(f, f.read(ctx.workspace_root).get("criteria"), "criteria")
    shared_ids = {c.id for c in ctx.shared.criteria}
    for i, criterion in enumerate(criteria):
        if criterion.id in shared_ids:
            raise f.fail(f"criteria[{i}].id", f"{criterion.id!r} is already a shared Criterion")
    return criteria


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
