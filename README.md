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
uv run bench --help
```

Run every command from the benchmark repo root, or pass `--workspace DIR`. The
workspace needs `config/`, Tasks under `tasks/` or `examples/`, and Candidates under
`candidates/` with exactly one marked `baseline: true`.

### 1. Record a Run from results files

A results folder holds one `<task id>.yaml` per Task in the Suite:

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
bench run --resume 2026-11-13-claude@opus-5-5 --results results/claude
```

`bench run` starts the Run and records every Task that has a file; Tasks without one
stay pending, and `--resume` records them later. The Run id is `<date>-<candidate>`.

### 2. Score a Run

```sh
bench score 2026-11-13-claude@opus-5-5
```

Prints the weighted score, each Dimension and each Track's Quality.

### 3. Reach a Verdict

```sh
bench verdict 2026-11-13-other@1 2026-11-13-claude@opus-5-5 [--spot-check-disagreement 1.5]
bench verdict --gate other@1                     # a Candidate whose Gate failed
bench verdict ... --log --on 2026-11-13 [--judge-setup-hash HASH]
```

The first Run is the challenger, the second must be a Run of the Baseline. The first
line is the Verdict and its label (`adopt (public-only Suite, not a full adopt)` when
no Private Tasks were in the Suite), followed by the rationale. `--log` appends it to
the Decision Log under `decisions/`.

### Golden fixtures

`tests/fixtures/golden/<case>/` holds the Candidates and results files for one
Verdict path over the public `examples/` Task: `adopt-public-only`,
`reject-by-weighted`, `reject-by-regression`, `reject-by-gate`, `retest-by-margin`
and `retest-by-spot-check`. Each `case.yaml` states the expected Verdict, and
`tests/test_cli.py` reaches it through `bench run` and `bench verdict`.
