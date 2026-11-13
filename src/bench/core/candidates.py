"""Candidates: candidates/<tool>@<version>.yaml, each with its Gate record (ADR-0003, ADR-0005).

A Candidate is frozen once a Run uses it; any change is a new file. Config holds
environment variable names only, never secret values.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.fields import FieldReader

CANDIDATES_FOLDER = "candidates"

ADAPTER_KINDS = ("automated", "manual_assisted")
GATE_RESULTS = ("pass", "fail")
CLEARANCES = ("public_only", "public_and_private")

CANDIDATE_FIELDS = ("tool", "version", "adapter", "settings", "env_vars", "baseline", "gate")
GATE_FIELDS = (
    "reviewer",
    "date",
    "retention",
    "sso",
    "enterprise_terms",
    "connector_support",
    "result",
    "clearance",
)

_ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Settings keys that would hold a secret value; these belong in env_vars instead.
_SECRET_KEY = re.compile(r"(^|_)(api_?key|key|secret|password|token|credentials?)$", re.I)


@dataclass(frozen=True)
class Gate:
    reviewer: str
    date: datetime.date
    retention: str
    sso: bool
    enterprise_terms: bool
    connector_support: str
    result: str  # "pass" or "fail"
    clearance: str | None  # "public_only" or "public_and_private"; None when failed


@dataclass(frozen=True)
class Candidate:
    id: str  # the file stem, <tool>@<version>
    tool: str
    version: str
    adapter: str  # "automated" or "manual_assisted"
    settings: dict[str, Any]
    env_vars: tuple[str, ...]
    baseline: bool
    gate: Gate | None  # None until the Gate review is recorded

    @property
    def gate_passed(self) -> bool:
        return self.gate is not None and self.gate.result == "pass"

    @property
    def clearance(self) -> str | None:
        """The Clearance a passing Gate grants; None without one."""
        return self.gate.clearance if self.gate_passed else None


def load_candidates(root: Path) -> tuple[Candidate, ...]:
    folder = root / CANDIDATES_FOLDER
    return tuple(_load(root, path) for path in sorted(folder.glob("*.yaml")))


def _load(root: Path, path: Path) -> Candidate:
    f = FieldReader(f"{CANDIDATES_FOLDER}/{path.name}")
    if "@" not in path.stem.strip("@"):
        raise f.fail("(file)", "must be named <tool>@<version>.yaml")
    data = f.read(root)
    for key in data:
        if key not in CANDIDATE_FIELDS:
            raise f.fail(str(key), f"unknown, expected one of {', '.join(CANDIDATE_FIELDS)}")
    return Candidate(
        id=path.stem,
        tool=f.text(data.get("tool"), "tool"),
        version=f.text(data.get("version"), "version"),
        adapter=f.choice(data.get("adapter"), "adapter", ADAPTER_KINDS),
        settings=_settings(f, data.get("settings", {})),
        env_vars=_env_vars(f, data.get("env_vars", [])),
        baseline=f.boolean(data.get("baseline", False), "baseline"),
        gate=None if data.get("gate") is None else _gate(f, data["gate"]),
    )


def _settings(f: FieldReader, value: Any) -> dict[str, Any]:
    settings = f.mapping(value, "settings")
    for key in settings:
        if _SECRET_KEY.search(str(key)):
            raise f.fail(
                f"settings.{key}",
                "looks like a secret; list its environment variable name in env_vars",
            )
    return settings


def _env_vars(f: FieldReader, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise f.fail("env_vars", "must be a list of environment variable names")
    for i, name in enumerate(value):
        if not isinstance(name, str) or not _ENV_VAR.match(name):
            raise f.fail(
                f"env_vars[{i}]", "must be an environment variable name like API_KEY, not a value"
            )
    return tuple(value)


def _gate(f: FieldReader, value: Any) -> Gate:
    raw = f.mapping(value, "gate")
    for key in raw:
        if key not in GATE_FIELDS:
            raise f.fail(f"gate.{key}", f"unknown, expected one of {', '.join(GATE_FIELDS)}")
    result = f.choice(raw.get("result"), "gate.result", GATE_RESULTS)
    clearance = raw.get("clearance")
    if result == "pass" or clearance is not None:
        clearance = f.choice(clearance, "gate.clearance", CLEARANCES)
    return Gate(
        reviewer=f.text(raw.get("reviewer"), "gate.reviewer"),
        date=f.date(raw.get("date"), "gate.date"),
        retention=f.text(raw.get("retention"), "gate.retention"),
        sso=f.boolean(raw.get("sso"), "gate.sso"),
        enterprise_terms=f.boolean(raw.get("enterprise_terms"), "gate.enterprise_terms"),
        connector_support=f.text(raw.get("connector_support"), "gate.connector_support"),
        result=result,
        clearance=clearance,
    )
