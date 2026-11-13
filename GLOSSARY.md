# DS PM Benchmark

A repeatable benchmark that answers one question: should a new AI tool or model be adopted for data-science product-management work? It runs a fixed set of PM tasks against a Candidate, compares it with the incumbent Baseline, and logs a dated Verdict.

## Language

### What is tested

**Track**:
One of four jobs-to-be-done the suite covers (Framing, Specs, Prioritize, Story), each holding three Tasks.
_Avoid_: Category, suite section

**Task**:
One unit of PM work with a public-safe input (synthetic or sanitized), a canonical prompt, a Gold Answer, a Rubric and one Trap. Identified as T<track>.<n>.
_Avoid_: Test case, question, scenario

**Public Task**:
A Task whose input and Gold Answer are safe to publish and live in the public repo.
_Avoid_: Open task, shared task

**Private Task**:
A Task holding real sanitized work, stored outside the public repo, whose content is only ever sent to Candidates and Judges with private Clearance.
_Avoid_: Internal task, secret task

**Trap**:
The single built-in failure mode a Task is designed to expose, such as inventing a metric the input never states.
_Avoid_: Gotcha, edge case

**Gold Answer**:
The PM-written or PM-approved reference output for a Task, used by the Judge and never shown to a Candidate.
_Avoid_: Ground truth, expected output, reference

**Canonical Prompt**:
The exact prompt text for a Task, used word for word on every Candidate.
_Avoid_: Prompt template

### What is scored

**Rubric**:
The set of Criteria a Task's Output is scored against: the shared Criteria plus one or two task-specific ones.

**Criterion**:
One 1–5 scored aspect of output quality, such as Correctness or PM judgment.
_Avoid_: Metric, rule

**Dimension**:
One weighted component of the overall score: Quality, Edit distance, Time saved, or Cost/latency. Quality is the average of a Task's Criteria.
_Avoid_: Metric, factor

**Edit Distance**:
The share of words changed between a raw Output and its edited final.

**Manual Time**:
The minutes a PM takes to do a Task by hand, timed once and used to measure Time saved.
_Avoid_: Manual baseline (reserved for the incumbent Candidate sense of Baseline)

### Who is tested

**Candidate**:
One tool, an exact model or version string, and its settings, frozen once a Run uses it. Any change makes a new Candidate.
_Avoid_: Model, tool, challenger (except in a Comparison)

**Baseline**:
The Candidate designated as the incumbent that challengers must beat, currently Claude.
_Avoid_: Control, reference

**Adapter**:
The way a Candidate is driven: automated (API or CLI) or manual-assisted, where a person pastes the Output back.

### Running and deciding

**Gate**:
The pass-or-fail security and data-handling review of a Candidate, which must pass before it can be Run or adopted. Not scored.
_Avoid_: Security check, approval

**Clearance**:
The level of data a Candidate or Judge is approved for by its Gate: public only, or public and private.
_Avoid_: Permission, access level

**Suite**:
The set of Tasks a Comparison covers: public and private, or public only when a Candidate lacks private Clearance.
_Avoid_: Benchmark, test set

**Run**:
One Candidate against all Tasks on one date.
_Avoid_: Execution, session, benchmark

**Output**:
What a Candidate returned for a Task, kept as the raw version and the edited final.

**Comparison**:
A challenger Run paired with a Baseline Run from the same week, over the same Suite. The Verdict belongs to a Comparison, never to a single Run.
_Avoid_: Evaluation, head-to-head

**Verdict**:
The outcome of a Comparison: adopt, reject, or re-test.
_Avoid_: Decision (reserved for the Decision Log), result

**Regression**:
A Track whose quality score falls more than the configured margin below the Baseline's.

**Decision Log**:
The dated, append-only record of Verdicts with their rationale.

### Judging

**Judge**:
The model, or panel of models, that scores Outputs blind against the Rubric and Gold Answer. Never the Candidate under test.
_Avoid_: Grader, evaluator

**Judge Setup**:
The Judge model or panel plus the judge prompt, identified by a content hash.

**Spot-check**:
A human scoring of a sampled Output during a Run, used to detect Judge drift.

**Calibration Set**:
A fixed collection of Outputs scored blind by a human expert, used to test the Judge.

**Certification**:
The status of a Judge Setup whose agreement with the human scores on the Calibration Set meets the thresholds. Lost when the Judge Setup changes.
