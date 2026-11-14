"""The Calibration Set: Outputs scored blind by a human expert, used to test the Judge
(ADR-0002, ADR-0006).

calibration/<item>.yaml in the public repo holds numbers only: where the Output came
from (Run, Task, version), its visibility, the Criterion ids, the human scores and each
Judge Setup's scores keyed by its hash. What can quote the Task (input, Gold Answer,
Rubric text, Output, Judge reasons) goes to calibration/content/<item>.yaml and
calibration/judge/<item>.yaml beside it: in the public repo for a Public Task, in its
private root for a Private one. Item ids (c0001, ...) are opaque, so a blind view names
neither the Run nor the Candidate.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bench.core.errors import CalibrationError
from bench.core.judging import JUDGE_FOLDER, BlindOutput, JudgeSetup, TaskJudging, blind_output
from bench.core.kinds import Visibility
from bench.core.lookup import next_number
from bench.core.rubric import Criterion, check_criterion_score, criterion_dict
from bench.core.runs import Run

CALIBRATION_FOLDER = "calibration"
CONTENT_FOLDER = "content"

_ITEM = re.compile(r"^c(\d{4,})\.yaml$")


@dataclass(frozen=True)
class CalibrationItem:
    """An item's numbers: never its content."""

    id: str
    run_id: str
    task_id: str
    task_version: int
    visibility: Visibility
    added: datetime.date
    criteria: tuple[str, ...]  # Criterion ids, in Rubric order
    human_scores: Mapping[str, int] | None  # None until scored blind
    judge_scores: Mapping[str, Mapping[str, int | float]]  # setup hash -> Criterion -> it

    @property
    def human_scored(self) -> bool:
        return self.human_scores is not None


@dataclass(frozen=True)
class CriterionAgreement:
    """How a Judge Setup's scores agree with the human's over the items both scored."""

    items: int  # scores compared
    within_one_point: float  # share of them at most 1 point apart
    weighted_kappa: float | None  # quadratic weighted kappa; None when undefined


@dataclass(frozen=True)
class Agreement:
    """A Judge Setup's agreement with the human scores on the Calibration Set.

    criteria holds each Criterion's agreement and overall pools every Criterion's
    scores. Certification needs every Criterion to meet both thresholds; a Criterion
    whose kappa is undefined (human and Judge gave one same score throughout, which is
    perfect agreement) is held to the within-1-point share only. unscored and unjudged
    list the items still lacking a human score or this Judge Setup's score; they are
    left out of the numbers, and certifying needs neither to be left.
    """

    judge_setup_hash: str
    criteria: Mapping[str, CriterionAgreement]
    overall: CriterionAgreement
    unscored: tuple[str, ...]
    unjudged: tuple[str, ...]
    within_one_point_threshold: float
    weighted_kappa_threshold: float
    min_items: int  # the fewest fully scored items Certification needs
    items: int  # items scored by both the human and this Judge Setup

    @property
    def shortfalls(self) -> tuple[str, ...]:
        """Why this agreement does not certify the Judge Setup; empty when it does."""
        if not self.unscored and not self.unjudged and self.overall.items == 0:
            return ("the Calibration Set is empty",)
        found = []
        if self.items < self.min_items:
            found.append(
                f"the Calibration Set has {self.items} items, fewer than the "
                f"{self.min_items} Certification needs (calibration.min_items)"
            )
        if self.unscored:
            found.append(f"no human score yet for {', '.join(self.unscored)}")
        if self.unjudged:
            found.append(
                f"not scored by Judge Setup {self.judge_setup_hash} yet: "
                f"{', '.join(self.unjudged)}"
            )
        for cid, a in self.criteria.items():
            if a.within_one_point < self.within_one_point_threshold:
                found.append(
                    f"{cid}: {a.within_one_point:.0%} within 1 point, below "
                    f"{self.within_one_point_threshold:.0%}"
                )
            if a.weighted_kappa is not None and a.weighted_kappa < self.weighted_kappa_threshold:
                found.append(
                    f"{cid}: weighted kappa {a.weighted_kappa:.2f}, below "
                    f"{self.weighted_kappa_threshold:.2f}"
                )
        return tuple(found)

    @property
    def meets_thresholds(self) -> bool:
        return not self.shortfalls


