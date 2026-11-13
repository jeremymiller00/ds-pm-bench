# Tasks, runs and config are plain files in git, not a database

Tasks (Markdown with YAML frontmatter), Runs, Outputs, config and the Decision Log are files committed to one repo. The suite is edited by hand and expanded over time, and a Task changing is itself a decision worth seeing in a diff; a database would hide that. Runs record the Task version they used so old results stay interpretable. We accept slower queries across many Runs in exchange for transparency and zero infrastructure.
