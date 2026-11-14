"""Judging: a Judge Setup scores Outputs blind against the Rubric (ADR-0002, ADR-0003).

config/judge.yaml names the Judge Setup:
  mode       single (one judge model) or panel (a Panel of two or more)
  clearance  public_only or public_and_private (ADR-0006)
  prompt     the judge prompt file, relative to the Workspace root
  models     each {provider, model}, and optionally family: the Candidate tools that
             count as this model's family for the self-preference bias flag (default:
             the provider's tools in bench.core.provider_registry), and env_var: the
             variable holding its API key (default <PROVIDER>_API_KEY)

Its identity is a content hash of its models and the prompt the Judge sees, so editing
either makes a new Judge Setup. The Judge sees the input, the Rubric, the Gold Answer and
the final Output, never which Candidate produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.core.errors import JudgeClearanceError, JudgeReplyError
from bench.core.fields import FieldReader
from bench.core.kinds import Clearance, JudgeMode, Provider, Visibility
from bench.core.provider_registry import family_of
from bench.core.rubric import Criterion, check_criterion_score
from bench.core.tasks import Task

JUDGE_FILE = "config/judge.yaml"
# The folder judge reasons go in, beside a Run's Outputs or the Calibration Set's content.
JUDGE_FOLDER = "judge"
JUDGE_FIELDS = ("mode", "clearance", "prompt", "models")
MODEL_FIELDS = ("provider", "model", "family", "env_var")

# Judge models on a Panel more than this many points apart on a Criterion disagree
# (ADR-0002).
PANEL_DISAGREEMENT_POINTS = 1

# Appended to the judge prompt; part of what the Judge Setup hash covers.
REPLY_FORMAT = """\
Reply with JSON only, in this shape, with one entry for every Criterion above:
{"scores": {"<criterion id>": {"score": <whole number 1-5>, "reason": "<one line>"}}}"""


@dataclass(frozen=True)
class JudgeModelSpec:
    """One judge model of a Judge Setup."""

    provider: Provider
    model: str
    family: tuple[str, ...]  # Candidate tools of the same family, lower case
    env_var: str = ""  # the API key's variable (ADR-0005); not part of the identity

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class JudgeSetup:
    mode: JudgeMode
    models: tuple[JudgeModelSpec, ...]
    clearance: Clearance
    prompt_file: str
    prompt: str  # the judge prompt file's text

    @property
    def hash(self) -> str:
        """The identity: a content hash of the models and the prompt the Judge sees."""
        content = {
            "models": self._model_identities(),
            "prompt": self.prompt,
            "reply_format": REPLY_FORMAT,
        }
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def snapshot(self) -> dict[str, Any]:
        """What a Run records of the Judge Setup that scored it."""
        return {
            "hash": self.hash,
            "mode": str(self.mode),
            "models": self._model_identities(),
        }

    def _model_identities(self) -> list[dict[str, str]]:
        """The judge models as the hash and a Run's snapshot both record them."""
        return [{"provider": str(m.provider), "model": m.model} for m in self.models]


@dataclass(frozen=True)
class BlindOutput:
    """An Output as a blind scorer sees it, human or Judge: the Canonical Prompt and
    input, the Criteria to score, the Gold Answer and the final Output; never the Judge's
    scores, the Run or the Candidate. id is the Task id in a Run, or the opaque item id
    in the Calibration Set."""

    id: str
    input: str
    criteria: tuple[Criterion, ...]
    gold_answer: str
    output: str


def blind_output(
    output_id: str, task: Task, output: str, criteria: tuple[Criterion, ...] | None = None
) -> BlindOutput:
    """The one builder of what a blind scorer sees of a Task's Output: the same text for
    judging a Run, a Calibration Set item and a Spot-check. criteria defaults to the
    Task's whole Rubric. The caller checks Clearance before calling."""
    return BlindOutput(
        id=output_id,
        input=f"{task.canonical_prompt}\n\n{task.input_path.read_text().strip()}",
        criteria=task.rubric.criteria if criteria is None else criteria,
        gold_answer=task.gold_answer_path.read_text().strip(),
        output=output,
    )


def check_judge_clearance(setup: JudgeSetup, visibility: Visibility, what: str) -> None:
    """The one Private-Task Clearance check for judging: JudgeClearanceError for private
    content and a Judge Setup not cleared for it (ADR-0006). what names the content."""
    if visibility == Visibility.PRIVATE and setup.clearance != Clearance.PUBLIC_AND_PRIVATE:
        raise JudgeClearanceError(
            f"{what} is private and Judge Setup {setup.hash} is cleared for {setup.clearance}"
        )


@dataclass(frozen=True)
class JudgeRequest:
    """Everything a judge model sees for one Output; never the Candidate's identity."""

    prompt: str  # the judge prompt
    input: str  # the Canonical Prompt and input the Candidate was given
    criteria: tuple[Criterion, ...]  # the Rubric: shared then task-specific Criteria
    gold_answer: str
    output: str  # the edited final Output

    @classmethod
    def for_output(cls, prompt: str, blind: BlindOutput) -> JudgeRequest:
        return cls(prompt=prompt, input=blind.input, criteria=blind.criteria,
                   gold_answer=blind.gold_answer, output=blind.output)

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


@dataclass(frozen=True)
class CriterionJudgement:
    score: int | float
    reason: str