def weighted_kappa(human: list[float], judge: list[float]) -> float | None:
    """Quadratic weighted kappa of two raters' scores on a numeric scale.

    1 - observed / expected squared disagreement, the expected one being that of the two
    raters' scores paired at random; equal to Cohen's quadratic weighted kappa over the
    1-5 categories, and defined for a Panel's half-point medians too. None when chance
    disagreement is zero (both raters gave one score throughout).
    """
    n = len(human)
    if n == 0:
        return None
    observed = sum((h - j) ** 2 for h, j in zip(human, judge, strict=True)) / n
    mean_h, mean_j = sum(human) / n, sum(judge) / n
    var_h = sum((h - mean_h) ** 2 for h in human) / n
    var_j = sum((j - mean_j) ** 2 for j in judge) / n
    expected = var_h + var_j + (mean_h - mean_j) ** 2
    if expected == 0:
        return None
    return 1 - observed / expected


def criterion_agreement(human: list[float], judge: list[float]) -> CriterionAgreement:
    n = len(human)
    within = sum(abs(h - j) <= 1 for h, j in zip(human, judge, strict=True))
    return CriterionAgreement(
        items=n,
        within_one_point=within / n if n else 0.0,
        weighted_kappa=weighted_kappa(human, judge),
    )


@dataclass(frozen=True)
class CalibrationSet:
    root: Path  # the Workspace root
    private_roots: tuple[Path, ...]

    @property
    def folder(self) -> Path:
        return self.root / CALIBRATION_FOLDER

    def add(self, run: Run, task_id: str, on: datetime.date) -> CalibrationItem:
        """Add a finished Run's final Output of a Task; CalibrationError if the Run is
        unfinished or the Output is already in the set."""
        task = run.task(task_id)
        if run.pending:
            raise CalibrationError(
                f"Run {run.id} is not finished: {', '.join(run.pending)} still pending"
            )
        for item in self.items():
            if item.run_id == run.id and item.task_id == task.id:
                raise CalibrationError(
                    f"Run {run.id}'s Output of Task {task.id} is already calibration "
                    f"item {item.id}"
                )
        self.folder.mkdir(exist_ok=True)
        item_id = f"c{next_number(self.folder, _ITEM):04d}"
        content_root = task.root if task.visibility == Visibility.PRIVATE else self.root
        blind = blind_output(item_id, task, run.final_output(task.id))
        content = {
            "input": blind.input,
            "criteria": [criterion_dict(c) for c in blind.criteria],
            "gold_answer": blind.gold_answer,
            "output": blind.output,
        }
        content_path = _content_folder(content_root) / f"{item_id}.yaml"
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(yaml.safe_dump(content, sort_keys=False))
        record = {
            "id": item_id,
            "added": on.isoformat(),
            "run": run.id,
            "task": task.id,
            "version": task.version,
            "visibility": str(task.visibility),
            "criteria": [c.id for c in task.rubric.criteria],
            "human_scores": None,
            "judge": {},
        }
        with (self.folder / f"{item_id}.yaml").open("x") as file:
            yaml.safe_dump(record, file, sort_keys=False)
        return self.item(item_id)

    def items(self) -> tuple[CalibrationItem, ...]:
        """Every item, oldest first."""
        if not self.folder.is_dir():
            return ()
        paths = sorted(
            (p for p in self.folder.iterdir() if _ITEM.match(p.name)),
            key=lambda p: int(_ITEM.match(p.name).group(1)),
        )
        return tuple(_item(yaml.safe_load(p.read_text())) for p in paths)

    def item(self, item_id: str) -> CalibrationItem:
        return _item(self._record(item_id))

    def blind(self, item_id: str) -> BlindOutput:
        item = self.item(item_id)
        content = yaml.safe_load(self._content_path(item).read_text())
        return BlindOutput(
            id=item.id,
            input=content["input"],
            criteria=tuple(Criterion(**c) for c in content["criteria"]),
            gold_answer=content["gold_answer"],
            output=content["output"],
        )

    def record_human_scores(self, item_id: str, scores: Mapping[str, Any]) -> CalibrationItem:
        """Save the human's blind 1-5 score of every Criterion of the item."""
        record = self._record(item_id)
        _check_scores(item_id, record["criteria"], scores)
        record["human_scores"] = dict(scores)
        self._write(record)
        return _item(record)

    def record_judging(self, item_id: str, judging: TaskJudging, setup: JudgeSetup) -> None:
        """Save a Judge Setup's scoring of the item: the median per Criterion publicly,
        every judge model's score and reason beside the content."""
        record = self._record(item_id)
        record["judge"][setup.hash] = dict(judging.scores)
        self._write(record)
        item = _item(record)
        path = self._content_path(item).parent.parent / JUDGE_FOLDER / f"{item.id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        reasons = yaml.safe_load(path.read_text()) if path.is_file() else {}
        reasons[setup.hash] = {
            label: {cid: {"score": j.score, "reason": j.reason} for cid, j in scores.items()}
            for label, scores in judging.judges.items()
        }
        path.write_text(yaml.safe_dump(reasons, sort_keys=False))

    def agreement(
        self, setup_hash: str, within_one_point: float, kappa: float, min_items: int
    ) -> Agreement:
        """The Judge Setup's agreement with the human scores, with the thresholds."""
        pairs: dict[str, tuple[list[float], list[float]]] = {}
        unscored, unjudged = [], []
        compared = 0
        for item in self.items():
            judged = item.judge_scores.get(setup_hash)
            if item.human_scores is None:
                unscored.append(item.id)
            if judged is None:
                unjudged.append(item.id)
            if item.human_scores is None or judged is None:
                continue
            compared += 1
            for cid in item.criteria:
                human, judge = pairs.setdefault(cid, ([], []))
                human.append(item.human_scores[cid])
                judge.append(judged[cid])
        all_human = [h for human, _ in pairs.values() for h in human]
        all_judge = [j for _, judge in pairs.values() for j in judge]
        return Agreement(
            judge_setup_hash=setup_hash,
            criteria={cid: criterion_agreement(h, j) for cid, (h, j) in pairs.items()},
            overall=criterion_agreement(all_human, all_judge),
            unscored=tuple(unscored),
            unjudged=tuple(unjudged),
            within_one_point_threshold=within_one_point,
            weighted_kappa_threshold=kappa,
            min_items=min_items,
            items=compared,
        )

    def _record(self, item_id: str) -> dict[str, Any]:
        path = self.folder / f"{item_id}.yaml"
        if not _ITEM.match(path.name) or not path.is_file():
            raise CalibrationError(f"no calibration item {item_id!r} in {CALIBRATION_FOLDER}/")
        return yaml.safe_load(path.read_text())

    def _write(self, record: dict[str, Any]) -> None:
        path = self.folder / f"{record['id']}.yaml"
        path.write_text(yaml.safe_dump(record, sort_keys=False))

    def _content_path(self, item: CalibrationItem) -> Path:
        if item.visibility == Visibility.PUBLIC:
            return _content_folder(self.root) / f"{item.id}.yaml"
        for root in self.private_roots:
            path = _content_folder(root) / f"{item.id}.yaml"
            if path.is_file():
                return path
        raise CalibrationError(
            f"calibration item {item.id} is private and its content is in none of the "
            "configured private roots"
        )


