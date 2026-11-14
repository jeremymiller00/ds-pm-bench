"""Judging: a Judge Setup scores Outputs blind against the Rubric (ADR-0002, ADR-0003).

config/judge.yaml names the Judge Setup:
  mode       single (one model) or panel (two or more)
  clearance  public_only or public_and_private (ADR-0006)
  prompt     the judge prompt file, relative to the Workspace root
  models     each {provider, model}, and optionally family: the Candidate tools that
             count as this model's family for the self-preference bias flag, and
             env_var: the variable holding its API key (default <PROVIDER>_API_KEY)

Its identity is a content hash of its models and the prompt the Judge sees, so editing
either makes a new Judge Setup. The Judge sees the input, the Rubric, the Gold Answer and
the final Output, never which Candidate produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bench.core.errors import JudgeReplyError
from bench.core.fields import FieldReader
from bench.core.kinds import Clearance
from bench.core.rubric import Criterion, check_criterion_score

JUDGE_FILE = "config/judge.yaml"
JUDGE_FIELDS = ("mode", "clearance", "prompt", "models")
MODEL_FIELDS = ("provider", "model", "family", "env_var")
MODES = ("single", "panel")

# Judges on a panel more than this many points apart on a Criterion disagree (ADR-0002).
PANEL_DISAGREEMENT_POINTS = 1

# The Candidate tools in each provider's family when a model names none.
DEFAULT_FAMILIES: Mapping[str, tuple[str, ...]] = {
    "anthropic": ("anthropic", "claude"),
    "openai": ("openai", "chatgpt", "gpt", "codex"),
    "google": ("google", "gemini"),
}

# Appended to the judge prompt; part of what the Judge Setup hash covers.
REPLY_FORMAT = """\
Reply with JSON only, in this shape, with one entry for every Criterion above:
{"scores": {"<criterion id>": {"score": <whole number 1-5>, "reason": "<one line>"}}}"""


@dataclass(frozen=True)
class JudgeModelSpec:
    """One judge model of a Judge Setup."""

    provider: str
    model: str
    family: tuple[str, ...]  # Candidate tools of the same family, lower case
    env_var: str = ""  # the API key's variable (ADR-0005); not part of the identity

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class JudgeSetup:
    mode: str  # "single" or "panel"
    models: tuple[JudgeModelSpec, ...]
    clearance: Clearance
    prompt_file: str
    prompt: str  # the judge prompt file's text

    @property
    def hash(self) -> str:
        """The identity: a content hash of the models and the prompt the Judge sees."""
        content = {
            "models": [{"provider": m.provider, "model": m.model} for m in self.models],
            "prompt": self.prompt,
            "reply_format": REPLY_FORMAT,
        }
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def snapshot(self) -> dict[str, Any]:
        """What a Run records of the Judge Setup that scored it."""
        return {
            "hash": self.hash,
            "mode": self.mode,
            "models": [{"provider": m.provider, "model": m.model} for m in self.models],
        }


@dataclass(frozen=True)
class JudgeRequest:
    """Everything one Judge sees for one Output; never the Candidate's identity."""

    prompt: str  # the judge prompt
    input: str  # the Canonical Prompt and input the Candidate was given
    criteria: tuple[Criterion, ...]  # the Rubric: shared then task-specific Criteria
    gold_answer: str
    output: str  # the edited final Output

    def render(self) -> str:
        """The whole request as one text, for a model taking a single message."""
        rubric = "\n".join(f"- {c.id} ({c.name}): {c.description}" for c in self.criteria)
        return (
            f"{self.prompt.strip()}\n\n"
            f"## Input\n\n{self.input.strip()}\n\n"
            f"## Rubric\n\n{rubric}\n\n"
            f"## Gold Answer\n\n{self.gold_answer.strip()}\n\n"
            f"## Output\n\n{self.output.strip()}\n\n"
            f"{REPLY_FORMAT}\n"
        )


class JudgeModel(Protocol):
    """A judge model call: returns the model's reply text to a JudgeRequest."""

    def judge(self, request: JudgeRequest) -> str: ...


# What Workspace receives by injection: the JudgeModel for each model of a Judge Setup.
JudgeModels = Callable[[JudgeModelSpec], JudgeModel]


@dataclass(frozen=True)
class CriterionJudgement:
    score: int | float
    reason: str


@dataclass(frozen=True)
class TaskJudging:
    """One Task's judging by a Judge Setup: every Judge's scores and reasons, and the
    median per Criterion that becomes the Task's Criterion scores."""

    task_id: str
    judges: Mapping[str, Mapping[str, CriterionJudgement]]  # label -> Criterion -> it
    same_family: bool  # a Judge shares the Candidate's family

    @property
    def scores(self) -> dict[str, int | float]:
        """The median per Criterion across the Judges."""
        criteria = next(iter(self.judges.values()))
        return {
            cid: statistics.median(j[cid].score for j in self.judges.values())
            for cid in criteria
        }

    @property
    def disagreements(self) -> tuple[str, ...]:
        """Criteria the Judges score more than PANEL_DISAGREEMENT_POINTS apart."""
        criteria = next(iter(self.judges.values()))
        flagged = []
        for cid in criteria:
            values = [j[cid].score for j in self.judges.values()]
            if max(values) - min(values) > PANEL_DISAGREEMENT_POINTS:
                flagged.append(cid)
        return tuple(flagged)

    @property
    def full_human_check(self) -> bool:
        """A disagreement or a same-family Judge means a human checks every Criterion."""
        return self.same_family or bool(self.disagreements)


