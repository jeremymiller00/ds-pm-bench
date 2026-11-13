# Candidates, Tasks and Judge Setups are immutable once used

A Candidate (tool, exact model or version, settings), a Task version and a Judge Setup (identified by content hash) are frozen the moment a Run uses them. Any edit creates a new version, and every Run and Decision Log entry records the exact versions it used. This is slower than editing in place, but a Verdict is only comparable to another Verdict if everything that produced the scores can be reconstructed, and model drift makes silent edits especially dangerous.
