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

## Public candle backtests

The optional market-data layer is independent of the Scarlett API. The first connector uses
Hyperliquid's public `candleSnapshot` endpoint and stores source, interval, retrieval time,
requested range, and provider limits with every bundle. Provider candles are market evidence,
not Scarlett forecast inputs or proof of executable fills.

The rule backtester calculates signals at the completed bar close and enters at the following
bar open. Positions do not overlap within a symbol, and each round trip pays the configured
cost. Candidate selection uses the first 60% of bars, validation uses the next 20%, and the
last 20% is opened only for a selected validation champion. At least ten confirmation trades
are required before the result can become a forward candidate. Repeating parameter searches
on the same final segment consumes it; it does not create independent confirmation.

The built-in rule family is intentionally small: SMA and EMA crosses, RSI reversion, and
close-price breakouts. It is a mechanism for falsifying simple ideas, not evidence that the
search has covered every strategy or execution condition.
