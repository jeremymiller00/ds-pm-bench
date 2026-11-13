# Build Plan: DS PM Benchmark TUI

Source: the "DS PM AI Benchmark Suite" doc (2026-11-13) plus the design grilling. Vocabulary is in [GLOSSARY.md](GLOSSARY.md); hard-to-reverse decisions are in [docs/adr/](docs/adr/).

## Goal

One public repo containing the harness that runs the suite, the editable and expandable Public Tasks (plus Private Tasks read from separate private repos), and the tools to set all configuration. A Verdict (adopt, reject, re-test) comes from a Comparison of a challenger Run against a same-week Baseline Run, and is written to the Decision Log.

## Settled design

- **Stack:** Python 3.12, Textual, `uv`, pytest. Data is YAML and Markdown files ([ADR 0001](docs/adr/0001-plain-files-in-git-not-a-database.md)).
- **Layering:** a pure core library (tasks, runs, scoring, adopt rule, judge, calibration) with no UI imports. The TUI is primary; a thin CLI (`bench run`, `bench score`, `bench verdict`) sits on the same core.
- **Adapters:** automated (API or CLI) and manual-assisted (paste the output back, timed). Both write the same Output record.
- **Immutability:** Candidates, Task versions and Judge Setups are frozen once used ([ADR 0003](docs/adr/0003-candidates-and-judge-setups-are-immutable.md)).
- **Public repo, secrets only private ([ADR 0005](docs/adr/0005-public-repo-secrets-only-private.md)):** every input and Gold Answer must be safe to publish. Config holds env var names only; keys live in a git-ignored `.env`; a secret scan runs before every commit.
- **Public and private Tasks ([ADR 0006](docs/adr/0006-public-and-private-task-roots.md)):** Private Tasks live in separate private git repos whose paths are in config. For a Private Task, Outputs, finals, Judge reasons, Spot-check notes and Calibration items go to the private root; the public Run keeps numbers only. The 12 catalog Tasks are private by default; `examples/` and any synthetic twins are public.
- **Gate and Clearance:** a Candidate without a passing Gate cannot be Run or adopted. Clearance (public only, or public and private) applies to Candidates and Judges; a Private Task's input is never sent to, or displayed for, anything without private Clearance.
- **Suite:** a Comparison covers one Suite. A public-only Suite gets a labelled Verdict that cannot count as a full adopt, and every public Decision Log entry notes how many Private Tasks it includes.
- **Judging:** single non-Claude judge or a panel (median, disagreement flag). Certified against a human-scored Calibration Set before use: at least 90% of scores within 1 point and weighted kappa of at least 0.6, both configurable ([ADR 0002](docs/adr/0002-certified-judge-with-human-calibration.md)). A same-family Candidate gets a bias warning and a full human check. The 25% per-run Spot-check stays.
- **Scoring:** Dimensions Quality 50, Edit distance 20, Time saved 20, Cost/latency 10, editable and must sum to 100. Adopt when the Gate passes, the weighted score is at least the Baseline's, and no Track's quality falls more than 0.5 below it. Re-test if within 0.2 of baseline or if judge and human differ by more than 1 point. Otherwise reject. Candidates with no per-task cost score latency only, with a visible note.
- **Repo contents:** Public Tasks, Run numbers and the Decision Log are committed and public. Only `.env` and caches are ignored; private content lives in the private root(s).

## Repo layout (target)

```
ds-pm-bench/
  GLOSSARY.md  PLAN.md  docs/adr/
  config/       settings.yaml  rubric.yaml  prices.yaml  judge/prompt.md
  candidates/   <candidate>@<version>.yaml  (includes Gate record)
  tasks/        T1.1/ task.md input.* gold.md rubric.yaml  ... T4.3/
  examples/     one synthetic task, runs without private data
  calibration/  scored outputs + human scores
  runs/         <date>-<candidate>/ raw/ final/ judge.json scores.json
  decisions/    log entries
  src/bench/    core/  adapters/  cli.py  tui/
  tests/

ds-pm-bench-private/   (separate private repo, path in git-ignored config/local.yaml)
  tasks/        T1.1/ ... T4.3/   (catalog Tasks, private by default)
  runs/         <date>-<candidate>/ raw/ final/ judge-reasons.json
  calibration/  private calibration items
```

## Slices

Each slice is demoable and tested without network access.

### 1. Core and config
- File formats and loaders for Task, Candidate, Run, config, reading public and private Task roots.
- Scoring math: weight validation, normalization of each Dimension to 1–5, Quality averaging, null-cost handling, edit distance.
- Adopt/reject/re-test rule, including the 0.5 and 0.2 margins, Suites and the public-only label.
- Clearance checks and the public/private write split.
- Thin CLI, the `examples/` task, and golden-fixture tests for every verdict path.
- Done when: `bench verdict` on two fixture Runs gives the expected Verdict.

### 2. Task library
- Scaffold the 12 Tasks from the doc's catalog as Private Tasks in the private root (id, Track, Trap, expected output, task-specific Criteria, Manual Time field).
- Draft Canonical Prompts from `ai-pm-toolkit/prompts` where they map; inputs and Gold Answers start as `TODO`.
- TUI task browser (showing visibility), editor and new-task scaffold that asks public or private. A Task edit bumps its version.
- The harness refuses to run a Task whose Gold Answer is `TODO`.
- Done when: you can edit a Task in the TUI and see the version bump in git.

### 3. Manual-assisted run flow (TUI)
- Gate checklist, record and Clearance per Candidate.
- Per Task: show prompt and input, start the timer, paste the Output, stop the timer, open in `$EDITOR`, time the edit session (pausable), save raw and final, compute edit distance.
- Manual Time capture for the Time-saved Dimension.
- Done when: a full Run of the example Task completes for a Candidate with no API.

### 4. Automated adapters and judge
- Anthropic adapter, plus one other provider, behind the Adapter interface; token and latency capture; prices from config.
- Blind judge: single or panel, versioned by prompt hash, one-line reason per Criterion, panel median and disagreement flag.
- Same-family bias warning.
- Done when: an automated Run plus judge output works against recorded fakes, with an opt-in live smoke test per adapter.

### 5. Calibration, scorecard, log, config screens
- Calibration screen: blind human scoring, agreement stats, Certification, auto-invalidation on Judge Setup change; uncertified-judge override is logged.
- Spot-check queue per Run (3 of 12, one per Track across two Runs).
- Comparison scorecard with the Verdict, then a Decision Log entry written to `decisions/`.
- Config screens for weights, thresholds, Candidates, Judge Setups and prices, with validation.
- Done when: a Comparison goes from two Runs to a logged Verdict entirely in the TUI.

## Test strategy

Pytest with no real API calls.
- Unit tests with golden fixtures for everything that decides a Verdict.
- Adapters and judge against recorded or fake responses.
- A few Textual pilot tests: run a manual Task, reject weights that do not sum to 100, view a Verdict.
- One opt-in live smoke test per adapter, skipped by default.

## Open items for you (not blocking slice 1)

- Real sanitized inputs and Gold Answers for the 12 Private Tasks, and any public synthetic twins, filled in through the TUI task editor.
- Creating the private repo (for example `~/ds-pm-bench-private`).
- Which non-Claude model is the judge, and which second provider gets the other automated adapter.
- Timing the Manual Time for each Task once.
