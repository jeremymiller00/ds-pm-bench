"""Runs: one Candidate against a Suite of Tasks on one date (ADR-0003, ADR-0006).

runs/<date>-<candidate>/ in the public repo holds:
  run.yaml              the snapshot: Candidate, Task versions, date, Suite, the
                        Tasks left out of the Suite as placeholders (excluded), and
                        started: the Run's place in the order Runs were started
  results/<task>.yaml   numbers only per Task (timings, cost, Edit Distance, Criterion
                        scores, Spot-check human scores), with the Task's visibility
  raw/<task>.md         raw Output       } Public Tasks only; for a Private Task these
  final/<task>.md       edited final     } go under <private root>/runs/<run id>/
  judge/<task>.yaml     Judge reasons    }

run.yaml also gains judge_setup (hash, mode, models) when the Run is first judged.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bench.core.candidates import Candidate
from bench.core.errors import (
    ClearanceError,
    GateNotPassed,
    JudgeSetupMismatch,
    RunExists,
    RunIncomplete,
    RunMismatch,
    SuiteUnavailable,
    UnknownRunError,
    UnknownTaskError,
)
from bench.core.fields import FieldReader
from bench.core.judging import JUDGE_FOLDER, JudgeSetup, TaskJudging
from bench.core.kinds import Clearance, Suite, Visibility
from bench.core.lookup import find_by_id
from bench.core.results import TaskResult
from bench.core.rubric import check_criterion_score
from bench.core.tasks import Task, TaskContext, load_version

RUNS_FOLDER = "runs"
RUN_FILE = "run.yaml"
RESULTS_FOLDER = "results"
# run.yaml key: the Spot-check queue, Task id -> Track, saved once the Run is judged.
SPOT_CHECK_QUEUE = "spot_check_queue"

# What a results file (the input to record_from_file) may hold.
RESULTS_FILE_FIELDS = (
    "raw",
    "final",
    "active_seconds",
    "latency_seconds",
    "cost_usd",
    "criterion_scores",
)


@dataclass(frozen=True)
class Run:
    id: str
    candidate: Candidate
    date: datetime.date
    suite: Suite
    tasks: tuple[Task, ...]  # the Suite, at the versions this Run uses
    folder: Path  # runs/<id>/ in the public repo
    # Tasks the Suite would cover but left out because they still hold a placeholder:
    # Task id -> what is a placeholder ("input", "Gold Answer", "Manual Time",
    # "Canonical Prompt").
    excluded: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Its place in the order Runs were started (1 for the first); 0 for a Run made
    # before the order was recorded, which counts as started before every other.
    started: int = 0

    @property
    def candidate_id(self) -> str:
        return self.candidate.id

    @property
    def task_versions(self) -> dict[str, int]:
        return {t.id: t.version for t in self.tasks}

    def task(self, task_id: str) -> Task:
        task = find_by_id(self.tasks, task_id)
        if task is None:
            raise UnknownTaskError(f"Task {task_id!r} is not in Run {self.id}")
        return task

    def task_input(self, task_id: str) -> str:
        """What is sent to (or shown for) the Candidate: the Canonical Prompt, then the input.

        Only for a Task in the Suite; Workspace.task_input tells a Task the Candidate is
        not cleared for apart from one that does not exist.
        """
        task = self.task(task_id)
        check_clearance(self.candidate, task)  # before any content is read
        return f"{task.canonical_prompt}\n\n{task.input_path.read_text().strip()}"

    def content_folder(self, task: Task) -> Path:
        """Where a Task's content-bearing files go: the private root for a Private Task."""
        if task.visibility == Visibility.PRIVATE:
            return task.root / RUNS_FOLDER / self.id
        return self.folder

    def record_output(
        self,
        task_id: str,
        *,
        raw: str,
        final: str,
        active_seconds: float,
        latency_seconds: float,
        cost_usd: float | None,
        call: ProviderCall | None = None,
    ) -> OutputRecord:
        """Save a Task's raw Output and edited final with its timings and cost.

        active_seconds is the PM's active time on the Task; latency_seconds is the time
        to the raw Output. call is an automated Candidate's provider call (model, tokens,
        tries, why its cost is unknown), kept with the numbers.
        """
        task = self.task(task_id)
        if self._task_record(task.id) is not None:
            raise ValueError(f"Run {self.id}: Task {task.id}'s Output is already recorded")
        record = OutputRecord(
            task_id=task.id,
            edit_distance=edit_distance(raw, final),
            active_seconds=active_seconds,
            latency_seconds=latency_seconds,
            cost_usd=cost_usd,
            call=call,
        )
        content = self.content_folder(task)
        for name, text in (("raw", raw), ("final", final)):
            (content / name).mkdir(parents=True, exist_ok=True)
            (content / name / f"{task.id}.md").write_text(text)
        task_record = {
            "task": task.id,
            "version": task.version,
            "visibility": str(task.visibility),
            "edit_distance": record.edit_distance,
            "active_seconds": active_seconds,
            "latency_seconds": latency_seconds,
            "cost_usd": cost_usd,
        }
        if call is not None:
            task_record.update(asdict(call))
        self._write_task_record(task.id, task_record)
        return record

    def record_criterion_scores(self, task_id: str, scores: Mapping[str, float]) -> None:
        """Save the Judge's 1-5 Criterion scores for a Task whose Output is recorded."""
        task = self.task(task_id)
        task_record = self._task_record(task.id)
        if task_record is None:
            raise ValueError(f"Run {self.id}: Task {task.id} has no Output to score yet")
        expected = {c.id for c in task.rubric.criteria}
        if set(scores) != expected:
            missing = sorted(expected - set(scores))
            extra = sorted(set(scores) - expected)
            raise ValueError(
                f"Task {task.id}: Criterion scores must match its Rubric; "
                f"missing {missing}, unknown {extra}"
            )
        for cid, value in scores.items():
            check_criterion_score(value, f"Task {task.id}: {cid}")
        task_record["criterion_scores"] = dict(scores)
        self._write_task_record(task.id, task_record)

    @property
    def judge_setup_hash(self) -> str | None:
        """The hash of the Judge Setup that scored this Run; None until it is judged."""
        return (self._snapshot().get("judge_setup") or {}).get("hash")

    def check_judge_setup(self, setup: JudgeSetup) -> None:
        """A Run is scored by one Judge Setup: JudgeSetupMismatch for any other than the
        one that first judged it."""
        if self.judge_setup_hash not in (None, setup.hash):
            raise JudgeSetupMismatch(
                f"Run {self.id} was judged by Judge Setup {self.judge_setup_hash}, not "
                f"{setup.hash}; a Run is scored by one Judge Setup"
            )

    @property
    def unjudged(self) -> tuple[str, ...]:
        """Task ids whose Output is recorded but not yet scored, in Suite order."""
        return tuple(
            t.id for t in self.tasks
            if (r := self._task_record(t.id)) is not None and "criterion_scores" not in r
        )

    def final_output(self, task_id: str) -> str:
        """The edited final Output of a Task whose Output is recorded."""
        task = self.task(task_id)
        return (self.content_folder(task) / "final" / f"{task.id}.md").read_text()

    def record_judging(self, judging: TaskJudging, setup: JudgeSetup) -> None:
        """Save a Judge Setup's scoring of a Task: the median per Criterion as its
        Criterion scores, and every judge model's scores. Reasons go beside the Outputs, so a
        Private Task's stay in its private root (ADR-0006).

        A Run is scored by one Judge Setup: the first records its hash in run.yaml and
        any other is refused (JudgeSetupMismatch).
        """
        self.check_judge_setup(setup)
        snapshot = self._snapshot()
        recorded = snapshot.get("judge_setup")
        task = self.task(judging.output_id)
        self.record_criterion_scores(task.id, judging.scores)
        if recorded is None:
            snapshot["judge_setup"] = setup.snapshot()
            self._write_snapshot(snapshot)
        task_record = self._task_record(task.id)
        task_record["judge"] = {
            "setup": setup.hash,
            "scores": {
                label: {cid: j.score for cid, j in scores.items()}
                for label, scores in judging.judges.items()
            },
            "disagreements": list(judging.disagreements),
            "same_family": judging.same_family,
            "full_human_check": judging.full_human_check,
        }
        self._write_task_record(task.id, task_record)
        folder = self.content_folder(task) / JUDGE_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        reasons = {
            label: {cid: {"score": j.score, "reason": j.reason} for cid, j in scores.items()}
            for label, scores in judging.judges.items()
        }
        (folder / f"{task.id}.yaml").write_text(yaml.safe_dump(reasons, sort_keys=False))

    @property
    def full_human_checks(self) -> tuple[str, ...]:
        """Task ids the Judge marked for a full human check: a same-family judge model or a
        panel disagreement."""
        return tuple(
            t.id for t in self.tasks
            if ((self._task_record(t.id) or {}).get("judge") or {}).get("full_human_check")
        )

    def judge_reasons(self, task_id: str) -> dict[str, Any]:
        """Each judge model's score and reason per Criterion: label -> Criterion -> both."""
        task = self.task(task_id)
        path = self.content_folder(task) / JUDGE_FOLDER / f"{task.id}.yaml"
        return yaml.safe_load(path.read_text()) if path.is_file() else {}

    def _snapshot(self) -> dict[str, Any]:
        return yaml.safe_load((self.folder / RUN_FILE).read_text())

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        (self.folder / RUN_FILE).write_text(yaml.safe_dump(snapshot, sort_keys=False))

    def record_from_file(self, path: Path) -> None:
        """Record one Task's Output, and its Criterion scores if present, from a results file.

        The file is <task id>.yaml holding raw, final, active_seconds, latency_seconds,
        cost_usd (null or absent when there is no per-Task cost) and, optionally,
        criterion_scores.
        """
        task_id = path.stem
        f = FieldReader(str(path))
        data = f.mapping(_load_yaml(f, path), "(file)")
        for key in data:
            if key not in RESULTS_FILE_FIELDS:
                raise f.fail(str(key), f"unknown, expected one of {', '.join(RESULTS_FILE_FIELDS)}")
        for key in ("raw", "final", "active_seconds", "latency_seconds"):
            if key not in data:
                raise f.fail(key, "is missing")
        self.record_output(
            task_id,
            raw=str(data["raw"]),
            final=str(data["final"]),
            active_seconds=f.number(data["active_seconds"], "active_seconds", minimum=0),
            latency_seconds=f.number(data["latency_seconds"], "latency_seconds", minimum=0),
            cost_usd=(
                None
                if data.get("cost_usd") is None
                else f.number(data["cost_usd"], "cost_usd", minimum=0)
            ),
        )
        if "criterion_scores" in data:
            scores = f.mapping(data["criterion_scores"], "criterion_scores")
            self.record_criterion_scores(task_id, scores)

    def record_from_folder(self, folder: Path) -> tuple[str, ...]:
        """Record every pending Task that has a <task id>.yaml in folder; returns their ids."""
        recorded = []
        for task_id in self.pending:
            path = folder / f"{task_id}.yaml"
            if path.is_file():
                self.record_from_file(path)
                recorded.append(task_id)
        return tuple(recorded)

    def record_spot_check(self, task_id: str, criterion: str, human_score: float) -> SpotCheck:
        """Record a human's 1-5 score of one Criterion the Judge already scored.

        Recording the same Task and Criterion again replaces the earlier human score.
        """
        task = self.task(task_id)
        task_record = self._task_record(task.id)
        if task_record is None or "criterion_scores" not in task_record:
            raise ValueError(f"Run {self.id}: Task {task.id} has no Criterion scores to check")
        if criterion not in task_record["criterion_scores"]:
            raise ValueError(f"Task {task.id}: {criterion!r} is not a Criterion of its Rubric")
        check_criterion_score(human_score, f"Task {task.id}: Spot-check {criterion}")
        task_record.setdefault("spot_checks", {})[criterion] = human_score
        self._write_task_record(task.id, task_record)
        return SpotCheck(
            run_id=self.id,
            task_id=task.id,
            criterion=criterion,
            judge_score=task_record["criterion_scores"][criterion],
            human_score=human_score,
        )

    @property
    def spot_check_queue(self) -> tuple[str, ...]:
        """Task ids queued for a human Spot-check, in Suite order; empty until judged."""
        return tuple(self._snapshot().get(SPOT_CHECK_QUEUE) or {})

    def queue_spot_checks(self, task_ids: tuple[str, ...]) -> None:
        """Save the Spot-check queue with each Task's Track. Set once: a Run that already
        has one keeps it, so the queue is never resampled."""
        snapshot = self._snapshot()
        if snapshot.get(SPOT_CHECK_QUEUE) is not None:
            return
        snapshot[SPOT_CHECK_QUEUE] = {t: self.task(t).track for t in task_ids}
        self._write_snapshot(snapshot)

    @property
    def spot_checks_left(self) -> dict[str, tuple[str, ...]]:
        """Queued Task id -> the Criteria a human has not scored yet, for Tasks with any
        left. Blind: holds no Judge scores."""
        left = {}
        for task_id in self.spot_check_queue:
            task = self.task(task_id)
            checked = (self._task_record(task_id) or {}).get("spot_checks", {})
            criteria = tuple(c.id for c in task.rubric.criteria if c.id not in checked)
            if criteria:
                left[task_id] = criteria
        return left

    @property
    def spot_checks(self) -> tuple[SpotCheck, ...]:
        """Every Spot-check recorded on this Run, in Suite order."""
        checks = []
        for task in self.tasks:
            task_record = self._task_record(task.id) or {}
            for criterion, human_score in task_record.get("spot_checks", {}).items():
                checks.append(
                    SpotCheck(
                        run_id=self.id,
                        task_id=task.id,
                        criterion=criterion,
                        judge_score=task_record["criterion_scores"][criterion],
                        human_score=human_score,
                    )
                )
        return tuple(checks)

    @property
    def complete(self) -> bool:
        """Every Task in the Suite has its Output and Criterion scores recorded."""
        return all(
            "criterion_scores" in (self._task_record(t.id) or {}) for t in self.tasks
        )

    @property
    def progress(self) -> tuple[int, int]:
        """(Tasks whose Output is recorded, Tasks in the Suite)."""
        return len(self.tasks) - len(self.pending), len(self.tasks)

    @property
    def pending(self) -> tuple[str, ...]:
        """Task ids in the Suite whose Output is not recorded yet, in Suite order."""
        return tuple(t.id for t in self.tasks if self._task_record(t.id) is None)

    def task_results(self) -> tuple[TaskResult, ...]:
        """One TaskResult per Task, for Workspace.score. Refuses an incomplete Run."""
        missing = []
        for task in self.tasks:
            task_record = self._task_record(task.id)
            if task_record is None:
                missing.append(f"{task.id} has no Output")
            elif "criterion_scores" not in task_record:
                missing.append(f"{task.id} has no Criterion scores")
        if missing:
            raise RunIncomplete(f"Run {self.id} is incomplete: {'; '.join(missing)}")
        results = []
        for task in self.tasks:
            n = self._task_record(task.id)
            results.append(
                TaskResult(
                    task_id=task.id,
                    track=task.track,
                    criterion_scores=n["criterion_scores"],
                    edit_distance=n["edit_distance"],
                    active_seconds=n["active_seconds"],
                    manual_minutes=task.manual_time_minutes,
                    cost_usd=n["cost_usd"],
                    latency_seconds=n["latency_seconds"],
                )
            )
        return tuple(results)

    def _task_record(self, task_id: str) -> dict[str, Any] | None:
        """The Task's results/<task>.yaml (numbers only), or None before its Output."""
        path = self._results_file(task_id)
        return yaml.safe_load(path.read_text()) if path.is_file() else None

    def _write_task_record(self, task_id: str, task_record: dict[str, Any]) -> None:
        (self.folder / RESULTS_FOLDER).mkdir(exist_ok=True)
        self._results_file(task_id).write_text(yaml.safe_dump(task_record, sort_keys=False))

    def _results_file(self, task_id: str) -> Path:
        return self.folder / RESULTS_FOLDER / f"{task_id}.yaml"


