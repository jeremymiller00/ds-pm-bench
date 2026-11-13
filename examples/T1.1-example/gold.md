# Framing: weekly churn-risk call list for Lumen Bikes

## Decision
Each week, which subscribers the four account managers call. The Head of Customer Success owns the decision; account managers act on it.

## Prediction or analysis
For each active subscriber, the probability of cancelling in the next 30 days, refreshed weekly. The output is a ranked list of about 100 subscribers (4 account managers x 25 calls), not a score for its own sake.

## Data
- Available: two years of subscription records (start, plan, city, cancel date), ride logs (trips per day, distance) and support tickets tagged by topic.
- Gaps: no cancellation reason, so the model can predict who leaves but not why; call outcomes are not recorded, so the effect of a call cannot yet be measured.

## Success criteria
- The input gives no current churn rate or target, so none is assumed. First step: compute the 30-day cancellation rate per city from the subscription records to set a baseline.
- Model check: of the ~100 subscribers listed each week, the share who would have cancelled, compared with a random list of the same size.
- Business check: cancellation rate among called vs. comparable uncalled at-risk subscribers, once call outcomes are logged.

## Open questions
- What counts as churn: cancellation only, or also downgrades and paused plans?
- Is there a retention offer the account managers can make, or only a conversation?
- Can call outcomes and a cancellation reason be added to start measuring impact?
- Should the list balance across the three cities or rank all subscribers together?
