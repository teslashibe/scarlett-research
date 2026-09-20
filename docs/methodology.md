# Methodology

The search loop deliberately makes a modest claim. It can identify a retrospective
candidate worth freezing for forward paper evaluation. It cannot prove future profitability.

Each run records a protocol hash, splits observations chronologically, scores all generated
candidates on development data, and opens validation results only for the five eligible
development finalists. Promotion requires minimum sample size, positive after-cost mean,
and bounded single-symbol concentration. Every trial remains in `results.json`.

The current evaluator uses saved outcome R and subtracts a configurable cost in R. That is
appropriate for ranking saved decisions but is not a funded account simulation. It does not
model fill probability, overlapping capital, funding, or exchange liquidity. Those require
setup replays and a separate portfolio executor before any stronger claim.

Repeated runs on the same validation window do not create new evidence. After choosing a
candidate, freeze the definition and collect a new forward window. If the definition changes,
the previous confirmation evidence cannot be reused as untouched confirmation.

