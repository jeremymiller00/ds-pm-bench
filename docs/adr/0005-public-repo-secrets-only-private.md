---
status: amended by ADR-0006
---

# The repo is public; only secrets are kept out of it

Everything is committed to a public repo: the harness, public Tasks with their inputs and Gold Answers, Runs, Outputs, Calibration Sets and the Decision Log. This makes the benchmark itself an observable team artifact and lets others reuse or extend it. The cost is that every input and Gold Answer must be safe to publish, so Tasks are synthetic or sanitized to a public standard, not merely to an internal one. Secrets are the only exception: config holds environment variable names, values live in a git-ignored `.env`, and a secret scan runs before every commit.

The Gate stays, but as a precondition of a Run and of an adopt Verdict ("we could actually use this tool for real work"), not as protection for the inputs, which are already public. This supersedes ADR-0004.

## Consequences

Results are public from the first Run, which reverses the source doc's "private until two Candidates have run".

ADR-0006 amends this: Private Tasks and their content live outside the public repo.
