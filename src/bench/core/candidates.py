"""Candidates: candidates/<tool>@<version>.yaml, each with its Gate (ADR-0003, ADR-0006).

A Candidate is frozen once a Run uses it; any change is a new file, the next Candidate
in its Lineage, <tool>@<version>~<n>, and the used file stays as it was so its Runs
still load. The Baseline mark and the Gate are not part of the Candidate, so they
change in place: the Gate is a dated, append-only list of reviews (ADR-0003).
Config holds environment variable names only, never secret values.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.fields import FieldReader
from bench.core.kinds import AdapterKind, Clearance

CANDIDATES_FOLDER = "candidates"

ADAPTER_KINDS = ("automated", "manual_assisted")
GATE_RESULTS = ("pass", "fail")

CANDIDATE_FIELDS = (
    "tool",
    "version",
    "adapter",
    "settings",
    "env_vars",
    "cost_per_task_usd",
    "baseline",
    "gate",
)
# The Gate checklist a reviewer fills in for each review (ADR-0003, the review record);
# a passing review also grants a Clearance (ADR-0006).
GATE_CHECKLIST = ("retention", "sso", "enterprise_terms", "connector_support")
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

# A Candidate after the first in its Lineage: <tool>@<version>~<n>, n >= 2.
_LINEAGE_SUFFIX = re.compile(r"^(?P<lineage>.+)~(?P<n>[2-9]|[1-9][0-9]+)$")
_ID_UNSAFE = re.compile(r"[^a-z0-9._-]+")
_ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Settings keys that would hold a secret value; these belong in env_vars instead.
_SECRET_KEY = re.compile(r"(^|_)(api_?key|key|secret|password|token|credentials?)$", re.I)


@dataclass(frozen=True)
class GateReview:
    """One dated review of a Candidate's Gate. The latest review is the one that applies."""

    reviewer: str
    date: datetime.date
    retention: str
    sso: bool
    enterprise_terms: bool
    connector_support: str
    result: str  # "pass" or "fail"
    clearance: Clearance | None  # None when failed

    @property
    def passed(self) -> bool:
        return self.result == "pass"


@dataclass(frozen=True)
class Candidate:
    id: str  # the file stem, <tool>@<version>
    tool: str
    version: str
    adapter: str  # "automated" or "manual_assisted"
    settings: dict[str, Any]
    env_vars: tuple[str, ...]
    baseline: bool
    # The Gate: every review, oldest first, append-only; empty until the first one.
    gate_reviews: tuple[GateReview, ...]
    # A manual-assisted Candidate's flat cost per Task, such as a subscription's share.
    cost_per_task_usd: float | None = None

    @property
    def lineage(self) -> str:
        """The Lineage: the id of its first Candidate, shared by every Candidate in it."""
        m = _LINEAGE_SUFFIX.match(self.id)
        return m.group("lineage") if m else self.id

    @property
    def lineage_position(self) -> int:
        """Its position in the Lineage: 1 for <tool>@<version>; n for <tool>@<version>~<n>."""
        m = _LINEAGE_SUFFIX.match(self.id)
        return int(m.group("n")) if m else 1

    @property
    def manual_assisted(self) -> bool:
        """Whether a person works its Tasks by hand, pasting each Output back."""
        return self.adapter == AdapterKind.MANUAL_ASSISTED

    @property
    def task_cost_usd(self) -> float | None:
        """The cost to record for each Task a Run of it does by hand.

        The flat per-Task cost of a manual-assisted Candidate; None otherwise, which
        scoring treats as no per-Task cost. Automated cost comes from token prices.
        """
        return self.cost_per_task_usd if self.manual_assisted else None

    @property
    def gate(self) -> GateReview | None:
        """The latest Gate review, which applies; None until one is recorded."""
        return self.gate_reviews[-1] if self.gate_reviews else None

    @property
    def gate_passed(self) -> bool:
        return self.gate is not None and self.gate.passed

    @property
    def clearance(self) -> Clearance | None:
        """The Clearance a passing Gate grants; None without one."""
        return self.gate.clearance if self.gate_passed else None


def load_candidates(root: Path) -> tuple[Candidate, ...]:
    folder = root / CANDIDATES_FOLDER
    candidates = tuple(_load(root, path) for path in sorted(folder.glob("*.yaml")))
    marked = [c.id for c in candidates if c.baseline]
    if candidates and len(marked) != 1:
        raise FieldReader(CANDIDATES_FOLDER).fail(
            "baseline",
            f"exactly one Candidate must be the Baseline, found {len(marked)}"
            + (f" ({', '.join(marked)})" if marked else ""),
        )
    return candidates


def candidate_id_for(tool: str, version: str) -> str:
    """<tool>@<version>, lower-cased, with anything unsafe in a file name made a hyphen."""
    return f"{_slug(tool)}@{_slug(version)}"


def _slug(text: str) -> str:
    return _ID_UNSAFE.sub("-", text.strip().lower()).strip("-")


