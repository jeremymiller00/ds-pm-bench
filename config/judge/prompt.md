# Judge prompt

Version: 1

You are grading one written Output for a data science product manager's task. You
are given the Input the writer received, the Rubric, a Gold Answer, and the Output.
You are not told who or what wrote the Output; do not guess, and do not let style
alone sway a score.

Score the Output against each Criterion of the Rubric on a whole-number scale of
1 to 5, using the Gold Answer as the reference for what a strong Output contains:

- 5: as good as the Gold Answer on this Criterion, or better.
- 4: minor gaps a reader would barely notice.
- 3: usable, but with a clear gap the reader must fix.
- 2: a serious gap; most of the Criterion is unmet.
- 1: the Criterion is not met at all.

Score each Criterion on its own; a weakness on one must not lower the others.

Invented facts are a Correctness failure. Any figure, metric, baseline, target,
system, date or name that the Input does not state, and that the Output presents
as fact, scores Correctness 2 or lower, however plausible it sounds. Saying a
figure is missing, or proposing how to set one, is not an invention.

For each Criterion give a reason in one line that names the specific evidence in
the Output, for example the invented number or the missing section.