@dataclass(frozen=True)
class TaskJudging:
    """One Output's judging by a Judge Setup: every judge model's scores and reasons, and
    the median per Criterion that becomes its Criterion scores."""

    output_id: str  # BlindOutput.id: the Task id in a Run, the item id in the Calibration Set
    judges: Mapping[str, Mapping[str, CriterionJudgement]]  # label -> Criterion -> it
    same_family: bool  # a judge model shares the Candidate's family

    @property
    def scores(self) -> dict[str, int | float]:
        """The median per Criterion across the judge models."""
        criteria = next(iter(self.judges.values()))
        return {
            cid: statistics.median(j[cid].score for j in self.judges.values())
            for cid in criteria
        }

    @property
    def disagreements(self) -> tuple[str, ...]:
        """Criteria the judge models score more than PANEL_DISAGREEMENT_POINTS apart."""
        criteria = next(iter(self.judges.values()))
        flagged = []
        for cid in criteria:
            values = [j[cid].score for j in self.judges.values()]
            if max(values) - min(values) > PANEL_DISAGREEMENT_POINTS:
                flagged.append(cid)
        return tuple(flagged)

    @property
    def full_human_check(self) -> bool:
        """A disagreement or a same-family judge model means a human checks every
        Criterion."""
        return self.same_family or bool(self.disagreements)


def load_judge_setup(root: Path) -> JudgeSetup | None:
    """The Judge Setup in config/judge.yaml; None when the file is absent."""
    if not (root / JUDGE_FILE).is_file():
        return None
    f = FieldReader(JUDGE_FILE)
    return _parse(f, root, f.read(root), prompt=None)


def edit_judge_setup(
    root: Path, changes: Mapping[str, Any], prompt: str | None = None
) -> JudgeSetup:
    """Apply changes (mode, clearance, prompt: the prompt file's path, models) to
    config/judge.yaml, starting from an empty one when it is absent, and write it back;
    prompt, if given, is the prompt file's new text. Everything is validated before
    anything is written: an invalid change raises ConfigError naming the field."""
    f = FieldReader(JUDGE_FILE)
    data = f.read(root) if (root / JUDGE_FILE).is_file() else {}
    for key in changes:
        if key not in JUDGE_FIELDS:
            raise f.fail(key, f"unknown, expected one of {', '.join(JUDGE_FIELDS)}")
    data = {**data, **changes}
    setup = _parse(f, root, data, prompt=prompt)
    if prompt is not None:
        path = (root / setup.prompt_file).resolve()
        if not path.is_relative_to(root.resolve()):
            raise f.fail("prompt", f"{setup.prompt_file} is outside the Workspace root")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt)
    f.write(root, data)
    return setup


def _parse(
    f: FieldReader, root: Path, data: dict[str, Any], *, prompt: str | None
) -> JudgeSetup:
    """The Judge Setup data describes; prompt, when given, is the prompt file's text to
    be, else the file is read."""
    f.exact_keys(data, "(file)", JUDGE_FIELDS)
    mode = f.enum(data["mode"], "mode", JudgeMode)
    raw_models = data["models"]
    if not isinstance(raw_models, list) or not raw_models:
        raise f.fail("models", "must be a non-empty list of {provider, model}")
    models = tuple(_model(f, entry, f"models[{i}]") for i, entry in enumerate(raw_models))
    if mode == JudgeMode.SINGLE and len(models) != 1:
        raise f.fail("models", f"a single Judge has exactly one model, got {len(models)}")
    if mode == JudgeMode.PANEL and len(models) < 2:
        raise f.fail("models", f"a Panel needs at least two models, got {len(models)}")
    labels = [m.label for m in models]
    for i, label in enumerate(labels):
        if label in labels[:i]:
            raise f.fail(f"models[{i}]", f"duplicate model {label}")
    prompt_file = f.text(data["prompt"], "prompt")
    if prompt is None:
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
    provider = f.enum(f.text(raw.get("provider"), f"{at}.provider").lower(),
                      f"{at}.provider", Provider)
    model = f.text(raw.get("model"), f"{at}.model")
    if "family" in raw:
        family = raw["family"]
        if not isinstance(family, list) or not family:
            raise f.fail(f"{at}.family", "must be a non-empty list of Candidate tools")
        names = tuple(f.text(t, f"{at}.family[{i}]").lower() for i, t in enumerate(family))
    else:
        names = family_of(provider)
    if "env_var" in raw:
        env_var = f.text(raw["env_var"], f"{at}.env_var")
    else:
        env_var = f"{provider.upper()}_API_KEY"
    return JudgeModelSpec(provider=provider, model=model, family=names, env_var=env_var)


def parse_reply(
    reply: str, criteria: tuple[Criterion, ...], where: str
) -> dict[str, CriterionJudgement]:
    """A judge model's reply as a score and one-line reason per Criterion;
    JudgeReplyError, naming the judge model and Task, if it is malformed."""
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
    spec: JudgeModelSpec, candidate_provider: Provider | None, candidate_model: str
) -> bool:
    """The judge model is the model the Candidate calls: the same provider and model id.

    candidate_provider and candidate_model are what the Candidate actually calls
    (bench.core.candidates.provider_for and model_for).
    """
    return spec.provider == candidate_provider and _norm(spec.model) == _norm(candidate_model)


def _norm(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.strip().lower()).strip("-")