def load_judge_setup(root: Path) -> JudgeSetup | None:
    """The Judge Setup in config/judge.yaml; None when the file is absent."""
    if not (root / JUDGE_FILE).is_file():
        return None
    f = FieldReader(JUDGE_FILE)
    data = f.read(root)
    f.exact_keys(data, "(file)", JUDGE_FIELDS)
    mode = f.choice(data["mode"], "mode", MODES)
    raw_models = data["models"]
    if not isinstance(raw_models, list) or not raw_models:
        raise f.fail("models", "must be a non-empty list of {provider, model}")
    models = tuple(_model(f, entry, f"models[{i}]") for i, entry in enumerate(raw_models))
    if mode == "single" and len(models) != 1:
        raise f.fail("models", f"a single Judge has exactly one model, got {len(models)}")
    if mode == "panel" and len(models) < 2:
        raise f.fail("models", f"a panel needs at least two models, got {len(models)}")
    labels = [m.label for m in models]
    for i, label in enumerate(labels):
        if label in labels[:i]:
            raise f.fail(f"models[{i}]", f"duplicate model {label}")
    prompt_file = f.text(data["prompt"], "prompt")
    path = root / prompt_file
    if not path.is_file():
        raise f.fail("prompt", f"file {prompt_file} does not exist")
    prompt = path.read_text()
    if not prompt.strip():
        raise f.fail("prompt", f"file {prompt_file} is empty")
    return JudgeSetup(
        mode=mode,
        models=models,
        clearance=f.enum(data["clearance"], "clearance", Clearance),
        prompt_file=prompt_file,
        prompt=prompt,
    )


def _model(f: FieldReader, entry: Any, at: str) -> JudgeModelSpec:
    raw = f.mapping(entry, at)
    for key in raw:
        if key not in MODEL_FIELDS:
            raise f.fail(f"{at}.{key}", f"unknown, expected one of {', '.join(MODEL_FIELDS)}")
    provider = f.text(raw.get("provider"), f"{at}.provider").lower()
    model = f.text(raw.get("model"), f"{at}.model")
    if "family" in raw:
        family = raw["family"]
        if not isinstance(family, list) or not family:
            raise f.fail(f"{at}.family", "must be a non-empty list of Candidate tools")
        names = tuple(f.text(t, f"{at}.family[{i}]").lower() for i, t in enumerate(family))
    else:
        names = DEFAULT_FAMILIES.get(provider, (provider,))
    if "env_var" in raw:
        env_var = f.text(raw["env_var"], f"{at}.env_var")
    else:
        env_var = f"{provider.upper()}_API_KEY"
    return JudgeModelSpec(provider=provider, model=model, family=names, env_var=env_var)


def parse_reply(
    reply: str, criteria: tuple[Criterion, ...], where: str
) -> dict[str, CriterionJudgement]:
    """A Judge's reply as a score and one-line reason per Criterion; JudgeReplyError,
    naming the Judge and Task, if it is malformed."""
    text = _strip_fence(reply)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeReplyError(f"{where}: reply is not JSON ({exc.msg})") from None
    scores = data.get("scores") if isinstance(data, dict) else None
    if not isinstance(scores, dict):
        raise JudgeReplyError(f"{where}: reply has no 'scores' mapping")
    expected = [c.id for c in criteria]
    missing = [cid for cid in expected if cid not in scores]
    unknown = sorted(set(scores) - set(expected))
    if missing or unknown:
        raise JudgeReplyError(
            f"{where}: reply must score exactly the Rubric's Criteria; "
            f"missing {missing}, unknown {unknown}"
        )
    judged = {}
    for cid in expected:
        entry = scores[cid]
        if not isinstance(entry, dict):
            raise JudgeReplyError(f"{where}: {cid} must be {{score, reason}}")
        score = entry.get("score")
        if isinstance(score, float) and score.is_integer():
            score = int(score)
        if not isinstance(score, int):
            raise JudgeReplyError(f"{where}: {cid} score must be a whole number, got {score!r}")
        try:
            check_criterion_score(score, f"{where}: {cid}")
        except ValueError as exc:
            raise JudgeReplyError(str(exc)) from None
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip() or "\n" in reason.strip():
            raise JudgeReplyError(f"{where}: {cid} needs a one-line reason, got {reason!r}")
        judged[cid] = CriterionJudgement(score=score, reason=reason.strip())
    return judged


def _strip_fence(reply: str) -> str:
    """Allow a reply wrapped in one Markdown code fence."""
    m = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n\s*```\s*", reply, re.S)
    return m.group(1) if m else reply


def same_family(spec: JudgeModelSpec, candidate_tool: str) -> bool:
    return candidate_tool.strip().lower() in spec.family


def is_candidate_under_test(
    spec: JudgeModelSpec, candidate_tool: str, candidate_version: str
) -> bool:
    """The same family and the same model id: the Candidate would grade itself."""
    return same_family(spec, candidate_tool) and _norm(spec.model) == _norm(candidate_version)


def _norm(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.strip().lower()).strip("-")