def write_candidate(root: Path, candidate_id: str, data: dict[str, Any]) -> Candidate:
    """Validate a Candidate's fields and write candidates/<id>.yaml; ConfigError, naming
    the field, writes nothing."""
    path = root / CANDIDATES_FOLDER / f"{candidate_id}.yaml"
    candidate = parse_candidate(FieldReader(f"{CANDIDATES_FOLDER}/{path.name}"), path.stem, data)
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(candidate_data(candidate), sort_keys=False))
    return candidate


def candidate_data(c: Candidate) -> dict[str, Any]:
    """The Candidate as its file holds it."""
    data: dict[str, Any] = {
        "tool": c.tool,
        "version": c.version,
        "adapter": c.adapter,
        "settings": c.settings,
        "env_vars": list(c.env_vars),
    }
    if c.cost_per_task_usd is not None:
        data["cost_per_task_usd"] = c.cost_per_task_usd
    data["baseline"] = c.baseline
    data["gate"] = [_review_data(r) for r in c.gate_reviews]
    return data


def _review_data(r: GateReview) -> dict[str, Any]:
    """A Gate review as a Candidate file holds it."""
    return {
        "reviewer": r.reviewer,
        "date": r.date.isoformat(),
        "retention": r.retention,
        "sso": r.sso,
        "enterprise_terms": r.enterprise_terms,
        "connector_support": r.connector_support,
        "result": r.result,
        "clearance": None if r.clearance is None else str(r.clearance),
    }


def gate_data(
    f: FieldReader,
    *,
    reviewer: str,
    on: datetime.date,
    checklist: dict[str, Any],
    passed: bool,
    clearance: Clearance | str | None,
) -> dict[str, Any]:
    """A Gate record as a Candidate file holds it; the checklist must hold exactly
    GATE_CHECKLIST. Field values are checked when the Candidate is parsed."""
    f.exact_keys(f.mapping(checklist, "gate"), "gate", GATE_CHECKLIST)
    if not passed and clearance is not None:
        raise f.fail("gate.clearance", "a failed Gate grants no Clearance; pass None")
    return {
        "reviewer": reviewer,
        "date": on.isoformat(),
        **{item: checklist[item] for item in GATE_CHECKLIST},
        "result": "pass" if f.boolean(passed, "gate.result") else "fail",
        "clearance": None if clearance is None else str(clearance),
    }


def _load(root: Path, path: Path) -> Candidate:
    f = FieldReader(f"{CANDIDATES_FOLDER}/{path.name}")
    return parse_candidate(f, path.stem, f.read(root))


def parse_candidate(f: FieldReader, candidate_id: str, data: dict[str, Any]) -> Candidate:
    if "@" not in candidate_id.strip("@"):
        raise f.fail("(file)", "must be named <tool>@<version>.yaml")
    for key in data:
        if key not in CANDIDATE_FIELDS:
            raise f.fail(str(key), f"unknown, expected one of {', '.join(CANDIDATE_FIELDS)}")
    adapter = f.choice(data.get("adapter"), "adapter", ADAPTER_KINDS)
    cost = data.get("cost_per_task_usd")
    if cost is not None:
        cost = f.number(cost, "cost_per_task_usd", minimum=0)
        if adapter != "manual_assisted":
            raise f.fail(
                "cost_per_task_usd",
                "only a manual_assisted Candidate has a flat per-Task cost; "
                "automated cost comes from token prices",
            )
    return Candidate(
        id=candidate_id,
        tool=f.text(data.get("tool"), "tool"),
        version=f.text(data.get("version"), "version"),
        adapter=adapter,
        settings=_settings(f, data.get("settings", {})),
        env_vars=_env_vars(f, data.get("env_vars", [])),
        baseline=f.boolean(data.get("baseline", False), "baseline"),
        gate_reviews=_gate_reviews(f, data.get("gate")),
        cost_per_task_usd=cost,
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


def _gate_reviews(f: FieldReader, value: Any) -> tuple[GateReview, ...]:
    """The Gate: a list of reviews, oldest first; a single mapping is read as one review,
    and null or absent as none."""
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(_review(f, item) for item in value)
    return (_review(f, value),)


def _review(f: FieldReader, value: Any) -> GateReview:
    raw = f.mapping(value, "gate")
    for key in raw:
        if key not in GATE_FIELDS:
            raise f.fail(f"gate.{key}", f"unknown, expected one of {', '.join(GATE_FIELDS)}")
    result = f.choice(raw.get("result"), "gate.result", GATE_RESULTS)
    clearance = raw.get("clearance")
    if result == "pass" or clearance is not None:
        clearance = f.enum(clearance, "gate.clearance", Clearance)
    return GateReview(
        reviewer=f.text(raw.get("reviewer"), "gate.reviewer"),
        date=f.date(raw.get("date"), "gate.date"),
        retention=f.text(raw.get("retention"), "gate.retention"),
        sso=f.boolean(raw.get("sso"), "gate.sso"),
        enterprise_terms=f.boolean(raw.get("enterprise_terms"), "gate.enterprise_terms"),
        connector_support=f.text(raw.get("connector_support"), "gate.connector_support"),
        result=result,
        clearance=clearance,
    )
