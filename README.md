# DS PM Benchmark

A harness for deciding whether to adopt an AI tool for DS PM work. Vocabulary is in
[GLOSSARY.md](GLOSSARY.md), the build plan in [PLAN.md](PLAN.md), and decisions in
[docs/adr/](docs/adr/).

## Running slice 1

Slice 1 is the core library and a thin `bench` CLI over it. There is no Judge or
adapter yet: you record each Task's Output, timings, cost and Criterion scores in a
results file, and the CLI turns them into Runs, scores and Verdicts.

```sh
uv sync
uv run pytest            # includes the golden Verdict fixtures, driven through the CLI
uv run ruff check        # lint and import order; also runs as a pre-commit hook
uv run bench --help
uv run bench               # open the TUI (same as `bench tui`)
```

Run every command from the benchmark repo root, or pass `--workspace DIR`. The
workspace needs `config/`, Tasks under `tasks/` or `examples/`, and Candidates under
`candidates/` with exactly one marked `baseline: true`. Each Task's `track` must be
one of the `tracks` listed in `config/settings.yaml`.

Private Tasks (ADR 0006) are read from private roots listed in `config/local.yaml`,
which is git-ignored because the paths are machine-specific. Copy
`config/local.example.yaml` to start; without it no Private Tasks load.
`private_roots` in the public `config/settings.yaml` is refused.

To start the 12 catalog Tasks (T1.1-T4.3) as Private Tasks in one of those roots:

```sh
bench scaffold-catalog --root ../ds-pm-bench-private
```

Each gets its Track, Trap and one or two task-specific Criteria; the input, Gold
Answer, Manual Time and Canonical Prompt start as placeholders (`TODO`), so the
harness will not Run a Task until all four are filled in. It refuses, writing
nothing, if any catalog Task already exists.

In the TUI's Tasks tab, `e` edits the highlighted Task and `n` scaffolds a new one
(private only when a private root is configured); `ctrl+s` saves, `escape` cancels.
Saving shows the Task's version, and whether the edit made a new one because a Run
used the old (ADR 0003). Validation errors appear inline in the form.

### 1. Record a Run from results files

A results folder holds one `<task id>.yaml` per Task in the Suite (an unknown key
or a missing measure fails naming the file and field):

```yaml
raw: the Candidate's raw Output
final: the PM's edited final
active_seconds: 600        # PM active time, prompting plus editing
latency_seconds: 12        # time to first draft
cost_usd: 0.03             # null when the Candidate has no per-Task cost
criterion_scores:          # optional; 1-5 for every shared and task Criterion
  correctness: 4
  # ...
```

```sh
bench run claude@opus-5-5 --results results/claude --on 2026-11-13
bench run other@1 --suite public_only --results results/other --on 2026-11-13
bench run --resume 2026-11-13-claude@opus-5-5 --results results/claude
```

`bench run` starts the Run and records every Task that has a file; Tasks without one
stay pending, and `--resume` records them later. The Run id is `<date>-<candidate>`.

The Suite defaults to every Task the Candidate's Clearance allows. `--suite
public_only` narrows a private Clearance; a Suite never exceeds the Clearance. A
Candidate cleared for private Tasks when none are loaded (no `private_roots`) is
refused unless you pass `--suite public_only`. Tasks still holding a placeholder
(input, Gold Answer, Manual Time or Canonical Prompt) are left out of the Suite and
listed under `excluded` in the Run's `run.yaml`.

A Run resumes only while its Candidate (tool, version, adapter, settings, flat
per-Task cost) is unchanged (ADR 0003). Changing a Candidate a Run used adds the
next Candidate to its Lineage, `candidates/<tool>@<version>~<n>.yaml`, and leaves the
used file as it was; the new Candidate takes the Baseline mark if the old one held it.
The Gate is a review record, not part of the Candidate: recording it again appends a
dated review in place, and the latest review applies at once, even to a Run in
progress (a failed or narrowed Gate stops it). A review that moves the Gate to failed
appends a reject Verdict to `decisions/`; the Baseline must hold a passing Gate. A
manual-assisted Candidate may set `cost_per_task_usd`, recorded as each Task's cost;
without it cost is null. Editing a Task a Run used makes a new version and first
copies the used one into the Task's `versions/<n>/` folder (in the private root for
a Private Task), so a finished Run still opens, scores and compares on the versions
it used. An unfinished Run is refused once a Task it used has a new version: a Run
must finish on the Task versions it started with.

### 2. Score a Run

```sh
bench score 2026-11-13-claude@opus-5-5
```

Prints the weighted score, each Dimension and each Track's Quality.

### 3. Record Spot-checks

```sh
bench spot-check 2026-11-13-other@1 T1.1-example clarity 3.5
```

Records a human's 1-5 score for one Criterion the Judge already scored, and prints
how far apart they are. A Comparison re-tests when any Spot-check on either Run is
more than 1 point from the Judge.

### 4. Reach a Verdict

```sh
bench verdict 2026-11-13-other@1 2026-11-13-claude@opus-5-5
bench verdict --gate other@1                     # a Candidate whose Gate failed
bench verdict ... --log --on 2026-11-13 [--judge-setup-hash HASH]
```

The first Run is the challenger, the second must be a Run of the Baseline. The first
line is the Verdict and its label (`adopt (public-only Suite, not a full adopt)` when
the Suite was public only), followed by the rationale. The rules apply in order:
Gate, Spot-check, Regression, then the weighted score against the Baseline (adopt,
re-test within the margin, reject). `--log` appends it to
the Decision Log under `decisions/`, with the hash of the Judge Setup that scored both
Runs unless `--judge-setup-hash` is given.

### Judge Setup

`config/judge.yaml` names the Judge Setup (ADR 0002, 0003):

```yaml
mode: panel                  # single: exactly one model; panel: two or more
clearance: public_only       # or public_and_private, to judge Private Tasks
prompt: config/judge/prompt.md
models:
  - {provider: openai, model: gpt-6}
  - {provider: google, model: gemini-4}
  # family: [chatbot]        # optional: Candidate tools of this model's family
```

Its hash covers the models and the prompt, so editing either is a new Judge Setup.
`Workspace.judge_run(run, allow_uncertified=True)` scores every recorded, unscored
Output blind; until certification lands every Judge Setup is uncertified, so the
override is required and each one is logged to the Decision Log. A Judge that is the
Candidate under test is refused, and a same-family Judge marks Tasks for a full human
check.

### Golden fixtures

`tests/fixtures/golden/<case>/` holds the Candidates and results files for one
Verdict path over the public `examples/` Task: `adopt-public-only`,
`reject-by-weighted`, `reject-by-regression`, `reject-by-gate`, `retest-by-margin`
and `retest-by-spot-check`. Each `case.yaml` states the expected Verdict (and any Spot-checks
to record), and `tests/test_cli.py` reaches it through `bench run`, `bench
spot-check` and `bench verdict`.
