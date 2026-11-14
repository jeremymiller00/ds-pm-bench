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

### Automated Candidates

An `automated` Candidate is called through the Anthropic or OpenAI Python SDK.
`settings.provider` (`anthropic` or `openai`) may be left out when the tool is Claude,
Anthropic, OpenAI, ChatGPT or GPT; the model id is `settings.model`, else the
Candidate's `version`. `settings.max_tokens` and `settings.temperature` are passed on.
The API key is read from the first name in `env_vars`, from the environment or else a
git-ignored `.env` at the repo root (ADR 0005). Prices come from `config/prices.yaml`,
keyed by model id; a model with no price gets a null cost.

The CLI and TUI open the Workspace through `bench.wiring.open_workspace`, which wires in
the provider SDKs; the core never imports them, and a `Workspace.open` without Adapters
or judge models refuses only when a Task is sent or a Run judged.

`Workspace.run_automated_task(run, task_id)` sends one Task, retrying once on a tool
error, and keeps the raw Output as a Returned Output, ready to edit;
`Workspace.sendable_tasks(run)` lists the Tasks it would send. A Task that errored twice
stores nothing and can be sent again later. `Workspace.begin_attempt(run, task_id)`
begins an Attempt of either kind, chosen by the Candidate's Adapter: for an automated
Candidate it starts with the Returned Output already in, edit, pause and `$EDITOR` work
as for manual Attempts, and finishing records the editing time as active seconds with
the successful call's latency, cost, model, tokens and tries. The live smoke tests are skipped unless you opt in:

```sh
BENCH_LIVE=1 ANTHROPIC_API_KEY=... BENCH_LIVE_ANTHROPIC_MODEL=<model id> uv run pytest -m live
BENCH_LIVE=1 OPENAI_API_KEY=... uv run pytest -m live  # also judges examples/ once
```

In the TUI's Tasks tab, `e` edits the highlighted Task and `n` scaffolds a new one
(private only when a private root is configured); `ctrl+s` saves, `escape` cancels.
Saving shows the Task's version, and whether the edit made a new one because a Run
used the old (ADR 0003). Validation errors appear inline in the form.

In the Runs tab, a Run of an automated Candidate gets **Send Tasks**: each pending Task
with no Returned Output yet is sent in the background, and a Task that errored twice is
listed as failed; pressing it again retries only those. Enter on a Task whose raw
Output is ready opens the Task screen with it in, to edit and save with `ctrl+s`
(abandoning keeps the raw Output). **Judge Run**, enabled once the Run is finished, shows the Judge Setup and its hash and,
until Certification exists, judges only through an explicit override that is logged to
the Decision Log; it then lists each Task's Criterion scores with every judge model's reason,
Panel disagreements and same-family bias warnings.

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

Once a Run is fully judged by a Judge Setup it gets a Spot-check queue
(`Run.spot_check_queue`, saved in `run.yaml`): `spot_check_rate` of its Tasks (3 of 12),
sampled reproducibly from the Run id, taking first the Tracks the same Candidate's
previous Run did not queue, so every Track is checked across two Runs. "Previous" is
the order Runs were started (`started` in `run.yaml`), not their dates, so a Run dated
the same day or earlier still counts. Tasks marked for a
full human check (a same-family judge model, a panel disagreement) are always queued, so
a same-family Candidate gets every Task. `Run.spot_checks_left` lists each queued Task's
Criteria with no human score yet, without the Judge's scores, so the check stays blind.
A Comparison refuses (`SpotChecksUnfinished`) while either Run has any left: a Verdict is
never computed on unchecked judging. Runs scored from results files (Criterion scores a
human wrote in `criterion_scores`) are never judged, so they get no queue and need none:
their scores are already human scores, and `bench spot-check` can still record extra checks.

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

`config/judge.yaml` names the Judge Setup (ADR 0002, 0003). The committed default is
a single public-only OpenAI Judge (non-Claude, so Claude as Baseline is never judged by
its own family) with the versioned prompt in `config/judge/prompt.md`:

```yaml
mode: single                 # single: exactly one model; panel: two or more
clearance: public_only       # or public_and_private, to judge Private Tasks
prompt: config/judge/prompt.md
models:
  - provider: openai         # openai or anthropic (google: no SDK wired yet)
    model: gpt-6-astra
    env_var: OPENAI_API_KEY  # optional, default <PROVIDER>_API_KEY; env, else .env
  # - {provider: anthropic, model: ..., family: [chatbot]}
  #   family: optional, the Candidate tools of this model's family
```

Each judge model is asked for JSON in its provider's structured-output mode (a JSON Schema
naming every Criterion). The key's `env_var` is not part of the hash.
Its hash covers the mode, the models and the prompt, so editing any of them is a new
Judge Setup.
`Workspace.judge_run(run)` scores every unscored Output of a finished Run blind with a
certified Judge Setup; an uncertified one needs `allow_uncertified=True`, and that
override is logged to the Decision Log once a Task is judged (once per Run and Judge
Setup). A judge model that is the model the Candidate calls (same
provider and model id) is refused, and a same-family judge model marks Tasks for a full
human check. Every refusal is a `JudgingError`.

### Archived judge prompts and Rubrics

Before the judge prompt or the shared Rubric is replaced, and whenever a Run, a
Calibration judging or a Certification uses one, its text is kept under its content
hash in `config/archive/judge-prompts/` and `config/archive/rubrics/` (ADR 0003). A
Run records `rubric_hash` and its Judge Setup's `prompt_hash`, and a Certification
both, so `Workspace.run_rubric(run)` and `run_judge_prompt(run)` rebuild what scored a
past Run from the repo alone, committed or not. A Run keeps scoring on the Rubric it
started with. `Workspace.update_rubric(criteria)` adds, edits or removes shared
Criteria (also in the TUI's Settings tab), refusing a change that would leave a Task
invalid, such as a shared Criterion a Task already defines.

### Calibration and Certification

The Calibration Set (`calibration/`) holds final Outputs of finished Runs, Spot-checked
or not: `Workspace.add_calibration_item(run_id, task_id)`. The public
`calibration/<item>.yaml` keeps numbers only (Run, Task, version, Criterion ids, human
scores, each Judge Setup's median scores by hash); the input, Gold Answer, Rubric text,
Output and Judge reasons go to `calibration/content/` and `calibration/judge/` in the
public repo, or in the private root for a Private Task. Item ids (`c0001`) are opaque.

`unscored_calibration_items()` gives what a human scores blind (content only, no Judge
scores, Run or Candidate) and `score_calibration_item(item_id, scores)` records a whole
1-5 score per Criterion. `judge_calibration_set()` has the current Judge Setup score every
item it has not, and `calibration_agreement()` reports per Criterion (and pooled) the
share within 1 point and the quadratic weighted kappa. `certify_judge()` needs every item
scored by both, at least `calibration.min_items` of them (default 12), and every
Criterion at or above `calibration.within_one_point` and
`calibration.weighted_kappa` in settings (a Criterion whose kappa is undefined because
both always gave the same score is held to the first only); it logs a
`judge_certification` entry to the Decision Log, or raises `CertificationFailed` naming
each shortfall. A Certification belongs to one Judge Setup hash and one Clearance
(ADR 0002): `judge_certified` reads the log for the current hash and Clearance, so an
edited mode, judge model or prompt is uncertified until certified again, and widening
the Clearance to `public_and_private` drops it.

### Golden fixtures

`tests/fixtures/golden/<case>/` holds the Candidates and results files for one
Verdict path over the public `examples/` Task: `adopt-public-only`,
`reject-by-weighted`, `reject-by-regression`, `reject-by-gate`, `retest-by-margin`
and `retest-by-spot-check`. Each `case.yaml` states the expected Verdict (and any Spot-checks
to record), and `tests/test_cli.py` reaches it through `bench run`, `bench
spot-check` and `bench verdict`.
