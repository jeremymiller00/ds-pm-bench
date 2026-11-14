# Candidates, Tasks and Judge Setups are immutable once used

A Candidate (tool, exact model or version, settings), a Task version and a Judge Setup (identified by content hash) are frozen the moment a Run uses them. Any edit creates a new version, and every Run and Decision Log entry records the exact versions it used. This is slower than editing in place, but a Verdict is only comparable to another Verdict if everything that produced the scores can be reconstructed, and model drift makes silent edits especially dangerous.

## Task versions are archived, not only kept in git history

When an edit bumps a Task that a Run used, the used version is first copied into the Task's own `versions/<n>/` folder, so a Run can always load the exact Task version it used, whether or not anyone committed before editing. Relying on git history alone was rejected: an uncommitted edit would make a past Run unreadable, and a Run would need a commit pointer as well as a version number. The archive sits beside the Task, so a Private Task's old versions stay in its private root.

## The Gate is a review record, not part of a Candidate

A Candidate's identity is its tool, exact version and settings. Its Gate is a dated review of that Candidate, so re-reviewing the Gate updates it in place and keeps every earlier review, rather than creating a new Candidate. The latest review applies immediately to every Run, including one in progress: a failed or narrowed Gate stops a Run from continuing or seeing Private Task inputs. Treating a re-review as a new Candidate was rejected because it let a Run started under the old Gate keep its old permissions.

## Judge prompts and the shared Rubric are archived by hash

Editing the judge prompt or the shared Rubric first copies the version being replaced into an archive keyed by its content hash, and every Run and certification records the hashes it used. A past Verdict can therefore be rebuilt from the repo alone, whether or not anyone committed before editing, for the same reason Task versions are archived.