def _content_folder(root: Path) -> Path:
    return root / CALIBRATION_FOLDER / CONTENT_FOLDER


def _check_scores(item_id: str, criteria: list[str], scores: Mapping[str, Any]) -> None:
    missing = [c for c in criteria if c not in scores]
    unknown = sorted(set(scores) - set(criteria))
    if missing or unknown:
        raise CalibrationError(
            f"calibration item {item_id}: score exactly its Criteria; "
            f"missing {missing}, unknown {unknown}"
        )
    for cid in criteria:
        value = scores[cid]
        if isinstance(value, bool) or not isinstance(value, int):
            raise CalibrationError(
                f"calibration item {item_id}: {cid} must be a whole number, got {value!r}"
            )
        try:
            check_criterion_score(value, f"calibration item {item_id}: {cid}")
        except ValueError as exc:
            raise CalibrationError(str(exc)) from None


def _item(record: dict[str, Any]) -> CalibrationItem:
    return CalibrationItem(
        id=record["id"],
        run_id=record["run"],
        task_id=record["task"],
        task_version=record["version"],
        visibility=Visibility(record["visibility"]),
        added=datetime.date.fromisoformat(record["added"]),
        criteria=tuple(record["criteria"]),
        human_scores=record["human_scores"],
        judge_scores=record.get("judge") or {},
    )