@dataclass(frozen=True)
class SpotCheck:
    """A human's score of one Criterion beside the Judge's, on one Task of one Run."""

    run_id: str
    task_id: str
    criterion: str
    judge_score: float
    human_score: float

    @property
    def gap(self) -> float:
        """How many points apart the Judge and the human are."""
        return abs(self.judge_score - self.human_score)


@dataclass(frozen=True)
class ProviderCall:
    """What a Task Result keeps of an automated Candidate's provider call."""

    model: str
    input_tokens: int
    output_tokens: int
    tries: int  # 1, or 2 after the one retry
    cost_note: str | None  # why the cost is unknown; None when it is known


@dataclass(frozen=True)
class OutputRecord:
    """The numbers recorded for one Task's Output. Never holds content."""

    task_id: str
    edit_distance: float
    active_seconds: float
    latency_seconds: float
    cost_usd: float | None
    call: ProviderCall | None = None  # an automated Candidate's call; None when manual


def edit_distance(raw: str, final: str) -> float:
    """Share of words changed: word-level Levenshtein distance over the longer word count."""
    a, b = raw.split(), final.split()
    if not a and not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other))
            )
        previous = current
    return previous[-1] / max(len(a), len(b))


def _load_yaml(f: FieldReader, path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise f.fail("(file)", f"is not valid YAML: {exc}") from exc


def _queued_tracks(snapshot: Mapping[str, Any]) -> frozenset[str] | None:
    queue = snapshot.get(SPOT_CHECK_QUEUE)
    return None if queue is None else frozenset(queue.values())


def run_id_for(candidate: Candidate, date: datetime.date) -> str:
    return f"{date.isoformat()}-{candidate.id}"


def check_gate(candidate: Candidate) -> None:
    if not candidate.gate_passed:
        raise GateNotPassed(f"Candidate {candidate.id} has no passing Gate, so it cannot be Run")


def check_clearance(candidate: Candidate, task: Task) -> None:
    """Call before reading any of a Private Task's content on the Candidate's behalf."""
    private = task.visibility == Visibility.PRIVATE
    if private and candidate.clearance != Clearance.PUBLIC_AND_PRIVATE:
        raise ClearanceError(
            f"Task {task.id} is private and Candidate {candidate.id} is cleared for "
            f"{candidate.clearance or 'nothing'}"
        )


# The Suites each Clearance allows, the default (all it allows) first (ADR-0006).
SUITES_FOR_CLEARANCE: Mapping[Clearance, tuple[Suite, ...]] = {
    Clearance.PUBLIC_ONLY: (Suite.PUBLIC_ONLY,),
    Clearance.PUBLIC_AND_PRIVATE: (Suite.PUBLIC_AND_PRIVATE, Suite.PUBLIC_ONLY),
}


def suites_for(candidate: Candidate) -> tuple[Suite, ...]:
    """The Suites a Run of the Candidate may cover, the default first; none without a
    passing Gate."""
    return () if candidate.clearance is None else SUITES_FOR_CLEARANCE[candidate.clearance]


def choose_suite(
    candidate: Candidate, requested: Suite | str | None, tasks: tuple[Task, ...]
) -> Suite:
    """The Suite a Run covers: the requested one, or by default all the Clearance allows.

    A Suite may narrow the Clearance to public only but never exceed it. A privately
    cleared Candidate with no Private Tasks loaded is refused rather than silently given
    a public-only Suite; ask for public_only explicitly.
    """
    allowed = suites_for(candidate)
    suite = allowed[0] if requested is None else Suite(requested)
    if suite not in allowed:
        raise ClearanceError(
            f"Candidate {candidate.id} is cleared for {candidate.clearance}, "
            f"not a {suite} Suite"
        )
    has_private = any(t.visibility == Visibility.PRIVATE for t in tasks)
    if suite == Suite.PUBLIC_AND_PRIVATE and not has_private:
        raise SuiteUnavailable(
            f"Candidate {candidate.id} is cleared for {suite} but no Private Tasks are "
            f"loaded (check private_roots); ask for a {Suite.PUBLIC_ONLY} Suite explicitly"
        )
    return suite


def tasks_in(suite: Suite, tasks: tuple[Task, ...]) -> tuple[Task, ...]:
    """The Tasks a Suite covers."""
    if suite == Suite.PUBLIC_AND_PRIVATE:
        return tasks
    return tuple(t for t in tasks if t.visibility == Visibility.PUBLIC)


def create_run(
    workspace_root: Path,
    candidate: Candidate,
    suite: Suite,
    tasks: tuple[Task, ...],
    date: datetime.date,
) -> Run:
    check_gate(candidate)
    for task in tasks:
        check_clearance(candidate, task)
    runnable = tuple(t for t in tasks if not t.placeholders)
    run_id = run_id_for(candidate, date)
    if (workspace_root / RUNS_FOLDER / run_id).exists():
        raise RunExists(f"Run {run_id} already exists; resume it instead")
    started = 1 + max((s.get("started", 0) for s in _snapshots(workspace_root)), default=0)
    run = Run(
        id=run_id,
        candidate=candidate,
        date=date,
        suite=suite,
        tasks=runnable,
        folder=workspace_root / RUNS_FOLDER / run_id,
        excluded={t.id: t.placeholders for t in tasks if t.placeholders},
        started=started,
    )
    run.folder.mkdir(parents=True)
    (run.folder / RUN_FILE).write_text(yaml.safe_dump(_snapshot(run), sort_keys=False))
    return run


def _snapshot(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "date": run.date.isoformat(),
        "suite": str(run.suite),
        "candidate": _candidate_snapshot(run.candidate),
        "tasks": run.task_versions,
        "excluded": {task_id: list(missing) for task_id, missing in run.excluded.items()},
        "started": run.started,
    }


def _candidate_snapshot(c: Candidate) -> dict[str, Any]:
    """What a Run freezes of its Candidate (ADR-0003): any change here is a new Candidate."""
    snapshot = {
        "id": c.id,
        "tool": c.tool,
        "version": c.version,
        "adapter": str(c.adapter),
        "settings": c.settings,
    }
    if c.cost_per_task_usd is not None:  # absent from Runs made before it existed
        snapshot["cost_per_task_usd"] = c.cost_per_task_usd
    return snapshot


def candidate_ids_used(workspace_root: Path) -> set[str]:
    """Every Candidate id some Run's snapshot holds; frozen per ADR-0003."""
    return {
        (yaml.safe_load(path.read_text()) or {}).get("candidate", {}).get("id")
        for path in (workspace_root / RUNS_FOLDER).glob(f"*/{RUN_FILE}")
    } - {None}


def list_run_ids(workspace_root: Path) -> tuple[str, ...]:
    """Every Run id under runs/, newest first (ids start with the Run's date)."""
    paths = (workspace_root / RUNS_FOLDER).glob(f"*/{RUN_FILE}")
    return tuple(sorted((p.parent.name for p in paths), reverse=True))


def _snapshots(workspace_root: Path) -> list[dict[str, Any]]:
    return [
        yaml.safe_load(path.read_text()) or {}
        for path in (workspace_root / RUNS_FOLDER).glob(f"*/{RUN_FILE}")
    ]


def tracks_spot_checked_before(workspace_root: Path, run: Run) -> frozenset[str]:
    """The Tracks queued for a Spot-check in the same Candidate's previous Run: the
    latest one with a queue started before this one, whatever their dates (Run.started;
    Runs from before the order was recorded fall back to their dates). Empty when there
    is none."""
    def order(snapshot: Mapping[str, Any]) -> tuple[int, str, str]:
        return snapshot.get("started", 0), snapshot.get("date", ""), snapshot.get("id", "")

    this = (run.started, run.date.isoformat(), run.id)
    earlier = [
        s for s in _snapshots(workspace_root)
        if (s.get("candidate") or {}).get("id") == run.candidate_id
        and order(s) < this
        and _queued_tracks(s) is not None
    ]
    latest = max(earlier, key=order, default=None)
    return frozenset() if latest is None else _queued_tracks(latest)


def task_versions_used(workspace_root: Path) -> set[tuple[str, int]]:
    """Every (Task id, version) some Run's snapshot holds; frozen per ADR-0003."""
    used: set[tuple[str, int]] = set()
    for path in (workspace_root / RUNS_FOLDER).glob(f"*/{RUN_FILE}"):
        snapshot = yaml.safe_load(path.read_text()) or {}
        used.update((task_id, v) for task_id, v in (snapshot.get("tasks") or {}).items())
    return used


def open_run(
    ctx: TaskContext,
    run_id: str,
    candidates: Mapping[str, Candidate],
    workspace_tasks: tuple[Task, ...],
) -> Run:
    """Load a Run from its snapshot, refusing if anything it used has changed (ADR-0003).

    A Task since bumped is loaded at the version the Run used, from its archive. A Run
    still unfinished is refused instead: it must finish on the versions it started with.
    """
    folder = ctx.workspace_root / RUNS_FOLDER / run_id
    if not (folder / RUN_FILE).is_file():
        raise UnknownRunError(f"no Run {run_id!r} in {RUNS_FOLDER}/")
    snapshot = yaml.safe_load((folder / RUN_FILE).read_text())
    snapped = snapshot["candidate"]
    candidate = candidates.get(snapped["id"])
    if candidate is None or _candidate_snapshot(candidate) != snapped:
        raise RunMismatch(f"Run {run_id}: Candidate {snapped['id']} is missing or has changed")
    check_gate(candidate)
    by_id = {t.id: t for t in workspace_tasks}
    tasks = []
    bumped = []
    for task_id, version in snapshot["tasks"].items():
        task = by_id.get(task_id)
        used = task if task is None or task.version == version else (
            load_version(ctx, task, version)
        )
        if used is None:
            found = "missing" if task is None else f"now version {task.version}"
            raise RunMismatch(f"Run {run_id} used Task {task_id} version {version}, {found}")
        if used is not task:
            bumped.append(f"Task {task_id} version {version} is now version {task.version}")
        check_clearance(candidate, used)
        tasks.append(used)
    run = Run(
        id=run_id,
        candidate=candidate,
        date=datetime.date.fromisoformat(snapshot["date"]),
        suite=Suite(snapshot["suite"]),
        tasks=tuple(tasks),
        folder=folder,
        excluded={
            task_id: tuple(missing)
            for task_id, missing in snapshot.get("excluded", {}).items()
        },
        started=snapshot.get("started", 0),
    )
    if bumped and not run.complete:
        raise RunMismatch(
            f"Run {run_id} cannot resume: {'; '.join(bumped)}, and a Run must finish "
            "on the Task versions it started with"
        )
    return run
