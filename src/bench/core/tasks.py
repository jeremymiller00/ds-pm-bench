"""Tasks: one folder each, read from the public repo and from private roots (ADR-0006).

A Task folder holds:
  task.md      YAML frontmatter (id, track, trap, version, manual_time_minutes),
               then the Canonical Prompt as the body
  input.*      the input, any one extension
  gold.md      the Gold Answer
  rubric.yaml  the task-specific Criteria, added to the shared Rubric
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from bench.core.fields import FieldReader
from bench.core.rubric import Rubric, parse_criteria

TASK_FILE = "task.md"
GOLD_FILE = "gold.md"
RUBRIC_FILE = "rubric.yaml"


@dataclass(frozen=True)
class Task:
    id: str
    track: str
    trap: str
    version: int
    manual_time_minutes: int | float
    canonical_prompt: str
    rubric: Rubric
    visibility: str  # "public" or "private", from the root the Task lives in
    folder: Path
    input_path: Path
    gold_answer_path: Path


def load_task(
    workspace_root: Path, folder: Path, visibility: str, shared: Rubric
) -> Task:
    f = FieldReader(_display(workspace_root, folder / TASK_FILE))
    meta, prompt = _frontmatter(f, folder / TASK_FILE)
    rubric_reader = FieldReader(_display(workspace_root, folder / RUBRIC_FILE))
    task_criteria = parse_criteria(
        rubric_reader, rubric_reader.read(workspace_root).get("criteria"), "criteria"
    )
    return Task(
        id=f.text(meta.get("id"), "id"),
        track=f.text(meta.get("track"), "track"),
        trap=f.text(meta.get("trap"), "trap"),
        version=meta["version"],
        manual_time_minutes=f.number(meta.get("manual_time_minutes"), "manual_time_minutes"),
        canonical_prompt=prompt,
        rubric=Rubric(criteria=shared.criteria + task_criteria),
        visibility=visibility,
        folder=folder,
        input_path=next(folder.glob("input.*")),
        gold_answer_path=folder / GOLD_FILE,
    )


def _frontmatter(f: FieldReader, path: Path) -> tuple[dict, str]:
    text = path.read_text()
    _, header, body = text.split("---\n", 2)
    return f.mapping(yaml.safe_load(header), "(frontmatter)"), body.strip()


def _display(workspace_root: Path, path: Path) -> str:
    """A file name for errors: relative to the Workspace root when inside it."""
    try:
        return str(path.relative_to(workspace_root))
    except ValueError:
        return str(path)
