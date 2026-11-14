"""The closed vocabularies the domain switches on, as string enums (see GLOSSARY.md).

Members compare equal to their YAML spelling, so config and run files stay plain text.
Convert with str() before writing them to YAML.
"""

from __future__ import annotations

from enum import StrEnum


class AdapterKind(StrEnum):
    """How the harness reaches a Candidate: automated (API or CLI), or manual-assisted,
    where a person pastes the Output back."""

    AUTOMATED = "automated"
    MANUAL_ASSISTED = "manual_assisted"


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


class JudgeMode(StrEnum):
    """A Judge Setup's shape: one judge model, or a Panel of two or more."""

    SINGLE = "single"
    PANEL = "panel"


class Provider(StrEnum):
    """A model provider: who serves an automated Candidate or a judge model."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"


class EntryKind(StrEnum):
    """What a Decision Log entry records."""

    VERDICT = "verdict"
    JUDGE_CERTIFICATION = "judge_certification"
    UNCERTIFIED_JUDGE_OVERRIDE = "uncertified_judge_override"
