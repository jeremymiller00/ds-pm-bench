"""The `bench` command line: a thin client over the Workspace. No business logic here.

  bench [tui]                                        open the TUI on the Workspace
  bench run CANDIDATE --results DIR [--on DATE] [--suite public_only]
                                                     start a Run and record results
  bench run --resume RUN_ID --results DIR            record results into an open Run
  bench spot-check RUN TASK CRITERION SCORE         record a human Spot-check score
  bench score RUN_ID                                 print a Run's scores
  bench verdict CHALLENGER_RUN BASELINE_RUN [...]    compare two Runs, print the Verdict
  bench verdict --gate CANDIDATE [...]               the Verdict for a failed Gate
  bench scaffold-catalog --root PRIVATE_ROOT         create the 12 catalog Tasks there
  bench judge RUN_ID [--allow-uncertified]           score a finished Run with the Judge
  bench calibrate add RUN_ID TASK [--on DATE]        add an Output to the Calibration Set
  bench calibrate status                             the Judge Setup's agreement
  bench calibrate certify [--on DATE]                certify the Judge Setup

A results directory holds one <task id>.yaml per Task, with raw, final,
active_seconds, latency_seconds, cost_usd and (optionally) criterion_scores.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml

from bench.core.calibration import CriterionAgreement
from bench.core.errors import (
    ClearanceError,
    GateNotPassed,
    JudgingError,
    RunExists,
    RunIncomplete,
    RunMismatch,
    SuiteMismatch,
    SuiteUnavailable,
    TaskNotRunnable,
)
from bench.core.kinds import Suite
from bench.core.verdicts import Verdict
from bench.core.workspace import Workspace
from bench.wiring import open_workspace

# Refusals the CLI reports as one line rather than a traceback. ConfigError,
# CalibrationError and the Unknown*Error lookups are ValueError and LookupError
# subclasses.
_REFUSALS = (
    ValueError,
    LookupError,
    ClearanceError,
    GateNotPassed,
    JudgingError,
    RunExists,
    RunIncomplete,
    RunMismatch,
    SuiteMismatch,
    SuiteUnavailable,
    TaskNotRunnable,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    open_workspace: Callable[[str], Workspace] = open_workspace,
) -> int:
    """open_workspace builds the Workspace; tests pass one with fake Adapters."""
    args = _parser().parse_args(argv)
    try:
        ws = open_workspace(args.workspace)
        return args.command(ws, args)
    except (OSError, yaml.YAMLError) as error:
        print(f"bench: {error}", file=sys.stderr)
        return 2
    except _REFUSALS as error:
        print(f"bench: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _run(ws: Workspace, args: argparse.Namespace) -> int:
    if (args.candidate is None) == (args.resume is None):
        raise ValueError("give either a CANDIDATE to start a Run or --resume RUN_ID")
    if args.resume and (args.suite or args.on):
        raise ValueError("--suite and --on set a new Run; a resumed Run keeps its own")
    if args.resume:
        run = ws.resume_run(args.resume)
    else:
        run = ws.start_run(args.candidate, suite=args.suite, on=args.on)
    for task_id in run.record_from_folder(Path(args.results)):
        print(f"recorded {task_id}")
    print(f"Run {run.id}")
    if run.pending:
        print(f"pending: {', '.join(run.pending)}")
    return 0


def _spot_check(ws: Workspace, args: argparse.Namespace) -> int:
    check = ws.resume_run(args.run_id).record_spot_check(args.task, args.criterion, args.score)
    print(
        f"Spot-check {check.task_id} {check.criterion}: human {check.human_score:g} "
        f"vs Judge {check.judge_score:g} ({check.gap:g} points apart)"
    )
    return 0


def _score(ws: Workspace, args: argparse.Namespace) -> int:
    score = ws.score(ws.resume_run(args.run_id).task_results())
    print(f"Run {args.run_id}: weighted {score.weighted:.2f}")
    for dimension, value in score.dimensions.items():
        print(f"  {dimension}: {value:.2f}")
    for track, quality in score.track_quality.items():
        print(f"  Track {track} quality: {quality:.2f}")
    for note in score.notes:
        print(f"  note: {note}")
    return 0


def _verdict(ws: Workspace, args: argparse.Namespace) -> int:
    if args.gate:
        if args.runs:
            raise ValueError("--gate takes no Runs")
        verdict = ws.gate_verdict(args.gate)
        _print_verdict(verdict)
        if args.log:
            _print_logged(ws, ws.log_verdict(verdict, on=args.on,
                                             judge_setup_hash=args.judge_setup_hash))
        return 0
    if len(args.runs) != 2:
        raise ValueError("give CHALLENGER_RUN and BASELINE_RUN, or --gate CANDIDATE")
    challenger, baseline = args.runs
    comparison = ws.compare(challenger, baseline)
    _print_verdict(comparison.verdict)
    if args.log:
        _print_logged(ws, ws.log_comparison(comparison, on=args.on,
                                            judge_setup_hash=args.judge_setup_hash))
    return 0


def _print_logged(ws: Workspace, path: Path) -> None:
    print(f"logged: {path.relative_to(ws.root)}")


def _scaffold_catalog(ws: Workspace, args: argparse.Namespace) -> int:
    tasks = ws.scaffold_catalog(args.root)
    for task in tasks:
        print(f"created {task.id} ({task.track}) in {task.folder}")
    print(f"created {len(tasks)} Private Tasks; placeholders: {', '.join(tasks[0].placeholders)}")
    return 0


def _judge(ws: Workspace, args: argparse.Namespace) -> int:
    run = ws.resume_run(args.run_id)
    certified = ws.judge_certified
    judged = ws.judge_run(run, allow_uncertified=args.allow_uncertified)
    for judging in judged:
        scores = ", ".join(f"{c} {s:g}" for c, s in judging.scores.items())
        flags = [f"Panel disagreement on {', '.join(judging.disagreements)}"
                 if judging.disagreements else "",
                 "same-family judge model" if judging.same_family else ""]
        flagged = "".join(f"; {f}" for f in flags if f)
        print(f"judged {judging.output_id}: {scores}{flagged}")
    if not judged:
        print(f"Run {run.id}: nothing to judge, every Output is scored")
    elif not certified:
        print("judged with an uncertified Judge Setup: the override is in the Decision Log")
    return 0


def _calibrate_add(ws: Workspace, args: argparse.Namespace) -> int:
    item = ws.add_calibration_item(args.run_id, args.task, on=args.on)
    print(f"added {item.id}: Run {item.run_id}'s Output of Task {item.task_id}; "
          "score it blind in the TUI's Calibration tab")
    return 0


def _calibrate_status(ws: Workspace, args: argparse.Namespace) -> int:
    setup = ws.judge_setup
    if setup is None:
        print("no Judge Setup")
        return 0
    print(f"Judge Setup {setup.hash}: "
          f"{'certified' if ws.judge_certified else 'not certified'}")
    agreement = ws.calibration_agreement()
    print(f"  {len(ws.calibration_items())} items, {agreement.items} scored by both "
          f"(Certification needs {agreement.min_items})")
    for cid, a in agreement.criteria.items():
        print(f"  {cid}: {_agreement(a)}")
    print(f"  overall: {_agreement(agreement.overall)}")
    for shortfall in agreement.shortfalls:
        print(f"  not certifiable: {shortfall}")
    return 0


def _calibrate_certify(ws: Workspace, args: argparse.Namespace) -> int:
    agreement = ws.certify_judge(on=args.on)
    print(f"certified Judge Setup {agreement.judge_setup_hash}: "
          f"{_agreement(agreement.overall)}")
    return 0


def _agreement(a: CriterionAgreement) -> str:
    kappa = "n/a" if a.weighted_kappa is None else f"{a.weighted_kappa:.2f}"
    return f"{a.within_one_point:.0%} within 1 point, weighted kappa {kappa} ({a.items} scores)"


def _print_verdict(verdict: Verdict) -> None:
    print(f"Verdict: {verdict.label}")
    print(f"  {verdict.candidate_id} vs Baseline {verdict.baseline_id}")
    for reason in verdict.rationale:
        print(f"  - {reason}")
    if verdict.regressions:
        print(f"  Regressions: {', '.join(verdict.regressions)}")
    for warning in verdict.warnings:
        print(f"  warning: {warning}")


def _tui(ws: Workspace, args: argparse.Namespace) -> int:
    from bench.tui.app import BenchApp  # Textual loads only when the TUI is opened

    BenchApp(ws).run()
    return 0


def _date(text: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace", default=".", help="the benchmark repo root (default: current directory)"
    )
    parser.set_defaults(command=_tui)
    commands = parser.add_subparsers(metavar="COMMAND")

    tui = commands.add_parser("tui", help="open the TUI (the default with no COMMAND)")
    tui.set_defaults(command=_tui)

    run = commands.add_parser("run", help="start or resume a Run and record results from files")
    run.add_argument("candidate", nargs="?", help="the Candidate to Run, <tool>@<version>")
    run.add_argument("--resume", metavar="RUN_ID", help="record into this existing Run instead")
    run.add_argument(
        "--results", required=True, metavar="DIR", help="folder of <task id>.yaml result files"
    )
    run.add_argument("--on", type=_date, help="the Run date, YYYY-MM-DD (default: today)")
    run.add_argument(
        "--suite",
        choices=[str(s) for s in Suite],
        help="narrow the Suite (default: every Task the Candidate's Clearance allows)",
    )
    run.set_defaults(command=_run)

    spot = commands.add_parser(
        "spot-check", help="record a human's 1-5 score of one Criterion the Judge scored"
    )
    spot.add_argument("run_id", metavar="RUN_ID")
    spot.add_argument("task", metavar="TASK")
    spot.add_argument("criterion", metavar="CRITERION")
    spot.add_argument("score", type=float, metavar="SCORE", help="the human's 1-5 score")
    spot.set_defaults(command=_spot_check)

    score = commands.add_parser("score", help="print a complete Run's scores")
    score.add_argument("run_id", metavar="RUN_ID")
    score.set_defaults(command=_score)

    verdict = commands.add_parser("verdict", help="print the Verdict, optionally logging it")
    verdict.add_argument(
        "runs", nargs="*", metavar="RUN_ID", help="CHALLENGER_RUN BASELINE_RUN"
    )
    verdict.add_argument("--gate", metavar="CANDIDATE", help="reject a Candidate whose Gate failed")
    verdict.add_argument("--log", action="store_true", help="append it to the Decision Log")
    verdict.add_argument("--on", type=_date, help="the Decision Log date (default: today)")
    verdict.add_argument(
        "--judge-setup-hash",
        help="Judge Setup recorded with the entry; defaults to the one that scored both Runs",
    )
    verdict.set_defaults(command=_verdict)

    catalog = commands.add_parser(
        "scaffold-catalog",
        help="create the 12 catalog Tasks as Private Tasks with placeholders",
    )
    catalog.add_argument(
        "--root",
        required=True,
        metavar="PRIVATE_ROOT",
        help="a private root listed in config/local.yaml",
    )
    catalog.set_defaults(command=_scaffold_catalog)

    judge = commands.add_parser("judge", help="score a finished Run's Outputs with the Judge")
    judge.add_argument("run_id", metavar="RUN_ID")
    judge.add_argument(
        "--allow-uncertified", action="store_true",
        help="judge with an uncertified Judge Setup; the override is logged",
    )
    judge.set_defaults(command=_judge)

    calibrate = commands.add_parser("calibrate", help="the Calibration Set and Certification")
    steps = calibrate.add_subparsers(metavar="STEP", required=True)
    add = steps.add_parser("add", help="add a finished Run's Output of a Task to the set")
    add.add_argument("run_id", metavar="RUN_ID")
    add.add_argument("task", metavar="TASK")
    add.add_argument("--on", type=_date, help="the date added (default: today)")
    add.set_defaults(command=_calibrate_add)
    status = steps.add_parser("status", help="the Judge Setup's agreement and Certification")
    status.set_defaults(command=_calibrate_status)
    certify = steps.add_parser("certify", help="certify the Judge Setup, logged")
    certify.add_argument("--on", type=_date, help="the Decision Log date (default: today)")
    certify.set_defaults(command=_calibrate_certify)
    return parser
