# Candidates, Tasks and Judge Setups are immutable once used

A Candidate (tool, exact model or version, settings), a Task version and a Judge Setup (identified by content hash) are frozen the moment a Run uses them. Any edit creates a new version, and every Run and Decision Log entry records the exact versions it used. This is slower than editing in place, but a Verdict is only comparable to another Verdict if everything that produced the scores can be reconstructed, and model drift makes silent edits especially dangerous.

## Task versions are archived, not only kept in git history

When an edit bumps a Task that a Run used, the used version is first copied into the Task's own `versions/<n>/` folder, so a Run can always load the exact Task version it used, whether or not anyone committed before editing. Relying on git history alone was rejected: an uncommitted edit would make a past Run unreadable, and a Run would need a commit pointer as well as a version number. The archive sits beside the Task, so a Private Task's old versions stay in its private root.
