"""The 12 catalog Tasks (T1.1-T4.3), scaffolded as Private Tasks (ADR-0006).

Only generic catalog data lives here: Track, task name, expected output, Trap and
the task-specific Criteria. Inputs, Gold Answers, Manual Time and Canonical Prompts
start as placeholders and are filled in the private root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CatalogTask:
    id: str
    track: str
    name: str
    input: str
    expected_output: str
    trap: str
    criteria: tuple[dict[str, str], ...]  # the task-specific Criteria, one or two

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
            criteria=self.criteria,
        )


def _criterion(id: str, name: str, description: str) -> dict[str, str]:
    return {"id": id, "name": name, "description": description}


CATALOG: tuple[CatalogTask, ...] = (
    CatalogTask(
        "T1.1",
        "Framing",
        "Frame a vague product ask",
        "A short, vague product request from a stakeholder.",
        "AI Project Template: input/output, data, dependencies, success criteria.",
        "The ask states no success metric; the Output must flag it, not invent one.",
        (
            _criterion(
                "flags_missing_metric",
                "Flags the missing metric",
                "Names the absent success metric as an open question instead of inventing one.",
            ),
            _criterion(
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
            _criterion(
                "four_lenses_assessed",
                "Four lenses assessed",
                "Assesses value, viability, feasibility and usability, each with evidence.",
            ),
            _criterion(
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
            _criterion(
                "data_framing",
                "Data framing",
                "Defines the target variable, unit of prediction and baseline.",
            ),
            _criterion(
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
            _criterion(
                "testable_acceptance_criteria",
                "Testable acceptance criteria",
                "Acceptance criteria are structured, specific and testable.",
            ),
            _criterion(
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
            _criterion(
                "ticket_ready",
                "Tickets ready to pick up",
                "Each ticket has a clear scope, acceptance criteria and dependencies.",
            ),
            _criterion(
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
            _criterion(
                "gap_list",
                "Gap list",
                "Lists the ticket's real gaps, starting with the ambiguous success criteria.",
            ),
            _criterion(
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
            _criterion(
                "value_vs_likelihood",
                "Value vs. likelihood of success",
                "Value vs. likelihood of success is explicit for every item.",
            ),
            _criterion(
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
            _criterion(
                "entry_complete",
                "Entry complete",
                "Records decision, options, rationale, owner and date.",
            ),
            _criterion(
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
            _criterion(
                "explains_moves",
                "Explains what moved",
                "Says what moved in the ranking and why, tied to the change.",
            ),
            _criterion(
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
            _criterion(
                "pr_faq_complete",
                "PR FAQ complete",
                "Covers product, intended customer, problem, benefits and quote.",
            ),
            _criterion(
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
            _criterion(
                "evidence_backed_themes",
                "Evidence-backed themes",
                "Jobs to be done, struggle points and themes each cite interview evidence.",
            ),
            _criterion(
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
            _criterion(
                "goal_and_decision",
                "Goal and one decision",
                "States the goal and builds the numbered agenda toward one decision.",
            ),
            _criterion(
                "fits_the_timebox",
                "Fits the timebox",
                "Cuts or defers topics so the agenda fits in 30 minutes.",
            ),
        ),
    ),
)
