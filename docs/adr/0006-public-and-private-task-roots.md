# Tasks are public or private, and private content never enters the public repo

The public repo holds the harness, public Tasks and the numbers from every Run. Private Tasks, which hold real sanitized work, live in one or more separate private git repos whose paths are set in config, so they keep their own history (ADR-0001) and the public repo never contains even an ignored copy. For a private Task, everything that can quote its input (raw Output, edited final, Judge reasons, Spot-check notes, Calibration items) is written to the private root. The public Run keeps only numbers (Criterion scores, Edit Distance, timings, cost) marked private.

Private Tasks restore the input protection that ADR-0004 gave and ADR-0005 removed: the Gate gains a Clearance level (public only, or public and private). The harness refuses to send a private Task's input to any Candidate or Judge without private Clearance, and in manual-assisted mode will not display it. A Comparison covers a Suite; a Candidate cleared for public only gets a public-only Suite, whose Verdict is labelled as such and cannot count as a full adopt. Public Decision Log entries note how many private Tasks a Verdict includes, because public data alone cannot reproduce it.

## Considered Options

A git-ignored private folder inside the public repo was rejected: private Tasks would lose git history, and one mistaken `git add -f` would publish them.
