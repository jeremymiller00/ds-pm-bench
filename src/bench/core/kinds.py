"""The closed vocabularies the domain switches on, as string enums (see GLOSSARY.md).

Members compare equal to their YAML spelling, so config and run files stay plain text.
Convert with str() before writing them to YAML.
"""

from __future__ import annotations

from enum import StrEnum


class Clearance(StrEnum):
    """What a Candidate's passing Gate lets it see (ADR-0006)."""

    PUBLIC_ONLY = "public_only"
    PUBLIC_AND_PRIVATE = "public_and_private"


class Suite(StrEnum):
    """Which Tasks a Run covers. Never more than the Candidate's Clearance allows."""

    PUBLIC_ONLY = "public_only"
    PUBLIC_AND_PRIVATE = "public_and_private"


class Visibility(StrEnum):
    """Whether a Task lives in the public repo or a private root (ADR-0006)."""

    PUBLIC = "public"
    PRIVATE = "private"
