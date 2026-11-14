"""The 12 catalog Tasks (T1.1-T4.3), scaffolded as Private Tasks (ADR-0006).

Only generic catalog data lives here: Track, task name, expected output, Trap and
the task-specific Criteria. Inputs, Gold Answers, Manual Time and Canonical Prompts
start as placeholders and are filled in the private root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bench.core.rubric import Criterion, criterion_dict


@dataclass(frozen=True)
class CatalogTask:
    id: str
    track: str
    name: str
    input: str
    expected_output: str
    trap: str
    criteria: tuple[Criterion, ...]  # the task-specific Criteria, one or two

    @property
    def canonical_prompt(self) -> str:
        """A TODO placeholder, so the Task cannot be Run until the prompt is drafted."""
        return (
            "TODO: draft the Canonical Prompt.\n\n"
            f"Task: {self.name}\n"
            f"Input: {self.input}\n"
            f"Expected output: {self.expected_output}\n"
        )

    def create_args(self) -> dict[str, Any]:
        return dict(
            track=self.track,
            trap=self.trap,
            canonical_prompt=self.canonical_prompt,
            criteria=[criterion_dict(c) for c in self.criteria],
        )


CATALOG: tuple[CatalogTask, ...] = (
    CatalogTask(
        "T1.1",
        "Framing",
        "Frame a vague product ask",
        "A short, vague product request from a stakeholder.",
        "AI Project Template: input/output, data, dependencies, success criteria.",
        "The ask states no success metric; the Output must flag it, not invent one.",
        (
            Criterion(
                "flags_missing_metric",
                "Flags the missing metric",
                "Names the absent success metric as an open question instead of inventing one.",
            ),
            Criterion(
                "template_complete",
                "Template complete",
                "Covers input/output, data, dependencies and success criteria.",
            ),
        ),
    ),
    CatalogTask(
        "T1.2",
        "Framing",
        "Assess value, viability, feasibility, usability",
        "A proposed AI/ML opportunity with its context.",
        "A four-lens assessment (value, viability, feasibility, usability) with go/no-go.",
        "Feasibility is blocked by missing labeled data.",
        (
            Criterion(
                "four_lenses_assessed",
                "Four lenses assessed",
                "Assesses value, viability, feasibility and usability, each with evidence.",
            ),
            Criterion(
                "catches_feasibility_blocker",
                "Catches the feasibility blocker",
                "Spots the missing labeled data and lets it drive the go/no-go call.",
            ),
        ),
    ),
    CatalogTask(
        "T1.3",
        "Framing",
        "Reframe business problem as data problem",
        "A business problem statement.",
        "Target variable, unit of prediction and baseline.",
        "The problem is better solved without ML.",
        (
            Criterion(
                "data_framing",
                "Data framing",
                "Defines the target variable, unit of prediction and baseline.",
            ),
            Criterion(
                "questions_need_for_ml",
                "Questions the need for ML",
                "Recognizes when a simpler non-ML solution fits better and says so.",
            ),
        ),
    ),
    CatalogTask(
        "T2.1",
        "Specs",
        "Write PRD Part A",
        "Notes and requirements for a feature.",
        "Product intent plus structured acceptance criteria.",
        "The input holds conflicting constraints.",
        (
            Criterion(
                "testable_acceptance_criteria",
                "Testable acceptance criteria",
                "Acceptance criteria are structured, specific and testable.",
            ),
            Criterion(
                "surfaces_conflicts",
                "Surfaces conflicting constraints",
                "Names the conflicting constraints and asks for a decision rather than "
                "silently picking one.",
            ),
        ),
    ),
    CatalogTask(
        "T2.2",
        "Specs",
        "Break PRD into tickets",
        "A PRD.",
        "Jira-ready tickets with acceptance criteria and dependencies.",
        "A hidden dependency on another team.",
        (
            Criterion(
                "ticket_ready",
                "Tickets ready to pick up",
                "Each ticket has a clear scope, acceptance criteria and dependencies.",
            ),
            Criterion(
                "finds_hidden_dependency",
                "Finds the hidden dependency",
                "Identifies the dependency on another team and makes it explicit.",
            ),
        ),
    ),
    CatalogTask(
        "T2.3",
        "Specs",
        "Review a weak ticket",
        "A weakly written ticket.",
        "A gap list plus a rewritten ticket.",
        "The success criteria are ambiguous.",
        (
            Criterion(
                "gap_list",
                "Gap list",
                "Lists the ticket's real gaps, starting with the ambiguous success criteria.",
            ),
            Criterion(
                "rewrite_resolves_gaps",
                "Rewrite resolves the gaps",
                "The rewritten ticket has unambiguous, measurable success criteria.",
            ),
        ),
    ),
    CatalogTask(
        "T3.1",
        "Prioritize",
        "Score and rank a backlog",
        "A backlog of candidate items.",
        "A ranked list scored on value vs. likelihood of success.",
        "One item looks high-value but has no owner.",
        (
            Criterion(
                "value_vs_likelihood",
                "Value vs. likelihood of success",
                "Value vs. likelihood of success is explicit for every item.",
            ),
            Criterion(
                "flags_unowned_item",
                "Flags the unowned item",
                "Calls out the high-value item with no owner instead of ranking it blindly.",
            ),
        ),
    ),
    CatalogTask(
        "T3.2",
        "Prioritize",
        "Write a decision log entry",
        "A discussion thread about a decision.",
        "A decision log entry: decision, options, rationale, owner, date.",
        "The thread ends without a clear decision.",
        (
            Criterion(
                "entry_complete",
                "Entry complete",
                "Records decision, options, rationale, owner and date.",
            ),
            Criterion(
                "no_invented_decision",
                "No invented decision",
                "States that no clear decision was reached rather than inventing one.",
            ),
        ),
    ),
    CatalogTask(
        "T3.3",
        "Prioritize",
        "Re-prioritize after a change",
        "A ranked list plus a change in context.",
        "An updated ranking plus what moved and why.",
        "The change should demote a pet project.",
        (
            Criterion(
                "explains_moves",
                "Explains what moved",
                "Says what moved in the ranking and why, tied to the change.",
            ),
            Criterion(
                "demotes_on_evidence",
                "Demotes on evidence",
                "Demotes the pet project when the change calls for it.",
            ),
        ),
    ),
    CatalogTask(
        "T4.1",
        "Story",
        "Write a PR FAQ",
        "A product concept.",
        "A PR FAQ: product, customer, problem, benefits, quote.",
        "No real exec quote is supplied; the Output must not fabricate one.",
        (
            Criterion(
                "pr_faq_complete",
                "PR FAQ complete",
                "Covers product, intended customer, problem, benefits and quote.",
            ),
            Criterion(
                "no_fabricated_quote",
                "No fabricated quote",
                "Leaves a marked placeholder for the quote instead of fabricating one.",
            ),
        ),
    ),
    CatalogTask(
        "T4.2",
        "Story",
        "Synthesize stakeholder interviews",
        "Several stakeholder interview notes.",
        "Jobs to be done, struggle points and themes with evidence.",
        "A minority view that matters.",
        (
            Criterion(
                "evidence_backed_themes",
                "Evidence-backed themes",
                "Jobs to be done, struggle points and themes each cite interview evidence.",
            ),
            Criterion(
                "keeps_minority_view",
                "Keeps the minority view",
                "Surfaces the minority view that matters instead of averaging it away.",
            ),
        ),
    ),
    CatalogTask(
        "T4.3",
        "Story",
        "Build a decision meeting agenda",
        "A decision to be made and a list of topics.",
        "A goal plus a numbered agenda toward one decision.",
        "Too many topics for 30 minutes.",
        (
            Criterion(
                "goal_and_decision",
                "Goal and one decision",
                "States the goal and builds the numbered agenda toward one decision.",
            ),
            Criterion(
                "fits_the_timebox",
                "Fits the timebox",
                "Cuts or defers topics so the agenda fits in 30 minutes.",
            ),
        ),
    ),
)
