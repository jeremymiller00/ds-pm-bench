# Judges must be certified against human scores before a Run may use them

An LLM judge scores every Output, but a Judge Setup is usable only after it agrees with a human-scored Calibration Set (at least 90% of scores within 1 point and weighted kappa of at least 0.6, both configurable). Changing the judge model or prompt invalidates certification automatically, and overriding the rule is allowed but logged. The Judge is never the Candidate under test, which rules out Claude as the sole judge because Claude is the Baseline in every Comparison. A single non-Claude judge or a panel (median score, disagreement flag forcing a human check) are both supported, and a Candidate from the judge's own family is flagged for self-preference bias with a mandatory full human check.

## Considered Options

A fixed judge with only a 25% spot-check was rejected: it detects drift but never proves the judge was trustworthy to begin with.
