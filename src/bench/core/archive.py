"""Judge prompts and shared Rubrics archived by content hash (ADR-0003).

Before the judge prompt or the shared Rubric is replaced, and whenever a Run or a
Certification uses one, its text is copied to config/archive/ under its content hash:
  judge-prompts/<hash>.md   a judge prompt's text
  rubrics/<hash>.yaml       a shared Rubric's Criteria
Runs and Certifications record the hashes, so what produced a past Verdict can be
rebuilt from the repo alone, committed or not. Archiving is idempotent: one file per hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from bench.core.fields import FieldReader
from bench.core.rubric import Rubric, criterion_dict, parse_criteria

ARCHIVE_FOLDER = "config/archive"
PROMPTS_FOLDER = "judge-prompts"
RUBRICS_FOLDER = "rubrics"


def content_hash(text: str) -> str:
    """The short content hash archives, Runs and Certifications name text by."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def rubric_hash(rubric: Rubric) -> str:
    """The shared Rubric's identity: a hash of its Criteria, in order."""
    criteria = [criterion_dict(c) for c in rubric.criteria]
    return content_hash(json.dumps(criteria, sort_keys=True, separators=(",", ":")))


def archive_judge_prompt(root: Path, prompt: str) -> str:
    """Keep the judge prompt's text under its hash; returns the hash."""
    digest = content_hash(prompt)
    _keep(root / ARCHIVE_FOLDER / PROMPTS_FOLDER / f"{digest}.md", prompt)
    return digest


def archived_judge_prompt(root: Path, digest: str) -> str | None:
    path = root / ARCHIVE_FOLDER / PROMPTS_FOLDER / f"{digest}.md"
    return path.read_text() if path.is_file() else None


def archive_rubric(root: Path, rubric: Rubric) -> str:
    """Keep the shared Rubric's Criteria under its hash; returns the hash."""
    digest = rubric_hash(rubric)
    data = {"criteria": [criterion_dict(c) for c in rubric.criteria]}
    _keep(root / _rubric_file(digest),
          yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return digest


def archived_rubric(root: Path, digest: str) -> Rubric | None:
    file = _rubric_file(digest)
    if not (root / file).is_file():
        return None
    f = FieldReader(file)
    return Rubric(criteria=parse_criteria(f, f.read(root).get("criteria"), "criteria"))


def _rubric_file(digest: str) -> str:
    return f"{ARCHIVE_FOLDER}/{RUBRICS_FOLDER}/{digest}.yaml"


def _keep(path: Path, text: str) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
