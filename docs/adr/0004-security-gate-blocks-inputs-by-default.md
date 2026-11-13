# A failed or missing Gate blocks inputs from leaving the repo

Inputs and Gold Answers are sanitized real work and live in a private repo. The harness refuses to send an input to any Candidate without a passing Gate record, and in manual-assisted mode it refuses to display the input at all, so the check cannot be bypassed by copy-pasting. API keys are never stored; config holds only environment variable names. This is stricter than a reminder in a checklist, deliberately, because the failure it prevents (private work reaching an unapproved tool) cannot be undone.
